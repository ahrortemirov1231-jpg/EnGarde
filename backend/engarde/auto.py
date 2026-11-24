"""Upload -> full automatic analysis, as one background job:
index (exact frame timestamps) -> bout -> scoring-light candidates over the whole video -> touches ->
per-touch pipeline (S1-S6) -> browser proxy. Light candidates still need a human 'verify' in Review."""
from __future__ import annotations

import os

import numpy as np

from . import lights
from . import video as vid
from .pipeline import runner


def auto_analyze(store, video_id, report=None, left_name=None, right_name=None, chunk_s=120.0):
    rep = report or (lambda *a, **k: None)
    v = store.get_video(video_id)
    if not v["indexed"]:
        rep(0.01, "indexing frames", "decoding every frame once for exact timestamps")
        store.index_video(video_id, make_proxy=False)
        v = store.get_video(video_id)
    bouts = store.db.q("SELECT bout_id FROM bouts WHERE video_id=?", (video_id,))
    if bouts:
        bout_id = bouts[0]["bout_id"]
    else:
        b = store.add_bout(video_id, v["title"],
                           left={"athlete_id": left_name or None, "display_name": left_name or None},
                           right={"athlete_id": right_name or None, "display_name": right_name or None})
        bout_id = b["bout_id"]
    idx = store.frame_index(video_id)
    t = idx["t"]
    existing = {x["light_frame"] for x in store.list_touches(video_id=video_id)}
    n_new, c0, T_end = 0, 0.0, float(t[-1])
    while c0 < T_end:
        c1 = min(T_end + 1e-3, c0 + chunk_s)
        rep(0.05 + 0.25 * (c0 / max(T_end, 1)), "finding scoring lights", f"{c0:.0f}-{c1:.0f} s of {T_end:.0f} s")
        fa = int(np.searchsorted(t, max(0.0, c0 - 2.0)))
        fb = int(np.searchsorted(t, c1))
        for c in lights.detect(v["path"], t, fa, fb, idx=idx):
            if not c0 <= c["t_s"] < c1 or any(abs(c["light_frame"] - e) < 30 for e in existing):
                continue
            store.add_touch(bout_id, c["light_frame"], c["lamp_left"], c["lamp_right"],
                            notes=f"detector candidate; red/green onset gap={c['gap_ms']} ms - verify",
                            light_source="detector_candidate")
            existing.add(c["light_frame"])
            n_new += 1
        c0 = c1
    todo = [x for x in store.list_touches(video_id=video_id) if not x["latest_run"]]
    failed = []
    for i, x in enumerate(todo):
        rep(0.30 + 0.65 * i / max(1, len(todo)), "analysing touches", f"touch {i + 1} of {len(todo)}")
        try:
            runner.analyze_touch(store, x["touch_id"], pose_source="auto")
        except Exception as e:  # noqa: BLE001 - one bad touch must not stop the video
            failed.append({"touch_id": x["touch_id"], "error": f"{type(e).__name__}: {e}"})
    proxy = os.path.join(store.video_dir(video_id), "proxy.mp4")
    meta = vid.probe(v["path"])
    if not os.path.exists(proxy) and vid.needs_browser_proxy(meta, v["path"]):
        rep(0.96, "making browser copy", "for smooth playback; analysis is already done")
        vid.make_browser_proxy(v["path"], proxy)
    return {"new_touches": n_new, "analysed": len(todo) - len(failed), "failed": failed}
