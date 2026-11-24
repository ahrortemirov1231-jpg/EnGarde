"""S2 detection + tracking and S3 pose.

One framework only (PRD: "first version wires one pose option"): Ultralytics YOLO11n-pose, which
gives COCO-17 keypoints per person box, tracked with Ultralytics' ByteTrack implementation.
Alternative source: an imported skeleton file (e.g. Engarde's exported skeletons, or the synthetic
fixture's ground-truth skeleton) — always labelled with its provenance.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

COCO17 = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
          "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]
CORE9 = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]  # shoulders, elbows, wrists, hips, knees, ankles
KP_CONF_MIN = 0.3
MAX_INTERP_GAP = 3

WEIGHTS_DIR = os.environ.get("ENGARDE_WEIGHTS", os.path.join(os.path.dirname(__file__), "..", "..", "..", "weights"))
DEFAULT_WEIGHTS = "yolo11n-pose.pt"


class PoseBackendUnavailable(RuntimeError):
    pass


def bytetrack_yaml(path: str, fps: float) -> dict:
    """PRD §5.2 starting values. Ultralytics' match_thresh is a COST threshold on (1 - IoU) (after
    optional score fusion), so 0.8 == reject matches with IoU < 0.2, i.e. the PRD's IoU 0.2."""
    cfg = {"tracker_type": "bytetrack", "track_high_thresh": 0.6, "track_low_thresh": 0.1,
           "new_track_thresh": 0.6, "track_buffer": int(round(fps or 30)),  # ~1 s regardless of fps
           "match_thresh": 0.8, "fuse_score": True}
    with open(path, "w") as f:
        for k, v in cfg.items():
            f.write(f"{k}: {v}\n")
    return cfg


def weights_path(name=DEFAULT_WEIGHTS) -> str:
    return os.path.abspath(os.path.join(WEIGHTS_DIR, name))


def run_yolo_track(frame_iter, n_frames: int, fps: float, work_dir: str, report=None,
                   weights=DEFAULT_WEIGHTS, imgsz=960) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """Returns (detections df, keypoints array aligned to df rows [N,17,3], info)."""
    wp = weights_path(weights)
    if not os.path.exists(wp):
        raise PoseBackendUnavailable(f"pose weights not found at {wp} (see README: download yolo11n-pose.pt)")
    try:
        from ultralytics import YOLO
        import ultralytics
    except ImportError as e:
        raise PoseBackendUnavailable(f"ultralytics not installed: {e}") from e
    try:
        import lap  # noqa: F401 — required by Ultralytics' ByteTrack matcher
    except ImportError as e:
        raise PoseBackendUnavailable("package 'lap' missing (pip install lap): ByteTrack cannot run") from e
    tracker_cfg_path = os.path.join(work_dir, "bytetrack.yaml")
    cfg = bytetrack_yaml(tracker_cfg_path, fps)
    model = YOLO(wp)
    rows, kps = [], []
    for i, (frame_no, img) in enumerate(frame_iter):
        res = model.track(img, persist=True, tracker=tracker_cfg_path, conf=0.1, iou=0.7, imgsz=imgsz,
                          classes=[0], verbose=False)[0]
        b = res.boxes
        if b is not None and len(b):
            xyxy = b.xyxy.cpu().numpy()
            conf = b.conf.cpu().numpy()
            ids = b.id.cpu().numpy().astype(int) if b.id is not None else np.full(len(conf), -1)
            k = res.keypoints.data.cpu().numpy() if res.keypoints is not None else np.zeros((len(conf), 17, 3))
            for j in range(len(conf)):
                rows.append({"frame": frame_no, "track_id": int(ids[j]), "x1": float(xyxy[j, 0]),
                             "y1": float(xyxy[j, 1]), "x2": float(xyxy[j, 2]), "y2": float(xyxy[j, 3]),
                             "score": float(conf[j])})
                kps.append(k[j])
        if report and i % 5 == 0:
            report(i / max(1, n_frames))
    df = pd.DataFrame(rows, columns=["frame", "track_id", "x1", "y1", "x2", "y2", "score"])
    info = {"backend": "ultralytics-yolo-pose+bytetrack", "weights": os.path.basename(wp),
            "ultralytics_version": ultralytics.__version__, "imgsz": imgsz, "det_conf_min": 0.1,
            "tracker": cfg, "keypoint_format": "COCO-17 (no toe/heel points)"}
    return df, (np.stack(kps) if kps else np.zeros((0, 17, 3))), info


# --------------------------------------------------------------------------- fencer selection
def _inside(poly, x, y):
    if poly is None:
        return True
    from matplotlib.path import Path  # light, already a dependency of ultralytics
    return bool(Path(np.asarray(poly)).contains_point((x, y)))


SELECT_VERSION = 2
MIN_WHITE = 0.25          # fencing whites; officials/audience in dark clothes score ~0.0-0.23 on our footage
MIN_PAIR_OVERLAP = 0.30   # both fencers visible together for >= 30% of the window


def whiteness(img, x1, y1, x2, y2) -> float:
    """Fraction of bright, low-saturation pixels in the body part of a box (fencing whites)."""
    import cv2
    w, h = x2 - x1, y2 - y1
    c = img[max(0, int(y1 + .12 * h)):max(0, int(y1 + .85 * h)), max(0, int(x1 + .2 * w)):max(0, int(x1 + .8 * w))]
    if c.size == 0:
        return float("nan")
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 2] > 150) & (hsv[..., 1] < 70)).mean())


def track_appearance(det: pd.DataFrame, frames_dir: str, per_track: int = 6) -> dict:
    import cv2
    out, cache = {}, {}
    for tid, g in det[det.track_id >= 0].groupby("track_id"):
        vals = []
        for r in g.iloc[::max(1, len(g) // per_track)].itertuples():
            f = int(r.frame)
            if f not in cache:
                p = os.path.join(frames_dir, f"{f:06d}.jpg")
                cache[f] = cv2.imread(p) if os.path.exists(p) else None
            if cache[f] is not None:
                vals.append(whiteness(cache[f], r.x1, r.y1, r.x2, r.y2))
        out[int(tid)] = float(np.nanmean(vals)) if vals and not np.all(np.isnan(vals)) else float("nan")
    return out


def _merge_fragments(d: pd.DataFrame, merge_gap: int, info: dict) -> tuple[dict, dict]:
    groups = {tid: g.sort_values("frame") for tid, g in d.groupby("track_id")}
    parent = {tid: tid for tid in groups}
    changed = True
    while changed:
        changed = False
        ids = sorted(groups, key=lambda t: groups[t].frame.min())
        for a in ids:
            for b in ids:
                if a == b or a not in groups or b not in groups:
                    continue
                ga, gb = groups[a], groups[b]
                gap = gb.frame.min() - ga.frame.max()
                if 0 < gap <= merge_gap:
                    la, fb = ga.iloc[-1], gb.iloc[0]
                    if abs(la.cx - fb.cx) < 1.0 * max(la.h, fb.h) and abs(la.y2 - fb.y2) < 0.5 * max(la.h, fb.h):
                        groups[a] = pd.concat([ga, gb])
                        del groups[b]
                        for k, v in parent.items():
                            if v == b:
                                parent[k] = a
                        parent[b] = a
                        info["merges"].append({"into": int(a), "merged": int(b), "gap_frames": int(gap)})
                        changed = True
                        break
            if changed:
                break
    return groups, parent


def select_fencers(det: pd.DataFrame, frames: list[int], piste_polygon=None, merge_gap=15, appearance=None,
                   forced: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Choose the two fencers (PRD §5.2 + our identity rules):
      1. optional piste polygon; offline merge of fragments (<= 15 frames, positional continuity)
      2. eligible = whiteness >= MIN_WHITE (fencing whites), when appearance is available
      3. choose the most plausible PAIR: similar height (ratio >= 0.6), feet on one line
         (|dy| <= 0.3 h), together for >= 30% of the window, horizontally separated, and not much smaller
         than the largest white person in view (>= 0.6 x); score = coverage x size
      4. left = smaller median x
      5. `forced` = {"left": track_id, "right": track_id} from a reviewer always wins
    If no plausible pair exists nothing is guessed: status 'no_plausible_pair' and every frame is
    identity_uncertain (-> no_call)."""
    info = {"piste_filter": piste_polygon is not None, "merges": [], "candidates": [], "select_version": SELECT_VERSION,
            "rules": {"min_white": MIN_WHITE, "min_pair_overlap": MIN_PAIR_OVERLAP}}
    if det.empty or (det["track_id"] < 0).all():
        return pd.DataFrame(), {**info, "status": "no_tracks"}
    d = det[det["track_id"] >= 0].copy()
    d["cx"] = (d.x1 + d.x2) / 2
    d["h"] = d.y2 - d.y1
    d["in_piste"] = [_inside(piste_polygon, cx, y2) for cx, y2 in zip(d.cx, d.y2)]
    groups, parent = _merge_fragments(d, merge_gap, info)
    T = len(frames)
    stats = {}
    for tid, g in groups.items():
        members = [k for k, v in parent.items() if v == tid]
        wv = [appearance.get(int(m)) for m in members] if appearance else []
        wv = [x for x in wv if x is not None and not np.isnan(x)]
        stats[tid] = {"frames": set(int(f) for f in g.frame[g.in_piste]), "h": float(g.h.median()),
                      "cx": float(g.cx.median()), "y2": float(g.y2.median()),
                      "white": float(np.mean(wv)) if wv else None, "members": [int(m) for m in members]}
    for tid, st in sorted(stats.items(), key=lambda kv: -len(kv[1]["frames"]) * kv[1]["h"]):
        info["candidates"].append({"track_id": int(tid), "members": st["members"], "frames": len(st["frames"]),
                                   "height_px": round(st["h"]), "median_x": round(st["cx"]),
                                   "whiteness": None if st["white"] is None else round(st["white"], 3),
                                   "eligible": st["white"] is None or st["white"] >= MIN_WHITE})
    chosen = None
    if forced and forced.get("left") is not None and forced.get("right") is not None:
        fl, fr = parent.get(int(forced["left"])), parent.get(int(forced["right"]))
        if fl is None or fr is None or fl == fr:
            raise ValueError(f"forced tracks {forced} not found among candidates or identical")
        chosen = (fl, fr)
        info["status"] = "reviewer_assigned"
    else:
        elig = [t for t, st in stats.items() if (st["white"] is None or st["white"] >= MIN_WHITE) and st["frames"]]
        best, best_score = None, 0.0
        # the fencers are the main subjects: a pair much smaller than the largest white person in view is
        # background (warming-up fencers, far strips), not the bout
        h_main = max((stats[t]["h"] for t in elig if len(stats[t]["frames"]) >= MIN_PAIR_OVERLAP * T), default=0)
        for i, a in enumerate(elig):
            for b in elig[i + 1:]:
                A, B = stats[a], stats[b]
                both = len(A["frames"] & B["frames"])
                hmax = max(A["h"], B["h"])
                if both < MIN_PAIR_OVERLAP * T or min(A["h"], B["h"]) / hmax < 0.6 or abs(A["y2"] - B["y2"]) > 0.3 * hmax \
                        or abs(A["cx"] - B["cx"]) < 0.3 * hmax or hmax < 0.6 * h_main:
                    continue
                sc = len(A["frames"]) * A["h"] + len(B["frames"]) * B["h"]
                if sc > best_score:
                    best, best_score = (a, b), sc
        if best:
            chosen = tuple(sorted(best, key=lambda t: stats[t]["cx"]))
            info["status"] = "two_fencers"
        else:
            # nothing plausible: keep the two largest eligible (or any) tracks for display, but mark uncertain
            pool = elig or list(stats)
            pool = sorted(pool, key=lambda t: -len(stats[t]["frames"]) * stats[t]["h"])[:2]
            chosen = tuple(sorted(pool, key=lambda t: stats[t]["cx"]))
            info["status"] = "no_plausible_pair" if len(pool) == 2 else "one_fencer_only"
    if forced:
        sides = {"left": chosen[0], "right": chosen[1]}
    else:
        sides = {"left": chosen[0], "right": chosen[1] if len(chosen) > 1 else None}
    info["assignment"] = {k: (int(v) if v is not None else None) for k, v in sides.items()}
    out = []
    for side, tid in sides.items():
        g = groups.get(tid) if tid is not None else None
        byframe = {} if g is None else {int(r.frame): r for r in g.itertuples()}
        for f in frames:
            r = byframe.get(f)
            out.append({"frame": f, "side": side, "present": r is not None,
                        "track_id": int(r.track_id) if r is not None else -1,
                        "x1": r.x1 if r is not None else np.nan, "y1": r.y1 if r is not None else np.nan,
                        "x2": r.x2 if r is not None else np.nan, "y2": r.y2 if r is not None else np.nan,
                        "score": r.score if r is not None else np.nan,
                        "det_row": int(r.Index) if r is not None else -1})
    tr = add_track_flags(pd.DataFrame(out))
    if info["status"] in ("no_plausible_pair", "one_fencer_only"):
        tr["identity_uncertain"] = True
        tr["selection_unverified"] = True
    return tr, info


def add_track_flags(tr: pd.DataFrame) -> pd.DataFrame:
    """(Re)compute all per-frame track flags from the boxes; idempotent."""
    keep_unverified = bool(tr["selection_unverified"].any()) if "selection_unverified" in tr.columns else False
    tr = tr[[c for c in ("frame", "side", "present", "track_id", "x1", "y1", "x2", "y2", "score", "det_row")
             if c in tr.columns]]
    tr = tr.sort_values(["side", "frame"]).reset_index(drop=True)
    tr["cx"] = (tr.x1 + tr.x2) / 2
    tr["h"] = tr.y2 - tr.y1
    tr["track_break"] = False
    tr["id_switch_suspect"] = False
    for side, g in tr.groupby("side"):
        pres = g.present.values
        first = np.argmax(pres) if pres.any() else len(pres)
        last = len(pres) - 1 - np.argmax(pres[::-1]) if pres.any() else -1
        brk = np.zeros(len(g), bool)
        if pres.any():
            brk[first:last + 1] = ~pres[first:last + 1]
        tr.loc[g.index, "track_break"] = brk
        cx, h = g.cx.values, g.h.values
        jump = np.zeros(len(g), bool)
        jump[1:] = np.abs(np.diff(cx)) > 0.6 * np.nanmax(np.vstack([h[1:], h[:-1]]), axis=0)
        tid = g.track_id.values
        jump[1:] |= (tid[1:] != tid[:-1]) & (tid[1:] >= 0) & (tid[:-1] >= 0)
        tr.loc[g.index, "id_switch_suspect"] = jump
    # left/right crossing on screen: identity becomes uncertain, labels are NOT swapped
    piv = tr.pivot(index="frame", columns="side", values="cx")
    crossing = (piv.get("left") > piv.get("right")) if {"left", "right"} <= set(piv.columns) else None
    tr["screen_order_ok"] = True
    tr["identity_uncertain"] = False
    if crossing is not None:
        cross_frames = set(piv.index[crossing.fillna(False).values])
        m = tr.frame.isin(cross_frames)
        tr.loc[m, "screen_order_ok"] = False
        tr.loc[m, "identity_uncertain"] = True
    tr.loc[tr.id_switch_suspect, "identity_uncertain"] = True
    # pair plausibility: both fencers stand on the same strip at a similar scale. A much smaller box, or
    # feet far apart vertically, means one "fencer" is probably a background person or an official.
    tr["pair_implausible"] = False
    if {"left", "right"} <= set(tr.side.unique()):
        L = tr[tr.side == "left"].set_index("frame")
        R = tr[tr.side == "right"].set_index("frame")
        both = L.present & R.present
        hmax = np.maximum(L.h, R.h)
        bad = both & ((np.minimum(L.h, R.h) / hmax < 0.6) | ((L.y2 - R.y2).abs() > 0.3 * hmax))
        bad_frames = set(bad.index[bad.fillna(False).values])
        m = tr.frame.isin(bad_frames)
        tr.loc[m, "pair_implausible"] = True
        tr.loc[m, "identity_uncertain"] = True
    if keep_unverified:
        tr["selection_unverified"] = True
        tr["identity_uncertain"] = True
    # per-frame screen position, independent of the fixed side label
    tr["screen_position"] = np.where(~tr.present, "absent", np.where(tr.screen_order_ok, tr.side,
                                     np.where(tr.side == "left", "right", "left")))
    return tr


# --------------------------------------------------------------------------- pose arrays
def pose_arrays(tracks: pd.DataFrame, kps: np.ndarray, frames: list[int]) -> dict:
    """Per side T x 17 x 3 raw keypoints (NaN when absent) + mask (conf >= 0.3) + interpolation."""
    T = len(frames)
    out = {}
    for side in ("left", "right"):
        raw = np.full((T, 17, 3), np.nan, dtype=np.float32)
        if len(tracks):
            g = tracks[tracks.side == side].set_index("frame")
            for i, f in enumerate(frames):
                if f in g.index:
                    dr = int(g.loc[f, "det_row"])
                    if 0 <= dr < len(kps):
                        raw[i] = kps[dr]
        out[side] = process_pose(raw)
    return out


def process_pose(raw: np.ndarray) -> dict:
    T = raw.shape[0]
    conf = np.nan_to_num(raw[..., 2], nan=0.0)
    mask = (conf >= KP_CONF_MIN).astype(np.uint8)
    xy = raw[..., :2].copy()
    xy[mask == 0] = np.nan
    interp = np.zeros((T, raw.shape[1]), np.uint8)
    for j in range(raw.shape[1]):
        m = mask[:, j].astype(bool)
        i = 0
        while i < T:
            if not m[i]:
                k = i
                while k < T and not m[k]:
                    k += 1
                gap = k - i
                if i > 0 and k < T and gap <= MAX_INTERP_GAP:
                    for c in range(2):
                        xy[i:k, j, c] = np.interp(np.arange(i, k), [i - 1, k], [xy[i - 1, j, c], xy[k, j, c]])
                    interp[i:k, j] = 1
                i = k
            else:
                i += 1
    return {"raw": raw, "mask": mask, "interp": interp, "xy": xy}


def save_pose_npz(path, pose: dict, frames, source: dict):
    arrs = {"frames": np.asarray(frames)}
    for side, p in pose.items():
        for k, v in p.items():
            arrs[f"{side}_{k}"] = v
    np.savez_compressed(path, **arrs, source=json.dumps(source))


def load_pose_npz(path) -> tuple[dict, list[int], dict]:
    z = np.load(path, allow_pickle=False)
    pose = {s: {k: z[f"{s}_{k}"] for k in ("raw", "mask", "interp", "xy")} for s in ("left", "right")}
    return pose, z["frames"].tolist(), json.loads(str(z["source"]))


# --------------------------------------------------------------------------- imported skeletons
def load_imported_skeleton(path: str, frames: list[int]) -> tuple[pd.DataFrame, dict, dict]:
    """Format (JSON): {"source": "...", "keypoint_format": "COCO-17", "synthetic": bool,
       "frames": {"<frame>": {"left": {"bbox":[x1,y1,x2,y2], "kp": [[x,y,c] x17]}, "right": {...}}}}"""
    with open(path) as f:
        data = json.load(f)
    if data.get("keypoint_format") != "COCO-17":
        raise ValueError("imported skeleton must be COCO-17")
    T = len(frames)
    rows = []
    pose = {}
    for side in ("left", "right"):
        raw = np.full((T, 17, 3), np.nan, dtype=np.float32)
        for i, f in enumerate(frames):
            e = data["frames"].get(str(f), {}).get(side)
            if e:
                raw[i] = np.asarray(e["kp"], dtype=np.float32)
                x1, y1, x2, y2 = e["bbox"]
                rows.append({"frame": f, "side": side, "present": True, "track_id": 1 if side == "left" else 2,
                             "x1": x1, "y1": y1, "x2": x2, "y2": y2, "score": 1.0, "det_row": -1})
            else:
                rows.append({"frame": f, "side": side, "present": False, "track_id": -1, "x1": np.nan,
                             "y1": np.nan, "x2": np.nan, "y2": np.nan, "score": np.nan, "det_row": -1})
        pose[side] = process_pose(raw)
    tr = add_track_flags(pd.DataFrame(rows))
    src = {"backend": "imported_skeleton", "file": os.path.basename(path), "source": data.get("source"),
           "synthetic": bool(data.get("synthetic")), "keypoint_format": "COCO-17"}
    return tr, pose, src
