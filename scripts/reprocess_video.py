#!/usr/bin/env python3
"""Re-extract frames on the analysis canvas and re-run the pipeline for every touch of a video
(used after the resolution-change fix). Earlier runs stay in the DB as history; human labels and
reviews are untouched.
  python scripts/reprocess_video.py --data data --video vid_xxx [--proxy]
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde import video as vid  # noqa: E402
from engarde.pipeline import runner  # noqa: E402
from engarde.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()
    s = Store(a.data)
    v = s.get_video(a.video)
    cv = vid.canvas_size(v["path"])
    s.db.update("videos", "video_id", a.video, {"width": cv["width"], "height": cv["height"]})
    vd = s.video_dir(a.video)
    for f in ("content_rect.json", "poster.jpg"):
        if os.path.exists(os.path.join(vd, f)):
            os.remove(os.path.join(vd, f))
    print(json.dumps({"canvas": cv, "content_rect": s.get_video(a.video)["content_rect"]}), flush=True)
    if a.proxy:
        t0 = time.time()
        vid.make_browser_proxy(v["path"], os.path.join(vd, "proxy.mp4"))
        print(json.dumps({"proxy_sec": round(time.time() - t0, 1)}), flush=True)
    out = []
    for t in s.list_touches(video_id=a.video):
        shutil.rmtree(s.frames_dir(t["touch_id"]), ignore_errors=True)
        shutil.rmtree(s.stage_dir(t["touch_id"]), ignore_errors=True)
        t0 = time.time()
        try:
            r = runner.analyze_touch(s, t["touch_id"], pose_source="yolo", force=True)
            row = {"touch_id": t["touch_id"], "light_frame": t["light_frame"], "status": "ok", "run_id": r["run_id"],
                   "n_segments": r["n_segments"], "no_call": r["no_call"], "sec": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            row = {"touch_id": t["touch_id"], "status": "failed", "error": f"{type(e).__name__}: {e}"}
        out.append(row)
        print(json.dumps(row), flush=True)
    if a.report:
        json.dump(out, open(a.report, "w"), indent=2)


if __name__ == "__main__":
    main()
