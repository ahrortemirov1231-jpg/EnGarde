#!/usr/bin/env python3
"""QA contact sheet for one touch: exact decoded frames with the tracked boxes, side labels, COCO-17
skeleton (visible joints solid, masked joints hollow), quality flags and the latest prediction per side.

  python scripts/qa_render.py --data ./data --touch tch_xxx --every 8 --out qa.jpg
"""
import argparse
import os
import sys

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde.pipeline import perception as per  # noqa: E402
from engarde.store import Store  # noqa: E402

BONES = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (11, 12), (5, 11), (6, 12), (11, 13), (13, 15), (12, 14), (14, 16)]
COL = {"left": (90, 120, 255), "right": (120, 220, 140)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--touch", required=True)
    ap.add_argument("--every", type=int, default=8)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    s = Store(a.data)
    t = s.get_touch(a.touch)
    sd = s.stage_dir(a.touch)
    tr = pd.read_parquet(os.path.join(sd, "tracks.parquet"))
    pose, frames, _ = per.load_pose_npz(os.path.join(sd, "pose.npz"))
    preds = s.predictions(a.touch)
    times = s.touch_times(a.touch)
    pick = list(range(0, len(frames), a.every))
    li = frames.index(t["light_frame"])
    if li not in pick:
        pick.append(li)
    pick.sort()
    tiles = []
    for i in pick:
        f = frames[i]
        img = cv2.imread(os.path.join(s.frames_dir(a.touch), f"{f:06d}.jpg"))
        for side in ("left", "right"):
            r = tr[(tr.side == side) & (tr.frame == f)].iloc[0]
            c = COL[side]
            if r.present:
                cv2.rectangle(img, (int(r.x1), int(r.y1)), (int(r.x2), int(r.y2)), c, 4)
                cv2.putText(img, f"{side.upper()} id{int(r.track_id)}", (int(r.x1), int(r.y1) - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.6, c, 4)
                flags = [n for n, k in (("BREAK", "track_break"), ("SWITCH?", "id_switch_suspect"),
                                        ("ID?", "identity_uncertain")) if bool(r[k])]
                if flags:
                    cv2.putText(img, " ".join(flags), (int(r.x1), int(r.y2) + 50), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (40, 40, 230), 4)
            else:
                cv2.putText(img, f"{side.upper()}: not detected", (40 if side == "left" else img.shape[1] - 700, img.shape[0] - 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.8, (40, 40, 230), 4)
            kp = pose[side]["raw"][i]
            m = pose[side]["mask"][i]
            for a_, b_ in BONES:
                if m[a_] and m[b_]:
                    cv2.line(img, tuple(int(v) for v in kp[a_, :2]), tuple(int(v) for v in kp[b_, :2]), c, 4)
            for j in range(17):
                if not np.isnan(kp[j, 0]):
                    cv2.circle(img, (int(kp[j, 0]), int(kp[j, 1])), 8, c, -1 if m[j] else 2)
        lab = []
        for side in ("left", "right"):
            p = [x for x in preds if x["side"] == side and x["start_frame"] <= f < x["end_frame"]]
            if p:
                p = p[0]
                lab.append(f"{side[0].upper()}: {'NO_CALL ' + p['no_call_reason'] if p['no_call'] else p['label']}")
        rel = times["rel_ms"][f - t["start_frame"]]
        head = f"frame {f}  {rel:+.0f} ms{'  LIGHT' if f == t['light_frame'] else ''}   " + "   ".join(lab)
        cv2.rectangle(img, (0, 0), (img.shape[1], 90), (20, 20, 20), -1)
        cv2.putText(img, head, (20, 62), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 4)
        sc = a.width / img.shape[1]
        tiles.append(cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA))
    h, w = tiles[0].shape[:2]
    rows = (len(tiles) + a.cols - 1) // a.cols
    sheet = np.full((rows * h, a.cols * w, 3), 255, np.uint8)
    for k, tile in enumerate(tiles):
        r, c = divmod(k, a.cols)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = tile
    cv2.imwrite(a.out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(a.out, sheet.shape)


if __name__ == "__main__":
    main()
