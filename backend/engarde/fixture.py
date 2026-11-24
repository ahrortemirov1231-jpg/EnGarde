"""SYNTHETIC FIXTURE — verifies data flow only. It is NOT real fencing footage and says nothing about
recognition quality on real video. Everything it produces carries synthetic=True / is_synthetic=1.

Renders two stick-figure 'fencers' with scripted footwork + a scoring lamp, and writes:
  fixture.mp4                   the video (text 'SYNTHETIC FIXTURE' burned in)
  fixture_skeleton.json         ground-truth COCO-17 keypoints + boxes (importable skeleton format)
  fixture_labels.json           scripted per-fencer segments [start,end) and the light frames
The right fencer's lower body is 'occluded' (conf 0) for frames 150-161 to exercise the quality gate.
"""
from __future__ import annotations

import json
import os

import cv2
import numpy as np

W, H, FPS, N = 1280, 720, 30, 240
LIGHTS = [{"frame": 150, "lamp_left": 1, "lamp_right": 0}, {"frame": 232, "lamp_left": 1, "lamp_right": 1}]
FIG_H = 300.0
GROUND = 600.0
OCCLUDE = (150, 162)  # right fencer, lower body conf=0


def _ease(a, b, t):
    t = np.clip(t, 0, 1)
    s = t * t * (3 - 2 * t)
    return a + (b - a) * s


# scripted segments in frames, [start, end)  (direction 'forward' = towards the opponent)
SCRIPT = {
    "left": [(0, 90, "Other/Still"), (90, 110, "Advance"), (110, 125, "Other/Still"), (125, 140, "Lunge"),
             (140, 180, "Other/Still"), (180, 200, "Retreat"), (200, 240, "Other/Still")],
    "right": [(0, 95, "Other/Still"), (95, 115, "Retreat"), (115, 135, "Advance"), (135, 205, "Other/Still"),
              (205, 222, "Advance"), (222, 240, "Other/Still")],
}
STEP = 0.32 * FIG_H
LUNGE_FOOT = 0.45 * FIG_H
LUNGE_HIP = 0.32 * FIG_H


def _pose_params(side: str):
    """Per frame: (hip_x_forward, front_foot_off, back_foot_off, arm_ext 0..1) in 'forward' units."""
    hip = np.zeros(N)
    ff = np.zeros(N)
    bf = np.zeros(N)
    arm = np.zeros(N)
    base = 0.0
    lunge_extra_hip = np.zeros(N)
    lunge_extra_ff = np.zeros(N)
    for (a, b, lab) in SCRIPT[side]:
        for f in range(a, b):
            t = (f - a) / max(1, (b - a - 1))
            if lab in ("Advance", "Retreat"):
                d = STEP if lab == "Advance" else -STEP
                lead = _ease(0, d, t * 1.5)          # leading foot moves first
                trail = _ease(0, d, t * 1.5 - 0.5)
                if lab == "Advance":
                    ff[f], bf[f] = base + lead, base + trail
                else:
                    bf[f], ff[f] = base + lead, base + trail
                hip[f] = base + _ease(0, d, t)
            else:
                ff[f] = bf[f] = hip[f] = base
            if lab == "Lunge":
                lunge_extra_ff[f] = _ease(0, LUNGE_FOOT, t * 1.25)
                lunge_extra_hip[f] = _ease(0, LUNGE_HIP, t * 1.1)
                arm[f] = _ease(0, 1, t * 2.0)
        if lab == "Advance":
            base += STEP
        elif lab == "Retreat":
            base -= STEP
        if lab == "Lunge":
            # hold the lunge position for 10 frames then recover back over the next 15 (labelled Other/Still)
            hold_end = min(N, b + 10)
            for f in range(b, hold_end):
                lunge_extra_ff[f], lunge_extra_hip[f], arm[f] = LUNGE_FOOT, LUNGE_HIP, 1.0
            for f in range(hold_end, min(N, hold_end + 15)):
                t = (f - hold_end) / 14
                lunge_extra_ff[f] = _ease(LUNGE_FOOT, 0, t)
                lunge_extra_hip[f] = _ease(LUNGE_HIP, 0, t)
                arm[f] = _ease(1, 0, t)
    return hip + lunge_extra_hip, ff + lunge_extra_ff, bf, arm


def _keypoints(x0, sign, hipf, fff, bff, arm):
    """COCO-17 for a side-view stick figure. sign=+1 faces right (left fencer), -1 faces left."""
    s = FIG_H
    hip_x = x0 + sign * hipf
    front_ank = np.array([x0 + sign * (0.24 * s + fff), GROUND - 0.02 * s])
    back_ank = np.array([x0 + sign * (-0.24 * s + bff), GROUND - 0.02 * s])
    spread = abs(front_ank[0] - back_ank[0])
    hip_y = GROUND - min(0.48 * s, np.sqrt(max(0.0, (0.56 * s) ** 2 - (spread / 2) ** 2)) + 0.02 * s)
    hipc = np.array([hip_x, hip_y])

    def knee(ank, fwd):
        mid = (hipc + ank) / 2
        L = 0.29 * s
        d = np.linalg.norm(ank - hipc) / 2
        bend = np.sqrt(max(0.0, L ** 2 - d ** 2))
        return mid + np.array([sign * fwd * bend, -0.2 * bend])

    sh = hipc + np.array([sign * 0.03 * s, -0.30 * s])
    head = sh + np.array([sign * 0.03 * s, -0.12 * s])
    # weapon arm: from bent (elbow down) to extended forward
    el = sh + np.array([sign * (0.10 + 0.08 * arm) * s, (0.10 - 0.10 * arm) * s])
    wr = el + np.array([sign * (0.06 + 0.12 * arm) * s, (-0.05 - 0.0 * arm) * s])
    back_el = sh + np.array([-sign * 0.10 * s, -0.06 * s])
    back_wr = back_el + np.array([-sign * 0.04 * s, -0.10 * s])
    fk, bk = knee(front_ank, 1.0), knee(back_ank, 1.0)
    off = 0.015 * s  # near/far body side offset so L/R joints are distinct
    # anatomical: left fencer is right-handed => right side is front (weapon arm + front leg)
    if sign > 0:
        R = dict(sh=sh + [0, off], el=el, wr=wr, hip=hipc + [0, off], kn=fk, ank=front_ank)
        Lh = dict(sh=sh - [0, off], el=back_el, wr=back_wr, hip=hipc - [0, off], kn=bk, ank=back_ank)
    else:
        Lh = dict(sh=sh + [0, off], el=el, wr=wr, hip=hipc + [0, off], kn=fk, ank=front_ank)
        R = dict(sh=sh - [0, off], el=back_el, wr=back_wr, hip=hipc - [0, off], kn=bk, ank=back_ank)
    nose = head + [sign * 0.04 * s, 0]
    pts = [nose, head + [sign * 0.02 * s, -0.02 * s], head + [sign * 0.02 * s, -0.02 * s],
           head - [0, 0.01 * s], head - [0, 0.01 * s],
           Lh["sh"], R["sh"], Lh["el"], R["el"], Lh["wr"], R["wr"], Lh["hip"], R["hip"], Lh["kn"], R["kn"],
           Lh["ank"], R["ank"]]
    return np.array(pts, dtype=float)


BONES = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (11, 12), (5, 11), (6, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


def make_fixture(out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    vpath = os.path.join(out_dir, "fixture.mp4")
    tmp = os.path.join(out_dir, "_raw.avi")
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    params = {s: _pose_params(s) for s in ("left", "right")}
    skel = {"source": "synthetic fixture ground truth (rendered stick figures)", "keypoint_format": "COCO-17",
            "synthetic": True, "frames": {}}
    light_frames = {L["frame"]: L for L in LIGHTS}
    for f in range(N):
        img = np.full((H, W, 3), (236, 238, 232), np.uint8)
        cv2.rectangle(img, (60, int(GROUND)), (W - 60, int(GROUND) + 40), (190, 196, 188), -1)
        for xm in (W // 2, W // 2 - 300, W // 2 + 300):
            cv2.line(img, (xm, int(GROUND)), (xm, int(GROUND) + 40), (120, 128, 120), 2)
        lit = [L for lf, L in light_frames.items() if lf <= f < lf + 20]
        cv2.rectangle(img, (520, 40), (620, 90), (60, 60, 200) if lit and lit[0]["lamp_left"] else (90, 90, 90), -1)
        cv2.rectangle(img, (660, 40), (760, 90), (60, 180, 60) if lit and lit[0]["lamp_right"] else (90, 90, 90), -1)
        cv2.putText(img, "SYNTHETIC FIXTURE - not real footage", (40, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (70, 70, 70), 2, cv2.LINE_AA)
        cv2.putText(img, f"frame {f}", (W - 220, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (70, 70, 70), 2, cv2.LINE_AA)
        entry = {}
        for side, x0, sign in (("left", 420.0, 1), ("right", 860.0, -1)):
            hipf, fff, bff, arm = (p[f] for p in params[side])
            kp = _keypoints(x0, sign, hipf, fff, bff, arm)
            col = (40, 44, 42)
            for a, b in BONES:
                cv2.line(img, tuple(int(v) for v in kp[a]), tuple(int(v) for v in kp[b]), col, 9, cv2.LINE_AA)
            cv2.circle(img, tuple(int(v) for v in kp[0] - [sign * 0.04 * FIG_H, 0]), 22, col, -1, cv2.LINE_AA)
            # blade
            tip = kp[10 if sign > 0 else 9] + [sign * 0.45 * FIG_H, -0.02 * FIG_H]
            cv2.line(img, tuple(int(v) for v in kp[10 if sign > 0 else 9]), tuple(int(v) for v in tip), (90, 90, 90), 3)
            conf = np.full(17, 0.95)
            if side == "right" and OCCLUDE[0] <= f < OCCLUDE[1]:
                conf[11:] = 0.0      # lower body unobserved
            xs, ys = kp[:, 0], kp[:, 1]
            entry[side] = {"bbox": [float(xs.min() - 25), float(ys.min() - 30), float(xs.max() + 25), float(ys.max() + 5)],
                           "kp": [[round(float(x), 2), round(float(y), 2), float(c)] for (x, y), c in zip(kp, conf)]}
        if OCCLUDE[0] <= f < OCCLUDE[1]:   # draw an occluder over the right fencer's legs
            cv2.rectangle(img, (700, 420), (1100, 610), (176, 182, 176), -1)
            cv2.putText(img, "occluded", (840, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2, cv2.LINE_AA)
        skel["frames"][str(f)] = entry
        vw.write(img)
    vw.release()
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", vpath], check=True)
    os.remove(tmp)
    with open(os.path.join(out_dir, "fixture_skeleton.json"), "w") as fh:
        json.dump(skel, fh)
    labels = {"synthetic": True, "fps": FPS, "n_frames": N, "lights": LIGHTS, "occluded_right": list(OCCLUDE),
              "interval_convention": "[start_frame, end_frame)",
              "segments": {s: [{"start_frame": a, "end_frame": b, "label": lab} for a, b, lab in SCRIPT[s]]
                           for s in SCRIPT}}
    with open(os.path.join(out_dir, "fixture_labels.json"), "w") as fh:
        json.dump(labels, fh, indent=2)
    return {"video": vpath, "skeleton": os.path.join(out_dir, "fixture_skeleton.json"),
            "labels": os.path.join(out_dir, "fixture_labels.json")}
