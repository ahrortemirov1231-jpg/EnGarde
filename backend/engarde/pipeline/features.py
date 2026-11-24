"""S4: orientation normalisation (PRD §6.1), camera motion (§6.2), FenceNet-style normalisation (§6.3)
and per-frame kinematic features (§6.5). Image coordinates only — no metres unless a piste
calibration exists (§6.4), which this MVP does not estimate automatically."""
from __future__ import annotations

import cv2
import numpy as np
import pandas as pd

from .perception import CORE9

NOSE, LSH, RSH, LEL, REL, LWR, RWR, LHIP, RHIP, LKN, RKN, LANK, RANK = 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16
SG5 = np.array([-3, 12, 17, 12, -3], dtype=float) / 35.0  # Savitzky-Golay window 5, order 2


def smooth(x: np.ndarray) -> np.ndarray:
    """SG(5) on a 1-D series with NaNs kept as NaN (features only; raw values are stored separately)."""
    out = np.full_like(x, np.nan, dtype=float)
    n = len(x)
    for i in range(n):
        if np.isnan(x[i]):
            continue
        if 2 <= i < n - 2 and not np.isnan(x[i - 2:i + 3]).any():
            out[i] = float(np.dot(SG5, x[i - 2:i + 3]))
        else:
            out[i] = x[i]
    return out


def deriv(x: np.ndarray, t: np.ndarray, half: int = 2, min_span_s: float = 0.06) -> np.ndarray:
    """Slope of a local least-squares line over +-`half` samples using TRUE timestamps (widened until the
    span covers >= min_span_s). Robust to the irregular frame timing of screen recordings, where a plain
    central difference over a 10 ms gap turns keypoint noise into huge speed spikes."""
    n = len(x)
    d = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(x[i]):
            continue
        h = half
        while True:
            a, b = max(0, i - h), min(n, i + h + 1)
            if t[b - 1] - t[a] >= min_span_s or (a == 0 and b == n) or h > 8:
                break
            h += 1
        tt, xx = t[a:b], x[a:b]
        ok = ~np.isnan(xx)
        if ok.sum() >= 3 and np.ptp(tt[ok]) > 0:
            tc = tt[ok] - tt[ok].mean()
            d[i] = float(np.dot(tc, xx[ok] - xx[ok].mean()) / np.dot(tc, tc))
    return d


def angle(a, b, c):
    """Angle at b (degrees) for arrays [T,2]."""
    v1, v2 = a - b, c - b
    n = np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)
    cos = np.sum(v1 * v2, axis=1) / np.where(n == 0, np.nan, n)
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


SCENE_BAND = (0.12, 0.82)   # broadcast overlays (logo, score bar) live outside this band


def camera_motion(frame_iter, boxes_by_frame: dict, report=None, n=None, scene_band=SCENE_BAND) -> pd.DataFrame:
    """ORB + RANSAC partial affine between consecutive frames on the background. Fencer boxes AND
    broadcast-overlay bands are masked: static overlays otherwise dominate RANSAC and report a
    confident zero motion while the camera pans. Also reports zoom (affine scale) and shot cuts
    (histogram change, inlier collapse or a jump in mean absolute frame difference)."""
    orb = cv2.ORB_create(1000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    prev = None
    rows = []
    for i, (f, img) in enumerate(frame_iter):
        # work at <= 960 px wide (speed); displacements are scaled back to original pixels
        k = min(1.0, 960.0 / img.shape[1])
        small_img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else img
        gray = cv2.cvtColor(small_img, cv2.COLOR_BGR2GRAY)
        mask = np.zeros(gray.shape, np.uint8)
        H_ = gray.shape[0]
        mask[int(scene_band[0] * H_):int(scene_band[1] * H_)] = 255
        for (x1, y1, x2, y2) in boxes_by_frame.get(f, []):
            if np.isfinite([x1, y1, x2, y2]).all():
                pad = 0.1 * (y2 - y1)
                cv2.rectangle(mask, (int((x1 - pad) * k), int((y1 - pad) * k)), (int((x2 + pad) * k), int((y2 + pad) * k)), 0, -1)
        kp, des = orb.detectAndCompute(gray, mask)
        hsv = cv2.cvtColor(small_img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA).astype(np.int16)
        row = {"frame": f, "cam_dx": 0.0, "cam_dy": 0.0, "cam_scale": 1.0, "cam_inliers": 0, "cam_ok": False,
               "hist_corr": 1.0, "mad": 0.0}
        if prev is not None:
            row["hist_corr"] = float(cv2.compareHist(prev["hist"], hist, cv2.HISTCMP_CORREL))
            row["mad"] = float(np.abs(small - prev["small"]).mean())
            if des is not None and prev["des"] is not None and len(kp) >= 8 and len(prev["kp"]) >= 8:
                m = bf.match(prev["des"], des)
                if len(m) >= 8:
                    src = np.float32([prev["kp"][x.queryIdx].pt for x in m])
                    dst = np.float32([kp[x.trainIdx].pt for x in m])
                    A, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                    if A is not None and inl is not None and inl.sum() >= 8:
                        disp = (dst - src)[inl.ravel() == 1]
                        row.update(cam_dx=float(np.median(disp[:, 0])) / k, cam_dy=float(np.median(disp[:, 1])) / k,
                                   cam_scale=float(np.hypot(A[0, 0], A[1, 0])), cam_inliers=int(inl.sum()), cam_ok=True)
        rows.append(row)
        prev = {"kp": kp, "des": des, "hist": hist, "small": small}
        if report and n and i % 10 == 0:
            report(i / n)
    df = pd.DataFrame(rows)
    df["camera_motion_flag"] = (np.hypot(df.cam_dx, df.cam_dy) > 2.0) | (np.abs(df.cam_scale - 1) > 0.01)  # PRD §6.2
    med_mad = float(df.mad.iloc[1:].median()) if len(df) > 2 else 0.0
    inl_med = float(df.cam_inliers.iloc[1:].median()) if len(df) > 2 else 0.0
    df["camera_cut"] = (df.hist_corr < 0.5) | (df.mad > max(12.0, 6 * med_mad)) | \
        ((df.index > 0) & (df.cam_inliers < 0.15 * inl_med)) | (np.abs(df.cam_scale - 1) > 0.08)
    df["camera_estimate_ok"] = df.cam_ok
    df["cam_x_cum"] = df.cam_dx.cumsum()   # chained translation back to window frame 0
    return df


def _side_features(xy: np.ndarray, mask: np.ndarray, conf: np.ndarray, side: str, W: int, t: np.ndarray,
                   bbox_h: np.ndarray, cam_x: np.ndarray) -> tuple[pd.DataFrame, dict]:
    T = xy.shape[0]
    p = xy.astype(float).copy()
    if side == "right":
        p[..., 0] = W - p[..., 0]   # mirror: both fencers "attack to the right"
        cam = -cam_x
    else:
        cam = cam_x
    # front/back legs after mirroring, PER FRAME (PRD §6.1): the ankle with larger x is front. This is
    # robust to the pose model swapping anatomical left/right labels in side views; knees and hips
    # follow their ankle. (Sabre footwork never crosses the feet.)
    la, ra = p[:, LANK], p[:, RANK]
    l_front = np.where(np.isnan(la[:, 0]) | np.isnan(ra[:, 0]), True, la[:, 0] >= ra[:, 0])

    def pick(lj, rj, front: bool):
        a, b = p[:, lj], p[:, rj]
        m = l_front if front else ~l_front
        return np.where(m[:, None], a, b)

    f_ank, b_ank = pick(LANK, RANK, True), pick(LANK, RANK, False)
    f_kn, b_kn = pick(LKN, RKN, True), pick(LKN, RKN, False)
    f_hip, b_hip = pick(LHIP, RHIP, True), pick(LHIP, RHIP, False)
    fl = bool(np.mean(l_front) >= 0.5)
    # weapon arm: wrist further forward on average
    lw = np.nanmean(p[:, LWR, 0]) if not np.all(np.isnan(p[:, LWR, 0])) else -np.inf
    rw = np.nanmean(p[:, RWR, 0]) if not np.all(np.isnan(p[:, RWR, 0])) else -np.inf
    F_SH, F_EL, F_WR = (LSH, LEL, LWR) if lw >= rw else (RSH, REL, RWR)

    hip = (p[:, LHIP] + p[:, RHIP]) / 2
    sh = (p[:, LSH] + p[:, RSH]) / 2
    # scale: |y_nose0 - y_frontankle0| at the first frame where both exist (FenceNet), else bbox height
    scale, scale_src = np.nan, "none"
    for i in range(T):
        if not np.isnan(p[i, NOSE, 1]) and not np.isnan(f_ank[i, 1]):
            scale = abs(p[i, NOSE, 1] - f_ank[i, 1])
            scale_src = f"nose-front_ankle@frame_offset_{i}"
            break
    if not np.isfinite(scale) or scale < 10:
        hh = bbox_h[np.isfinite(bbox_h)]
        if len(hh):
            scale, scale_src = float(np.median(hh)) * 0.9, "0.9*median_bbox_height"
    ref_x = np.nan
    for i in range(T):
        if not np.isnan(p[i, NOSE, 0]):
            ref_x = p[i, NOSE, 0]
            break
    if np.isnan(ref_x):
        ref_x = np.nanmedian(hip[:, 0]) if not np.all(np.isnan(hip[:, 0])) else 0.0

    def nx(v):
        return (v - ref_x) / scale if np.isfinite(scale) else np.full_like(v, np.nan)

    hip_x = smooth(nx(hip[:, 0]))
    hip_x_comp = smooth(nx(hip[:, 0] - cam))    # camera-compensated variant (kept side by side)
    f_ank_x = smooth(nx(f_ank[:, 0]))
    b_ank_x = smooth(nx(b_ank[:, 0]))
    core_missing = 1.0 - mask[:, CORE9].mean(axis=1)
    lower_missing = 1.0 - mask[:, [LHIP, RHIP, LKN, RKN, LANK, RANK]].mean(axis=1)  # footwork needs legs
    core_conf = np.nanmean(np.where(mask[:, CORE9] > 0, conf[:, CORE9], np.nan), axis=1)
    f_ank_y = f_ank[:, 1]
    lift = (np.nanmax(f_ank_y) - f_ank_y) / scale if np.isfinite(scale) and not np.all(np.isnan(f_ank_y)) else np.full(T, np.nan)
    torso = np.degrees(np.arctan2(sh[:, 0] - hip[:, 0], hip[:, 1] - sh[:, 1]))  # 0 = upright, + = leaning forward
    df = pd.DataFrame({
        "hip_x": hip_x, "hip_x_raw_px": hip[:, 0], "hip_vel": deriv(hip_x, t), "hip_acc": deriv(deriv(hip_x, t), t),
        "hip_x_comp": hip_x_comp, "hip_vel_comp": deriv(hip_x_comp, t),
        "front_ankle_x": f_ank_x, "back_ankle_x": b_ank_x,
        "front_ankle_vel": deriv(f_ank_x, t), "back_ankle_vel": deriv(b_ank_x, t),
        "foot_spread": f_ank_x - b_ank_x,
        "front_knee_angle": smooth(angle(f_hip, f_kn, f_ank)),
        "back_knee_angle": smooth(angle(b_hip, b_kn, b_ank)),
        "front_elbow_angle": smooth(angle(p[:, F_SH], p[:, F_EL], p[:, F_WR])),
        "wrist_shoulder_dx": smooth(nx(p[:, F_WR, 0]) - nx(p[:, F_SH, 0])),
        "torso_lean_deg": smooth(torso),
        "front_foot_lift": smooth(lift),
        "kp_core_conf": core_conf, "kp_core_missing": core_missing, "kp_lower_missing": lower_missing,
    })
    meta = {"scale_px": None if not np.isfinite(scale) else float(scale), "scale_source": scale_src,
            "front_leg": "per-frame (larger mirrored x); mostly " + ("left" if fl else "right") + " anatomical label",
            "weapon_arm": "left_anatomical" if F_WR == LWR else "right_anatomical", "mirrored": side == "right",
            "units": "body-scale units (nose-to-front-ankle height of the first frame); velocities per second"}
    return df, meta


def compute_features(pose: dict, tracks: pd.DataFrame, frames: list[int], t_s: list[float], width: int,
                     cam: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    t = np.asarray(t_s, dtype=float)
    cam = cam.set_index("frame").reindex(frames)
    cam_x = cam.cam_x_cum.fillna(0).values
    # screen-recorded broadcasts repeat frames: a repeated frame shows an EARLIER instant, so its
    # keypoints are dropped and re-interpolated in time from the neighbouring distinct frames.
    dup = np.zeros(len(frames), bool)
    if "mad" in cam.columns:
        dup[1:] = cam["mad"].fillna(99).values[1:] < 0.25
    cut = cam.camera_cut.fillna(False).values.astype(bool)
    after_cut = np.cumsum(cut) > 0
    per, meta = {}, {}
    for side in ("left", "right"):
        p = dict(pose[side])
        if dup.any():
            xy = p["xy"].astype(float).copy()
            xy[dup] = np.nan
            for j in range(xy.shape[1]):
                for c in range(2):
                    v = xy[:, j, c]
                    ok = ~np.isnan(v)
                    fill = dup & ~np.isnan(p["xy"][:, j, c])
                    if ok.sum() >= 2 and fill.any():
                        v[fill] = np.interp(t[fill], t[ok], v[ok])
            p["xy"] = xy
        g = tracks[tracks.side == side].set_index("frame").reindex(frames) if len(tracks) else None
        bbox_h = (g.y2 - g.y1).values if g is not None else np.full(len(frames), np.nan)
        df, m = _side_features(p["xy"], p["mask"], np.nan_to_num(p["raw"][..., 2]), side, width, t, bbox_h, cam_x)
        per[side], meta[side] = df, m
    # relative features (do not depend on camera panning): hip-midpoint gap in pixels / mean scale
    lhip, rhip = per["left"].hip_x_raw_px.values, per["right"].hip_x_raw_px.values   # right is mirrored raw
    rhip_img = width - rhip
    sc = np.nanmean([meta["left"]["scale_px"] or np.nan, meta["right"]["scale_px"] or np.nan])
    dist = smooth((rhip_img - lhip) / sc) if np.isfinite(sc) else np.full(len(frames), np.nan)
    closing = -deriv(dist, t)
    rows = []
    for side in ("left", "right"):
        df = per[side].copy()
        df.insert(0, "frame", frames)
        df.insert(1, "side", side)
        df["t_s"] = t
        df["fencer_distance"] = dist
        df["closing_rate"] = closing
        opp = per["right" if side == "left" else "left"]
        df["opp_hip_vel"] = opp.hip_vel.values
        df["camera_motion_flag"] = cam.camera_motion_flag.fillna(False).values
        df["camera_cut"] = cam.camera_cut.fillna(False).values
        g = tracks[tracks.side == side].set_index("frame").reindex(frames) if len(tracks) else None
        for col in ("present", "track_break", "id_switch_suspect", "identity_uncertain", "pair_implausible",
                    "screen_position"):
            df[col] = g[col].values if g is not None else (False if col != "screen_position" else "absent")
        df["duplicate_frame"] = dup
        # identities are not carried across a shot cut: everything after it is identity-uncertain
        df["identity_uncertain"] = df["identity_uncertain"].fillna(False).astype(bool).values | after_cut
        rows.append(df)
    out = pd.concat(rows, ignore_index=True)
    out["present"] = out["present"].fillna(False).astype(bool)
    meta["distance_units"] = "body-scale units (no piste calibration => no metres)"
    meta["camera_motion_frames"] = int(cam.camera_motion_flag.fillna(False).sum())
    meta["camera_cut_frames"] = [int(f) for f in cam.index[cam.camera_cut.fillna(False).values]]
    meta["duplicate_frames"] = int(dup.sum())
    meta["camera_estimate_failed_frames"] = int((~cam.cam_ok.fillna(False).astype(bool)).sum()) - 1
    return out, meta


FEATURE_COLUMNS = ["hip_x", "hip_vel", "hip_acc", "hip_vel_comp", "front_ankle_vel", "back_ankle_vel",
                   "foot_spread", "front_knee_angle", "back_knee_angle", "front_elbow_angle", "wrist_shoulder_dx",
                   "torso_lean_deg", "front_foot_lift", "fencer_distance", "closing_rate", "opp_hip_vel",
                   "kp_core_conf", "kp_core_missing", "kp_lower_missing"]
