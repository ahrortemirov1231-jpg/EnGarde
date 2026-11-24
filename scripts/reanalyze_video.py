#!/usr/bin/env python3
"""Re-run the pipeline for every touch of a video using cached stages (YOLO detections are reused;
selection, pose assembly, features, recognition and the quality gate are recomputed when their rules
changed). Earlier runs stay as history.   python scripts/reanalyze_video.py --data data --video vid_x"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde.pipeline import runner  # noqa: E402
from engarde.store import Store  # noqa: E402
ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--video", required=True)
a = ap.parse_args()
s = Store(a.data)
for t in s.list_touches(video_id=a.video):
    t0 = time.time()
    try:
        r = runner.analyze_touch(s, t["touch_id"], pose_source="auto")
        print(json.dumps({"seq": t["seq"], "touch_id": t["touch_id"], "status": "ok", "stages": r["stage_status"],
                          "sec": round(time.time() - t0, 1)}), flush=True)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"seq": t["seq"], "touch_id": t["touch_id"], "status": "failed", "error": f"{type(e).__name__}: {e}"}), flush=True)
