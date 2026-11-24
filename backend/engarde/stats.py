"""Descriptive statistics computable reliably from what exists. Anything that needs missing inputs is
returned as {"available": False, "reason": ...} — never 0."""
from __future__ import annotations

import numpy as np

from . import taxonomy as tx


def _unavail(reason):
    return {"available": False, "value": None, "reason": reason}


def insights(store, video_id=None, source="annotations") -> dict:
    touches = store.list_touches(video_id=video_id) if video_id else store.list_touches()
    segs = []
    for t in touches:
        idx = store.frame_index(t["video_id"])
        ts = idx["t"]
        rows = store.annotations(t["touch_id"]) if source == "annotations" else \
            [p for p in store.predictions(t["touch_id"]) if not p["no_call"]]
        for s in rows:
            end_t = ts[s["end_frame"]] if s["end_frame"] < len(ts) else ts[-1] + (ts[-1] - ts[-2])
            segs.append({**s, "dur_ms": float((end_t - ts[s["start_frame"]]) * 1000), "bout_id": t["bout_id"],
                         "video_id": t["video_id"]})
    labels = store.tax.footwork + (store.tax.untrained if source == "annotations" else [])
    by = {}
    for side in tx.SIDES:
        for lab in labels:
            d = [s["dur_ms"] for s in segs if s["side"] == side and s["label"] == lab]
            by.setdefault(side, {})[lab] = {
                "count": len(d),
                "total_ms": round(float(np.sum(d)), 1) if d else None,
                "mean_ms": round(float(np.mean(d)), 1) if d else None,
                "median_ms": round(float(np.median(d)), 1) if d else None,
            }
    window_frames = sum(t["end_frame"] - t["start_frame"] for t in touches) * 2
    labelled = sum(s["end_frame"] - s["start_frame"] for s in segs)
    any_calibrated = any(store.get_video(t["video_id"])["calibrated"] for t in touches) if touches else False
    expert = {t["touch_id"]: store.events(t["touch_id"], sources=["expert"]) for t in touches}
    # advance-to-lunge interval: needs an expert lunge_start and an Advance segment ending before it
    a2l = []
    for t in touches:
        idx = store.frame_index(t["video_id"])
        for e in expert[t["touch_id"]]:
            if e["event_type"] != "lunge_start" or e["event_frame"] is None:
                continue
            adv = [s for s in segs if s["touch_id"] == t["touch_id"] and s["side"] == e["side"]
                   and s["label"] in ("Advance", "Step forward", "STEPS_FORWARD") and s["end_frame"] <= e["event_frame"]]
            if adv:
                last = max(adv, key=lambda s: s["end_frame"])
                a2l.append(float((idx["t"][e["event_frame"]] - idx["t"][last["end_frame"]]) * 1000))
    hvf = []
    for t in touches:
        ev = expert[t["touch_id"]]
        for side in tx.SIDES:
            arm = [e for e in ev if e["side"] == side and e["event_type"] == "arm_extension_start" and e["event_frame"] is not None]
            ls = [e for e in ev if e["side"] == side and e["event_type"] == "lunge_start" and e["event_frame"] is not None]
            if arm and ls:
                hvf.append(arm[0]["event_time_ms"] - ls[0]["event_time_ms"])
    metrics = {
        "launch_distance_m": _unavail("no piste calibration: metric distances are not computed") if not any_calibrated
        else _unavail("calibrated launch distance not implemented in this MVP"),
        "strip_position_m": _unavail("no piste calibration"),
        "advance_to_lunge_ms": ({"available": True, "n": len(a2l), "median": round(float(np.median(a2l)), 1),
                                 "values": [round(v, 1) for v in a2l]} if a2l else
                                _unavail("needs expert lunge_start events preceded by an Advance segment")),
        "hand_vs_foot_ms": ({"available": True, "n": len(hvf), "median": round(float(np.median(hvf)), 1)} if hvf else
                            _unavail("needs expert arm_extension_start and lunge_start events on the same fencer")),
        "recovery_duration_ms": _unavail("needs lunge_land events and a Layer-A On Guard state (not labelled yet)"),
        "torso_lean_at_lunge_land": _unavail("needs lunge_land events from expert labels (PRD §12.2: compute from manual events first)"),
    }
    return {"source": source, "labels": labels, "n_touches": len(touches), "n_segments": len(segs),
            "labelled_frame_fraction": round(labelled / window_frames, 4) if window_frames else None,
            "by_side": by, "metrics": metrics,
            "label_sources": {src: sum(1 for s in segs if s.get("label_source") == src) for src in tx.LABEL_SOURCES}
            if source == "annotations" else None}
