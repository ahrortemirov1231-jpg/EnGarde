#!/usr/bin/env python3
"""Import a long video that had to be transferred in keyframe-aligned, stream-copied parts.

The parts are re-joined losslessly (ffmpeg concat, -c copy). The join is VERIFIED against a per-packet
fingerprint of the original video stream (ffmpeg -map 0:v:0 -c copy -f framemd5) made on the source
machine: same packet count, same packet sizes and MD5s, same order => identical decoded frames.
The concat demuxer re-times later parts, so the frame index takes its timestamps from the ORIGINAL
packet PTS; the joined file's own times are kept as t_play (browser playback mapping only).

  python scripts/import_reassembled.py --data ./data --parts p0.webm p1.webm ... \
      --framemd5 original_video.framemd5 --title "..." --original-name X.webm
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde import video as vid  # noqa: E402
from engarde.store import Store  # noqa: E402


def framemd5(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0", "-c", "copy", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    tb = [l for l in out.splitlines() if l.startswith("#tb 0:")][0].split(":")[1].strip()
    rows = [l.split(",") for l in out.splitlines() if l and not l.startswith("#")]
    return tb, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--parts", nargs="+", required=True)
    ap.add_argument("--framemd5", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--original-name", required=True)
    ap.add_argument("--capture-type", default="broadcast_panning")
    ap.add_argument("--weapon", default="sabre")
    ap.add_argument("--no-proxy", action="store_true", help="skip the browser proxy (make it later)")
    a = ap.parse_args()
    work = tempfile.mkdtemp(dir=a.data)
    lst = os.path.join(work, "list.txt")
    with open(lst, "w") as f:
        for p in a.parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    joined = os.path.join(work, "joined.webm")
    # -copyts keeps the parts' own (original) timestamps; without it the concat demuxer adds each part's
    # start time on top of the running offset and the joined timeline gets large gaps
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-copyts", "-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0",
                    "-c", "copy", "-copyts", joined], check=True)
    _, rows_j = framemd5(joined)
    orig = [l.strip().split(",") for l in open(a.framemd5) if l.strip() and not l.startswith("#")]
    same_count = len(rows_j) == len(orig)
    same_packets = same_count and all(x[4].strip() == y[4].strip() and x[5].strip() == y[5].strip()
                                      for x, y in zip(rows_j, orig))
    if not same_packets:
        sys.exit(f"VERIFICATION FAILED: packets differ (joined {len(rows_j)} vs original {len(orig)})")
    pts_orig = np.array([int(r[2]) for r in orig], dtype=np.int64)
    s = Store(a.data)
    v = s.add_video(joined, a.title, move=True, capture_type=a.capture_type, weapon_type=a.weapon)
    idx = vid.build_frame_index(v["path"])
    if len(idx["t"]) != len(pts_orig):
        sys.exit(f"decoded frame count {len(idx['t'])} != original packet count {len(pts_orig)}")
    # framemd5 for webm uses a 1/1000 timebase
    t_orig = pts_orig / 1000.0
    order_ok = bool(np.all(np.diff(t_orig) > 0))
    idx_full = {"t": t_orig, "key": idx["key"], "t_play": idx["t"]}
    vid.save_index(idx_full, os.path.join(s.video_dir(v["video_id"]), "frame_index.npz"))
    # finish import like Store.index_video, but keep the original timestamps
    summ = vid.index_summary({"t": t_orig})
    meta = json.load(open(os.path.join(s.video_dir(v["video_id"]), "probe.json")))
    s.db.update("videos", "video_id", v["video_id"], {
        "n_frames": summ["n_frames"], "duration_s": summ["duration_s"], "fps_measured": summ["fps_measured"],
        "vfr": int(summ["vfr"]), "sha256": vid.sha256_file(v["path"])})
    if not a.no_proxy:
        vid.make_browser_proxy(v["path"], os.path.join(s.video_dir(v["video_id"]), "proxy.mp4"))
    prov = {"original_file": a.original_name, "parts": [os.path.basename(p) for p in a.parts],
            "original_framemd5_sha256": hashlib.sha256(open(a.framemd5, "rb").read()).hexdigest(),
            "packets": len(orig), "packet_sizes_and_md5_identical": same_packets,
            "timestamps": "frame_index.t = original packet PTS; t_play = joined file (playback only)",
            "max_playback_offset_ms": float(np.max(np.abs((idx["t"] - idx["t"][0]) - (t_orig - t_orig[0]))) * 1000),
            "monotonic": order_ok}
    with open(os.path.join(s.video_dir(v["video_id"]), "provenance.json"), "w") as f:
        json.dump(prov, f, indent=2)
    with open(os.path.join(s.video_dir(v["video_id"]), "video_meta.json"), "w") as f:
        json.dump({**s.get_video(v["video_id"]), "probe": meta, "index": summ, "provenance": prov}, f, indent=2, default=str)
    os.remove(lst)
    print(json.dumps({"video_id": v["video_id"], **prov, **summ}, default=str))


if __name__ == "__main__":
    main()
