#!/usr/bin/env python3
"""One overlay frame per analysed touch (default -1.0 s before the light) to check that LEFT/RIGHT boxes
are the two fencers.  python scripts/identity_sheet.py --data data --video vid_x --out sheet.jpg"""
import argparse, os, sys
import cv2, numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde.pipeline.perception import add_track_flags  # noqa: E402
from engarde.store import Store  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True); ap.add_argument("--video", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--rel-ms", type=float, default=-1000); ap.add_argument("--first", type=int, default=0); ap.add_argument("--count", type=int, default=48); ap.add_argument("--step", type=int, default=1); ap.add_argument("--status", default=None)
a = ap.parse_args()
s = Store(a.data); v = s.get_video(a.video); cr = v["content_rect"] or [0, 0, v["width"], v["height"]]
COL = {"left": (90, 120, 255), "right": (120, 220, 140)}
tiles = []
for t in s.list_touches(video_id=a.video)[a.first::a.step]:
    if len(tiles) >= a.count:
        break
    tp = os.path.join(s.stage_dir(t["touch_id"]), "tracks.parquet")
    if not os.path.exists(tp):
        continue
    if a.status:
        import json as _j
        st = _j.load(open(os.path.join(s.stage_dir(t["touch_id"]), "manifest.json")))["s2_tracking"]["info"]["selection"].get("status")
        if st != a.status:
            continue
    times = s.touch_times(t["touch_id"]); rel = np.array(times["rel_ms"])
    f = times["frames"][int(np.argmin(np.abs(rel - a.rel_ms)))]
    img = cv2.imread(os.path.join(s.frames_dir(t["touch_id"]), f"{f:06d}.jpg"))
    tr = add_track_flags(pd.read_parquet(tp))
    for side in ("left", "right"):
        r = tr[(tr.side == side) & (tr.frame == f)].iloc[0]
        if r.present:
            c = (0, 0, 255) if r.pair_implausible else COL[side]
            cv2.rectangle(img, (int(r.x1), int(r.y1)), (int(r.x2), int(r.y2)), c, 8)
            cv2.putText(img, side[0].upper() + ("?" if r.identity_uncertain else ""), (int(r.x1), int(r.y1) - 15), cv2.FONT_HERSHEY_SIMPLEX, 4, c, 8)
    img = img[cr[1]:cr[1] + cr[3], cr[0]:cr[0] + cr[2]]
    img = cv2.resize(img, (400, int(400 * img.shape[0] / img.shape[1])))
    cv2.putText(img, f"#{t['seq']}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    tiles.append(img)
h = max(x.shape[0] for x in tiles); tiles = [cv2.resize(x, (400, h)) for x in tiles]
while len(tiles) % 8: tiles.append(np.zeros_like(tiles[0]))
cv2.imwrite(a.out, np.vstack([np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)]), [cv2.IMWRITE_JPEG_QUALITY, 80])
print(a.out, len(tiles))
