#!/usr/bin/env python3
"""Point an imported analysis at the original video file on THIS machine, and verify it is the same
video before use: packet count == frame count in the index, and frame timestamps at several places
match the index exactly (keyframe-seek decode with PTS check). Nothing is changed if a check fails.

  python scripts/relink_video.py --data data --list
  python scripts/relink_video.py --data data --video vid_xxx --file ~/Downloads/X.webm [--proxy]

--proxy also builds an H.264 browser proxy (about real time). Without it the browser plays the original
file (VP9 WebM: use Chrome or Firefox; Safari may not play it). Analysis does not need the proxy.
"""
import argparse, json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde import video as vid  # noqa: E402
from engarde.store import Store  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True); ap.add_argument("--video"); ap.add_argument("--file")
ap.add_argument("--list", action="store_true"); ap.add_argument("--proxy", action="store_true")
a = ap.parse_args()
s = Store(a.data)
if a.list or not a.video:
    for v in s.db.q("SELECT video_id, title, n_frames, path FROM videos"):
        print(f'{v["video_id"]}  {v["title"]}  frames={v["n_frames"]}  linked={"yes" if os.path.exists(v["path"]) else "no"}')
    sys.exit(0)
path = os.path.abspath(os.path.expanduser(a.file))
if not os.path.exists(path):
    sys.exit(f"file not found: {path}")
v = s.db.one("SELECT * FROM videos WHERE video_id=?", (a.video,))
if not v:
    sys.exit(f"unknown video {a.video}; run with --list")
idx = s.frame_index(a.video)
n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True)
        .stdout.strip().strip(",") or 0)
print(f"video packets in file: {n}   frames in analysis: {len(idx['t'])}")
if n != len(idx["t"]):
    sys.exit("MISMATCH: this is not the video that was analysed (frame counts differ). Nothing changed.")
vid._canvas_cache.pop(path, None)
for f in (0, len(idx["t"]) // 4, len(idx["t"]) // 2, 3 * len(idx["t"]) // 4, len(idx["t"]) - 2):
    try:
        list(vid.decode_range(path, idx, f, f + 1, out_w=640))   # raises if timestamps differ
    except RuntimeError:
        sys.exit(f"MISMATCH: frame timestamps around frame {f} differ from the analysed video. Nothing changed.")
    print(f"  frame {f}: timestamp {idx['t'][f]:.3f}s verified")
cv = vid.canvas_size(path)
if (cv["width"], cv["height"]) != (v["width"], v["height"]):
    sys.exit(f"MISMATCH: canvas {cv['width']}x{cv['height']} != analysed {v['width']}x{v['height']}. Nothing changed.")
s.db.update("videos", "video_id", a.video, {"path": path})
proxy = os.path.join(s.video_dir(a.video), "proxy.mp4")
if a.proxy:
    print("building browser proxy (about real time)…")
    vid.make_browser_proxy(path, proxy)
print(f"linked {a.video} -> {path}")
