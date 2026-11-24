#!/usr/bin/env python3
"""Bring Tim's tagged bouts (the 5 bouts / 164 touches from Andre's data.json) into the team workspace.

  python scripts/team_add_tim_bouts.py --src data_tim --json tim_data/data.json --dst data

What it does, per bout:
  * copies the joined video and its finished analysis (tracking, pose, features, rule guesses) from the
    benchmark folder (--src) into the team folder (--dst): nothing is analysed again;
  * writes Tim's tags as human labels under his ten label names, straight from data.json, and keeps the
    raw tag on each label ("Tim tag: HALF_JUMP"), label_source = adjudicated, annotator = tim_gold.
The benchmark folder is only read: compare_tim.bat keeps working on it. Running this twice changes nothing.
Close the team server first (the .bat checks).
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))

from engarde import tim_data as td  # noqa: E402
from engarde.store import Store  # noqa: E402

ANNOTATOR = "tim_gold"


def _copy_rows(src, dst, table, where, params):
    cols = [r["name"] for r in dst.db.q(f"PRAGMA table_info({table})")]
    n = 0
    for row in src.db.q(f"SELECT * FROM {table} WHERE {where}", params):
        dst.db.insert(table, {k: row[k] for k in cols if k in row})
        n += 1
    return n


def add_bouts(src_dir, json_path, dst_dir, log=print) -> dict:
    mp_path = os.path.join(src_dir, "tim_import.json")
    if not os.path.exists(mp_path):
        raise SystemExit(f"{src_dir} is not a folder made by tim_data.bat (no tim_import.json).")
    mp = json.load(open(mp_path, encoding="utf-8"))
    _, touches = td.load(json_path)
    by_id = {t.id: t for t in touches}
    dst = Store(dst_dir)
    if dst.tax.preset != "tim10":
        raise SystemExit(f"{dst_dir} does not use Tim's ten label names (it is set to '{dst.tax.preset}').")
    src = Store(src_dir)
    out = {"bouts_added": 0, "bouts_already_there": 0, "touches": 0, "labels": 0, "tags_without_label": {},
           "labels_by_name": {}, "videos": []}
    for bout, b in mp["bouts"].items():
        vid, bout_id = b["video_id"], b["bout_id"]
        if dst.db.one("SELECT 1 x FROM videos WHERE video_id=?", (vid,)):
            out["bouts_already_there"] += 1
            log(f"{bout}: already in the team workspace, skipped")
            continue
        v = src.db.one("SELECT * FROM videos WHERE video_id=?", (vid,))
        if not v or not os.path.isdir(src.video_dir(vid)):
            log(f"{bout}: not found in {src_dir}, skipped")
            continue
        log(f"{bout}: copying video and analysis ...")
        shutil.copytree(src.video_dir(vid), dst.video_dir(vid), dirs_exist_ok=True)
        tids = [r["touch_id"] for r in src.db.q("SELECT touch_id FROM touches WHERE video_id=? ORDER BY light_frame", (vid,))]
        for tid in tids:                                   # analysis outputs; exact frames are re-made on demand
            sd = src.stage_dir(tid)
            if os.path.isdir(sd):
                shutil.copytree(sd, dst.stage_dir(tid), dirs_exist_ok=True)
        with dst.db.transaction():
            row = dict(v)
            row["path"] = os.path.join(dst.video_dir(vid), os.path.basename(v["path"]))
            row.update(split=None, locked=0, locked_by=None, locked_at=None)
            cols = [r["name"] for r in dst.db.q("PRAGMA table_info(videos)")]
            dst.db.insert("videos", {k: row[k] for k in cols if k in row})
            _copy_rows(src, dst, "bouts", "video_id=?", (vid,))
            _copy_rows(src, dst, "fencers", "bout_id=?", (bout_id,))
            _copy_rows(src, dst, "touches", "video_id=?", (vid,))
            for tid in tids:
                _copy_rows(src, dst, "prediction_runs", "touch_id=?", (tid,))
                _copy_rows(src, dst, "action_segments", "touch_id=? AND kind='prediction'", (tid,))
                _copy_rows(src, dst, "events", "touch_id=? AND source != 'expert'", (tid,))
        n_lab = 0
        for uuid, m in mp["touches"].items():
            if m["bout"] != bout or uuid not in by_id:
                continue
            t = by_id[uuid]
            s, e = m["start_frame"], m["end_frame"]
            for g in t.gold:                               # count what could not be given one of the ten names
                if td.GOLD_TO_TEN.get(g[3]) is None:
                    out["tags_without_label"][g[3]] = out["tags_without_label"].get(g[3], 0) + 1
            for side, our in td.SIDES.items():
                for rs, re_, lab, raw in td.gold_runs_ten(t, side):
                    a, b_ = s + rs, min(s + re_, e)
                    if b_ <= a:
                        continue
                    dst.create_annotation(m["touch_id"], our, a, b_, lab, ANNOTATOR, label_source="adjudicated",
                                          label_version=f"tim10 (Tim tag: {raw})", _op="import")
                    out["labels_by_name"][lab] = out["labels_by_name"].get(lab, 0) + 1
                    n_lab += 1
        out["bouts_added"] += 1
        out["touches"] += len(tids)
        out["labels"] += n_lab
        out["videos"].append({"bout": bout, "title": v["title"], "touches": len(tids), "labels": n_lab})
        log(f"  added: {len(tids)} touches, {n_lab} labels")
    dst.audit("system:team_add_tim_bouts", "import_tim_bouts", "data.json",
              {k: out[k] for k in ("bouts_added", "touches", "labels")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "data_tim"))
    ap.add_argument("--json", default=os.path.join(ROOT, "tim_data", "data.json"))
    ap.add_argument("--dst", default=os.path.join(ROOT, "data"))
    a = ap.parse_args(argv)
    for p, what in ((a.src, "benchmark folder (data_tim)"), (a.json, "Tim's data.json")):
        if not os.path.exists(p):
            raise SystemExit(f"Cannot find the {what}: {p}")
    out = add_bouts(a.src, a.json, a.dst)
    print()
    print(f"Added {out['bouts_added']} bouts, {out['touches']} touches, {out['labels']} labels "
          f"({out['bouts_already_there']} bouts were already there).")
    if out["labels_by_name"]:
        print("Labels by name: " + ", ".join(f"{k} {v}" for k, v in sorted(out["labels_by_name"].items(), key=lambda x: -x[1])))
    if out["tags_without_label"]:
        print("Blade-only tags left without a label: " + ", ".join(f"{k} {v}" for k, v in out["tags_without_label"].items()))
    return out


if __name__ == "__main__":
    main()
