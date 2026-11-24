"""S5 recognition (rule baseline; XGBoost lives in engarde/ml.py) and S6 quality gate.

Rule baseline (PRD §7.2) — produces labels only. It never emits probabilities or confidence:
class_probs and calibrated_prob stay NULL, and the run is labelled "Rule baseline / unvalidated".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..intervals import Seg, frames_to_segments, median_filter_labels, merge_short

DEFAULT_RULES = {
    # body-scale units per second (scale = nose-to-front-ankle height ~ 1.4-1.6 m in reality)
    "v1_hip": 0.25,          # Advance / Retreat hip-speed threshold
    "v2_front_foot": 1.5,    # Lunge: front-ankle speed peak
    "s1_spread_gain": 0.30,  # Lunge: foot-spread increase that PERSISTS after landing (a step's spread returns)
    "lunge_onset_frac": 0.25,
    "lunge_land_frac": 0.20,
    "lunge_min_ms": 200,     # a lunge shorter than this is not called a lunge
    "lunge_hip_gain": 0.10,  # hips must move forward (body-scale units) over the lunge
    "wide_stance_gain": 0.35,  # Lunge also when foot spread > window median + this while hips move forward  # lunge_land: front-toe(ankle) speed falls below 20% of its peak
    "min_pose_frac": 0.5,    # a frame needs >= 50% of core joints for any rule to fire
    "median_k": 5,
    "min_seg_len": 4,
}
OTHER = "Other/Still"
LUNGE_LABELS = ("Lunge", "LUNGE")   # label names that mean a lunge interval, in any label set
NO_EVIDENCE = "__no_evidence__"   # internal marker; becomes a no_call interval, never a label


def evidence_mask(f: pd.DataFrame, cfg=DEFAULT_RULES) -> np.ndarray:
    """A frame has footwork evidence when the fencer is tracked, >= 50% of core joints are visible AND
    >= 50% of the lower-body joints (hips, knees, ankles) are visible."""
    lower = f.kp_lower_missing.values if "kp_lower_missing" in f else f.kp_core_missing.values
    return (f.present.values.astype(bool) & ((1 - f.kp_core_missing.values) >= cfg["min_pose_frac"])
            & ((1 - lower) >= 0.5))


def rule_labels(f: pd.DataFrame, cfg=DEFAULT_RULES) -> tuple[list[str], dict]:
    """f: features for ONE side, ordered by frame."""
    n = len(f)
    v = f.hip_vel.values
    fav = f.front_ankle_vel.values
    spread = f.foot_spread.values
    closing = f.closing_rate.values
    ok = evidence_mask(f, cfg) & ~np.isnan(v)
    labels = [OTHER] * n
    for i in range(n):
        if not ok[i]:
            labels[i] = NO_EVIDENCE
        elif v[i] > cfg["v1_hip"] and (np.isnan(closing[i]) or closing[i] > 0):
            labels[i] = "Advance"
        elif v[i] < -cfg["v1_hip"]:
            labels[i] = "Retreat"
    lunges = []
    # lunge: local peak of front-ankle speed above v2 with a sufficient spread gain
    i = 0
    while i < n:
        if ok[i] and not np.isnan(fav[i]) and fav[i] > cfg["v2_front_foot"]:
            j = i
            while j + 1 < n and not np.isnan(fav[j + 1]) and fav[j + 1] > cfg["v2_front_foot"]:
                j += 1
            pk = i + int(np.nanargmax(fav[i:j + 1]))
            peak = fav[pk]
            a = pk
            while a - 1 >= 0 and not np.isnan(fav[a - 1]) and fav[a - 1] > cfg["lunge_onset_frac"] * peak:
                a -= 1
            b = pk
            while b + 1 < n and not np.isnan(fav[b + 1]) and fav[b + 1] > cfg["lunge_land_frac"] * peak:
                b += 1
            pre = np.nanmin(spread[max(0, a - 3):a + 1]) if not np.all(np.isnan(spread[max(0, a - 3):a + 1])) else np.nan
            after = spread[b:min(n, b + 6)]
            gain_needed = cfg["s1_spread_gain"]
            if len(after) >= 3 and np.sum(~np.isnan(after)) >= 3:
                post = np.nanmedian(after)
            else:
                # landing not observable (window end / shot cut): fall back to the peak spread inside
                # the interval with a stricter gain, so a step's transient spread is not taken as a lunge
                inside = spread[pk:b + 1]
                post = np.nanmax(inside) if len(inside) and not np.all(np.isnan(inside)) else np.nan
                gain_needed = cfg["s1_spread_gain"] * 1.5
            hip = f.hip_x.values
            dur_ms = (f.t_s.values[min(n - 1, b)] - f.t_s.values[a]) * 1000 if "t_s" in f else (b - a + 1) * 33.3
            hip_gain = (hip[min(n - 1, b)] - hip[a]) if not (np.isnan(hip[a]) or np.isnan(hip[min(n - 1, b)])) else np.nan
            if np.isfinite(pre) and np.isfinite(post) and post - pre > gain_needed \
                    and dur_ms >= cfg["lunge_min_ms"] and np.isfinite(hip_gain) and hip_gain > cfg["lunge_hip_gain"]:
                lunges.append((a, b + 1, pk))
                for k in range(a, b + 1):
                    if labels[k] != NO_EVIDENCE:
                        labels[k] = "Lunge"
            i = j + 1
        else:
            i += 1
    # wide-stance criterion (PRD §7.2: foot spread increases by > s1): robust to a single-frame keypoint
    # dip that splits the front-ankle speed burst
    base_spread = np.nanmedian(spread[ok]) if ok.any() else np.nan
    if np.isfinite(base_spread):
        for k in range(n):
            if ok[k] and not np.isnan(spread[k]) and spread[k] > base_spread + cfg["wide_stance_gain"] \
                    and v[k] > cfg["v1_hip"]:
                labels[k] = "Lunge"
    return labels, {"lunges": lunges, "base_spread": base_spread}


def postprocess(labels: list[str], cfg=DEFAULT_RULES) -> list[Seg]:
    """PRD §7.5 baseline: median filter (5) -> merge segments < 4 frames. No-evidence spans are
    protected from merging so missing pose is never smoothed into a confident label."""
    sm = median_filter_labels(labels, cfg["median_k"])
    # keep no-evidence frames exactly where they were
    sm = [NO_EVIDENCE if labels[i] == NO_EVIDENCE else (OTHER if sm[i] == NO_EVIDENCE else sm[i]) for i in range(len(sm))]
    return merge_short(frames_to_segments(sm), cfg["min_seg_len"], protected=(NO_EVIDENCE,))


QUALITY_THRESHOLDS = {"low_confidence": 0.5, "pose_missing_frac": 0.30}


def quality_gate(seg: Seg, f: pd.DataFrame, offset: int, mean_prob: float | None, calibrated: bool) -> dict:
    """PRD §9.3. Evidence quality and classifier uncertainty are recorded separately."""
    sl = f.iloc[seg.start - offset: seg.end - offset]
    reasons = []
    missing_frac = float((~evidence_mask(sl)).mean()) if len(sl) else 1.0
    if seg.label == NO_EVIDENCE or missing_frac > QUALITY_THRESHOLDS["pose_missing_frac"]:
        reasons.append("pose_missing")
    track_flags = []
    for col, name in (("track_break", "track_break"), ("id_switch_suspect", "id_switch"),
                      ("identity_uncertain", "identity_uncertain"), ("pair_implausible", "pair_implausible")):
        if sl[col].fillna(False).astype(bool).any():
            track_flags.append(name)
    if track_flags:
        reasons.append("track_unstable")
    if sl.camera_cut.fillna(False).astype(bool).any():
        reasons.append("camera_cut")
    if mean_prob is not None and calibrated and mean_prob < QUALITY_THRESHOLDS["low_confidence"]:
        reasons.append("low_confidence")
    return {
        "no_call": bool(reasons),
        "no_call_reason": ",".join(reasons) or None,
        "track_quality": ",".join(track_flags) or "ok",
        "pose_quality": round(1.0 - missing_frac, 3),
        "camera_motion_flag": int(sl.camera_motion_flag.fillna(False).astype(bool).any()),
    }


def rule_events(seg_rows: list[dict], f: pd.DataFrame, offset: int, cfg=DEFAULT_RULES) -> list[dict]:
    """PRD §7.6 event baseline without event labels. Only from Lunge intervals that passed the gate.
    Returns events with frame=None when the evidence needed is missing (-> 'not determinable')."""
    ev = []
    fav = f.front_ankle_vel.values
    elbow = f.front_elbow_angle.values
    for s in seg_rows:
        if s["label"] not in LUNGE_LABELS:
            continue
        if s["no_call"]:
            ev.append({"event_type": "lunge_start", "frame": None, "note": f"lunge interval no_call ({s['no_call_reason']})"})
            continue
        a, b = s["start_frame"] - offset, s["end_frame"] - offset
        ev.append({"event_type": "lunge_start", "frame": s["start_frame"], "note": "start of rule Lunge interval"})
        seg_v = fav[a:min(len(fav), b + 3)]   # landing may fall just after the interval's last frame
        if len(seg_v) and not np.all(np.isnan(seg_v)):
            pk = int(np.nanargmax(seg_v[:b - a]))
            land = None
            for k in range(pk, len(seg_v)):
                if not np.isnan(seg_v[k]) and seg_v[k] < cfg["lunge_land_frac"] * seg_v[pk]:
                    land = k
                    break
            ev.append({"event_type": "lunge_land", "frame": (s["start_frame"] + land) if land is not None else None,
                       "note": "front-ankle speed < 20% of peak" if land is not None else "speed never fell below 20% of peak inside the interval"})
    # arm extension full: first frame front elbow > 160 deg (after being below it)
    below = False
    found = None
    for k in range(len(elbow)):
        if np.isnan(elbow[k]):
            continue
        if elbow[k] < 150:
            below = True
        elif below and elbow[k] > 160:
            found = k
            break
    if found is not None:
        ev.append({"event_type": "arm_extension_full", "frame": offset + found, "note": "front elbow angle > 160 deg"})
    return ev
