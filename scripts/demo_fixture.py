#!/usr/bin/env python3
"""Load the SYNTHETIC fixture into a data dir and run the full loop once (no server needed).

  python scripts/demo_fixture.py --data ./data [--pose yolo|import] [--with-labels] [--bouts N] [--blind]

--pose import : use the fixture's ground-truth skeleton (tests the 'reuse existing skeletons' path)
--pose yolo   : run YOLO11n-pose + ByteTrack on the rendered frames (shows real model status on
                stick figures — expected to be poor; that is a property of the fixture, not a result)
--with-labels : also load the scripted labels as annotations (label_source='unknown', annotator
                'synthetic_fixture'), so training/export can be exercised.
--bouts N     : load the fixture as N separate bouts with different athletes (default 1). Training E1/E2
                with leave-one-bout-out CV needs at least 3; the calibration report needs 3 development
                bouts plus a frozen test bout, so use 5 to try every panel on Insights > Models.
--blind       : mark the loaded labels as label_source='blind', so a bout can be used as a frozen test
                bout (the firewall only scores blind or adjudicated labels).
All bouts reuse the same synthetic clip, so model scores on this demo are close to perfect by
construction. They test that the software works, not how well it recognises real fencing.
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from engarde.fixture import make_fixture  # noqa: E402
from engarde.pipeline import runner  # noqa: E402
from engarde.store import Store  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    ap.add_argument("--pose", default="import", choices=["import", "yolo"])
    ap.add_argument("--with-labels", action="store_true")
    ap.add_argument("--bouts", type=int, default=1)
    ap.add_argument("--blind", action="store_true")
    a = ap.parse_args()
    store = Store(a.data)
    fx = make_fixture(os.path.join(store.data_dir, "fixture_src"))
    labels = json.load(open(fx["labels"]))
    source = "blind" if a.blind else "unknown"
    out = {"bouts": []}
    for k in range(a.bouts):
        tag = "" if a.bouts == 1 else f" {k + 1}"
        v = store.add_video(fx["video"], f"SYNTHETIC fixture{tag} — two stick figures", capture_type="fixed",
                            weapon_type="synthetic", is_synthetic=True)
        store.index_video(v["video_id"])
        if a.pose == "import":
            shutil.copy(fx["skeleton"], os.path.join(store.video_dir(v["video_id"]), "imported_skeleton.json"))
        L_id, R_id = (f"SYNTH-L{k + 1}", f"SYNTH-R{k + 1}") if a.bouts > 1 else ("SYNTH-L", "SYNTH-R")
        b = store.add_bout(v["video_id"], f"Synthetic bout{tag}",
                           left={"athlete_id": L_id, "display_name": f"Synthetic L{tag}"},
                           right={"athlete_id": R_id, "display_name": f"Synthetic R{tag}"})
        rec = {"video_id": v["video_id"], "bout_id": b["bout_id"], "touches": []}
        for L in labels["lights"]:
            t = store.add_touch(b["bout_id"], L["frame"], L["lamp_left"], L["lamp_right"], notes="synthetic")
            res = runner.analyze_touch(store, t["touch_id"], pose_source="auto" if a.pose == "import" else "yolo",
                                       report=lambda p, s=None, m=None: None)
            if a.with_labels:
                for side, segs in labels["segments"].items():
                    for sg in segs:
                        lo, hi = max(sg["start_frame"], t["start_frame"]), min(sg["end_frame"], t["end_frame"])
                        if hi > lo:
                            store.create_annotation(t["touch_id"], side, lo, hi, sg["label"], "synthetic_fixture",
                                                    label_source=source, _op="import")
            rec["touches"].append({"touch_id": t["touch_id"], "window": [t["start_frame"], t["end_frame"]],
                                   "clipped": bool(t["window_clipped"]), **res})
        out["bouts"].append(rec)
        print(f"loaded synthetic bout {k + 1} of {a.bouts}", file=sys.stderr)
    if a.bouts == 1:
        out = {**out["bouts"][0]}
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
