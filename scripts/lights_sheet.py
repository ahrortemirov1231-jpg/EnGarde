#!/usr/bin/env python3
"""Contact sheet of every touch's light frame (+ the frame 0.5 s later) for verifying light candidates.
  python scripts/lights_sheet.py --data data --video vid_xxx --out lights.jpg [--first 0 --count 24]
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--w", type=int, default=420)
    a = ap.parse_args()
    s = Store(a.data)
    idx = s.frame_index(a.video)
    tiles = []
    for t in s.list_touches(video_id=a.video)[a.first:a.first + a.count]:
        row = []
        for f in (t["light_frame"], t["end_frame"] - 1):
            p = os.path.join(s.frames_dir(t["touch_id"]), f"{f:06d}.jpg")
            img = cv2.imread(p) if os.path.exists(p) else np.zeros((600, 1400, 3), np.uint8)
            h = img.shape[0]
            img = img[int(0.05 * h):int(0.92 * h)]
            img = cv2.resize(img, (a.w, int(img.shape[0] * a.w / img.shape[1])))
            row.append(img)
        tile = np.hstack(row)
        lab = (f"#{t['seq']} f{t['light_frame']} {idx['t'][t['light_frame']]:.2f}s "
               f"L{t['lamp_left']} R{t['lamp_right']}")
        cv2.rectangle(tile, (0, 0), (tile.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(tile, lab, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)
    cols = 3
    hmax = max(x.shape[0] for x in tiles)
    tiles = [np.vstack([x, np.zeros((hmax - x.shape[0], x.shape[1], 3), np.uint8)]) for x in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    cv2.imwrite(a.out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    print(a.out, sheet.shape)


if __name__ == "__main__":
    main()
