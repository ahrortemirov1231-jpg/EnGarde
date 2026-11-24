"""S1 scoring-light CANDIDATE detector for broadcast footage (chroma onset), in the spirit of AIRS
detect-lights.mjs. Output is a list of candidates that a human must verify — never treated as truth.

Signal: area of strongly saturated red / green pixels in the upper part of the frame (scoreboard lamps),
excluding the bottom broadcast score bar. A lamp onset is a sustained rise above a rolling baseline.
Convention: red lamp = left fencer, green lamp = right fencer (FIE standard). Onsets of both colours
within 0.2 s (lockout physics) are merged into one candidate; the per-side gap is recorded.
"""
from __future__ import annotations

import cv2
import numpy as np


def lamp_series(path: str, frames: range, scale: float = 0.25, roi=(0.08, 0.80), report=None, idx=None) -> dict:
    from . import video as vid
    red, green, fr = [], [], []
    n = len(frames)
    W0 = vid.probe(path)["width"]
    small = np.zeros((1, 1, 3), np.uint8)
    for i, img in vid.decode_range(path, idx, frames.start, frames.stop, out_w=int(W0 * scale)):
        h = img.shape[0]
        small = img[int(roi[0] * h):int(roi[1] * h)]
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        sat = (hsv[..., 1] > 150) & (hsv[..., 2] > 150)
        hch = hsv[..., 0]
        red.append(int((sat & ((hch < 8) | (hch > 172))).sum()))
        green.append(int((sat & (hch > 45) & (hch < 85)).sum()))
        fr.append(i)
        if report and (i - frames.start) % 200 == 0:
            report((i - frames.start) / max(1, n))
    area = small.shape[0] * small.shape[1] if fr else 1
    return {"frames": np.asarray(fr), "red": np.asarray(red, float) / area, "green": np.asarray(green, float) / area}


def onsets(sig: np.ndarray, t: np.ndarray, rise=0.0007, ratio=3.0, base_s=1.5, hold_s=0.3, refractory_s=2.0) -> list[int]:
    """Indices where sig rises by > `rise` (fraction of ROI area) AND to > ratio x the rolling median of
    the previous base_s seconds, and stays there for hold_s."""
    out = []
    j0 = 0
    last_on = -1e9
    for i in range(len(sig)):
        while t[i] - t[j0] > base_s:
            j0 += 1
        if i - j0 < 3:
            continue
        base = np.median(sig[j0:i])
        if sig[i] - base > rise and sig[i] > ratio * base and t[i] - last_on > refractory_s:
            k = i
            while k < len(sig) and t[k] - t[i] < hold_s and sig[k] - base > rise * 0.6 and sig[k] > 0.6 * ratio * base:
                k += 1
            if k < len(sig) and t[k] - t[i] >= hold_s:
                out.append(i)
                last_on = t[i]
    return out


def detect(path: str, t_all: np.ndarray, start: int = 0, end: int | None = None, report=None, idx=None) -> list[dict]:
    """t_all: ORIGINAL-video timestamps used for all reported times; idx: frame index for seeking."""
    end = len(t_all) if end is None else end
    s = lamp_series(path, range(start, end), report=report, idx=idx or {"t": t_all, "key": np.ones(len(t_all), np.int8)})
    fr = s["frames"]
    t = t_all[fr]
    ev = [(fr[i], "red") for i in onsets(s["red"], t)] + [(fr[i], "green") for i in onsets(s["green"], t)]
    ev.sort()
    cands = []
    for f, col in ev:
        if cands and t_all[f] - t_all[cands[-1]["light_frame"]] <= 0.2 and col not in cands[-1]["_cols"]:
            c = cands[-1]
            c["_cols"].add(col)
            c["gap_ms"] = round(float((t_all[f] - t_all[c["light_frame"]]) * 1000), 1)
            continue
        cands.append({"light_frame": int(f), "_cols": {col}, "gap_ms": None})
    out = []
    for c in cands:
        out.append({"light_frame": c["light_frame"], "lamp_left": int("red" in c["_cols"]),
                    "lamp_right": int("green" in c["_cols"]), "gap_ms": c["gap_ms"],
                    "t_s": round(float(t_all[c["light_frame"]]), 3)})
    return out
