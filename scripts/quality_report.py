#!/usr/bin/env python3
"""Aggregate tracking / pose / quality-gate statistics over all touches of a video.
  python scripts/quality_report.py --data data --video vid_x --prefix data/reports/name"""
import argparse, collections, json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engarde.store import Store  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--video", required=True); ap.add_argument("--prefix", required=True)
a = ap.parse_args()
s = Store(a.data); T = s.list_touches(video_id=a.video)
rows = []; lab = collections.Counter(); rsn = collections.Counter(); tot = 0
for t in T:
    m = json.load(open(os.path.join(s.stage_dir(t["touch_id"]), "manifest.json")))
    q = m["s2_tracking"]["info"]["quality"]; f4 = m["s4_features"]["info"]; sel = m["s2_tracking"]["info"]["selection"]
    F = pd.read_parquet(os.path.join(s.stage_dir(t["touch_id"]), "features.parquet"))
    preds = s.predictions(t["touch_id"])
    for p in preds:
        n = p["end_frame"] - p["start_frame"]; tot += n
        if p["no_call"]:
            lab["no_call"] += n
            for x in p["no_call_reason"].split(","): rsn[x] += n
        else:
            lab[p["label"]] += n
    L = F[F.side == "left"]
    rows.append(dict(seq=t["seq"], light_frame=t["light_frame"], t_s=round(t["light_time_s"], 2), lamp_L=t["lamp_left"], lamp_R=t["lamp_right"],
                     selection=sel.get("status"), present_L=round(q["left"]["present_frac"], 2), present_R=round(q["right"]["present_frac"], 2),
                     kp_vis_L=round(q["left"]["kp_visible_frac"], 2), kp_vis_R=round(q["right"]["kp_visible_frac"], 2),
                     track_breaks=q["left"]["track_breaks"] + q["right"]["track_breaks"],
                     id_switch=q["left"]["id_switch_suspect"] + q["right"]["id_switch_suspect"],
                     pair_implausible_frames=int(L.pair_implausible.sum()), identity_uncertain_frames=int(L.identity_uncertain.sum()),
                     cuts=len(f4["camera_cut_frames"]), dup_frames=f4["duplicate_frames"],
                     n_seg=len(preds), n_nocall=sum(p["no_call"] for p in preds), run_id=s.latest_run(t["touch_id"])["run_id"]))
df = pd.DataFrame(rows); df.to_csv(a.prefix + "_quality.csv", index=False)
summ = {"touches": len(T), "fencer_frames": tot,
        "label_share": {k: round(v / tot, 3) for k, v in lab.most_common()},
        "no_call_reason_share": {k: round(v / tot, 3) for k, v in rsn.most_common()},
        "presence_median": [float(df.present_L.median()), float(df.present_R.median())],
        "kp_visible_median": [float(df.kp_vis_L.median()), float(df.kp_vis_R.median())],
        "touches_two_fencers": int((df.selection == "two_fencers").sum()),
        "touches_with_cut": int((df.cuts > 0).sum()),
        "touches_with_pair_implausible": int((df.pair_implausible_frames > 0).sum()),
        "id_switch_suspects_total": int(df.id_switch.sum()), "track_breaks_total": int(df.track_breaks.sum()),
        "touches_all_no_call": int((df.n_seg == df.n_nocall).sum()),
        "touches_with_any_label": int((df.n_seg > df.n_nocall).sum())}
json.dump(summ, open(a.prefix + "_summary.json", "w"), indent=2)
print(json.dumps(summ, indent=1))
