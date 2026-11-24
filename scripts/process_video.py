#!/usr/bin/env python3
"""Process a range of an already-imported video: light CANDIDATES -> touches -> YOLO pipeline.
All results use the ORIGINAL video's frame numbers and timestamps (ranges are processed in chunks,
never re-encoded or re-based).

  python scripts/process_video.py --data ./data --video vid_xxx --from 30 --to 60 [--chunk 60] [--no-analyze]
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde import lights  # noqa: E402
from engarde.pipeline import runner  # noqa: E402
from engarde.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    ap.add_argument("--video", required=True)
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--chunk", type=float, default=60.0, help="seconds per detection chunk")
    ap.add_argument("--no-analyze", action="store_true")
    ap.add_argument("--skip-detect", action="store_true", help="only analyse existing touches")
    ap.add_argument("--report", default=None, help="write a JSON report here")
    ap.add_argument("--reanalyze", action="store_true", help="re-run analysis for touches that already have a run")
    a = ap.parse_args()
    s = Store(a.data)
    v = s.get_video(a.video)
    idx = s.frame_index(v["video_id"])
    t = idx["t"]
    t1 = a.t1 if a.t1 is not None else float(t[-1])
    bouts = s.db.q("SELECT bout_id FROM bouts WHERE video_id=?", (v["video_id"],))
    if not bouts:
        sys.exit("create a bout first (fencer names / sides)")
    bout_id = bouts[0]["bout_id"]
    existing = {x["light_frame"] for x in s.list_touches(video_id=v["video_id"])}
    report = {"video_id": v["video_id"], "range_s": [a.t0, t1], "chunks": [], "touches": []}
    c0 = t1 if a.skip_detect else a.t0
    while c0 < t1:
        c1 = min(t1, c0 + a.chunk)
        # overlap chunks by 2 s so onsets near a boundary keep their baseline; dedupe by frame
        fa = int(np.searchsorted(t, max(0.0, c0 - 2.0)))
        fb = int(np.searchsorted(t, c1))
        t_start = time.time()
        cands = [c for c in lights.detect(v["path"], t, fa, fb, idx=idx) if c0 <= c["t_s"] < c1]
        report["chunks"].append({"range_s": [c0, c1], "frames": [fa, fb], "candidates": len(cands),
                                 "sec": round(time.time() - t_start, 1)})
        for c in cands:
            if any(abs(c["light_frame"] - e) < 30 for e in existing):
                continue
            tc = s.add_touch(bout_id, c["light_frame"], c["lamp_left"], c["lamp_right"],
                             notes=f"detector candidate; red/green onset gap={c['gap_ms']} ms — verify",
                             light_source="detector_candidate")
            existing.add(c["light_frame"])
            report["touches"].append({"touch_id": tc["touch_id"], **c})
        print(json.dumps(report["chunks"][-1]), flush=True)
        c0 = c1
    if not a.no_analyze:
        known = {x["touch_id"] for x in report["touches"]}
        for x in s.list_touches(video_id=v["video_id"]):
            in_range = a.t0 <= float(t[x["light_frame"]]) < t1
            if in_range and x["touch_id"] not in known and (a.reanalyze or not x["latest_run"]):
                report["touches"].append({"touch_id": x["touch_id"], "light_frame": x["light_frame"],
                                          "lamp_left": x["lamp_left"], "lamp_right": x["lamp_right"],
                                          "t_s": round(float(t[x["light_frame"]]), 3), "existing": True})
        report["touches"].sort(key=lambda r: r["light_frame"])
        for tr in report["touches"]:
            t_start = time.time()
            try:
                res = runner.analyze_touch(s, tr["touch_id"], pose_source="yolo")
                man = json.load(open(os.path.join(s.stage_dir(tr["touch_id"]), "manifest.json")))
                q = man["s2_tracking"]["info"]
                tr.update(status="ok", run_id=res["run_id"], n_segments=res["n_segments"], no_call=res["no_call"],
                          selection=q["selection"].get("status"), candidates=q["selection"].get("candidates", [])[:4],
                          merges=q["selection"].get("merges"), quality=q["quality"],
                          camera_motion_frames=man["s4_features"]["info"].get("camera_motion_frames"),
                          camera_cut_frames=man["s4_features"]["info"].get("camera_cut_frames"),
                          sec=round(time.time() - t_start, 1))
            except Exception as e:  # noqa: BLE001
                tr.update(status="failed", error=f"{type(e).__name__}: {e}")
            print(json.dumps({k: tr.get(k) for k in ("touch_id", "light_frame", "status", "sec", "n_segments", "no_call",
                                                      "selection")}), flush=True)
    if a.report:
        with open(a.report, "w") as f:
            json.dump(report, f, indent=2, default=str)


if __name__ == "__main__":
    main()
