#!/usr/bin/env python3
"""Bring Tim's tagged touches (Model comparison page, compare/data.json) into Engarde and compare
our models with his on the same moments.

Steps (run them in order; each one can be re-run safely):

  python scripts/import_tim_data.py check    [--json tim_data/data.json]
      Reads the file, lists bouts and labels, and re-scores Tim's own models with our scorer next to
      the numbers his page publishes (a check that we read the file the way his page does).

  python scripts/import_tim_data.py download [--json tim_data/data.json] [--clips tim_data/clips]
      Downloads data.json (if missing) and every touch clip. Skips files already downloaded.

  python scripts/import_tim_data.py load     [--data data_tim]
      One video per bout (the touch clips joined end to end at 30 fps), one touch per clip, and Tim's
      gold footwork tags as annotations in his 8-class vocabulary. Uses a separate data folder.

  python scripts/import_tim_data.py analyze  [--data data_tim]
      Runs our pipeline (YOLO pose + tracking + features + rule baseline) on every imported touch.
      Uses the GPU automatically when PyTorch can see it.

  Then train XGBoost and/or TCN in the app (start_tim.bat, Insights > Models), with NO frozen test
  bout, so every bout gets a held-out prediction like on Tim's page.

  python scripts/import_tim_data.py compare  [--data data_tim] [--model MODEL_ID | --model rule]
      Scores our model's held-out (out-of-fold) predictions and Tim's models on the same touches,
      same grid, same scorer, and writes data_tim/reports/tim_compare_<model>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from engarde import tim_data as td  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_URL = "https://engardeanalytics.netlify.app/compare/data.json"
DEF_JSON = os.path.join(ROOT, "tim_data", "data.json")
DEF_CLIPS = os.path.join(ROOT, "tim_data", "clips")
DEF_DATA = os.path.join(ROOT, "data_tim")
ANNOTATOR = "tim_gold"
UA = {"User-Agent": "engarde-capstone-importer/0.3"}


# ------------------------------------------------------------------ helpers
def _fetch(url: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dst)


def clip_name(t: td.TimTouch) -> str:
    ext = os.path.splitext(t.clip.split("?")[0])[1] or ".webm"
    return f"{t.bout}_p{int(t.point or 0):03d}_{t.id[:8]}{ext}"


def _surname(name: str) -> str | None:
    """Approximate athlete id from a display name: the all-caps token (USA Fencing style), else the last word."""
    toks = [x.strip("().,") for x in name.split()]
    caps = [x for x in toks if len(x) > 1 and x.isupper()]
    pick = caps[0] if caps else (toks[-1] if toks else None)
    return pick.upper() if pick else None


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg was not found on PATH. Install it (setup does this) and try again.")
    return exe


def _count_frames(path: str) -> int:
    import cv2
    cap = cv2.VideoCapture(path)
    n = 0
    while cap.grab():
        n += 1
    cap.release()
    return n


def _normalise(src: str, dst: str, fps: int = td.GRID_FPS):
    vf = (f"fps={fps},scale=1280:720:force_original_aspect_ratio=decrease,"
          "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1")
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", src, "-vf", vf, "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", dst], check=True)


def _load_touches(path: str):
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Run 'download' first, or save {DATA_URL} there from your browser.")
    return td.load(path)


def _mapping_path(data_dir: str) -> str:
    return os.path.join(data_dir, "tim_import.json")


def _read_mapping(data_dir: str) -> dict:
    p = _mapping_path(data_dir)
    return json.load(open(p)) if os.path.exists(p) else {"bouts": {}, "touches": {}}


def _fmt(r: dict) -> str:
    if not r:
        return "n/a"
    return (f"exact {r['exact']}%  partial {r['partial']}%  wrong {r['wrong']}%  no read {r['no_read']}%"
            f"  | on movement {r.get('exact_on_movement')}%")


KEY_CLASSES = ["LUNGE", "ADVANCE_LUNGE", "STEPS_FORWARD", "STEPS_IN_PLACE", "BACK_FAMILY", "RECOVERING", "STEPS_BACK"]


def _class_table(results: dict):
    """Per-class exact rate (recall) for each model, for the classes that carry the footwork."""
    first = next(iter(results.values()))
    present = [c for c in ["EN_GARDE"] + KEY_CLASSES if c in first.get("class_recall", {})]
    print("\nExact rate by gold movement class (seconds of gold in brackets):")
    print("  " + " " * 30 + "".join(f"{c[:13]:>14s}" for c in present))
    print("  " + " " * 30 + "".join(f"{('(' + str(first['class_recall'][c]['seconds']) + 's)'):>14s}" for c in present))
    for name, r in results.items():
        row = "".join(f"{str(r['class_recall'].get(c, {}).get('exact')) + '%':>14s}" for c in present)
        print(f"  {name[:30]:30s}{row}")


# ------------------------------------------------------------------ commands
def cmd_check(a):
    meta, touches = _load_touches(a.json)
    s = td.summary(touches)
    print(f"File built {meta.get('built')}: {s['touches']} touches in {len(s['bouts'])} bouts")
    for b, v in s["bouts"].items():
        print(f"  {b:10s} {v['touches']:3d} touches  {v['title']}")
    print(f"Gold footwork labels ({len(s['gold_labels'])}): " + ", ".join(f"{k} {v}" for k, v in s["gold_labels"].items()))
    if s["unknown_gold_labels"]:
        print("WARNING: labels not in our mapping (scored as WRONG): " + ", ".join(s["unknown_gold_labels"]))
    ours = td.score_tim_models(touches)
    trivial = td.score(touches, td.constant_predictor("EN_GARDE"))
    pub = td.published_scorecard(meta)
    print("\nTim's models, re-scored with our scorer vs. his page (all touches):")
    for m, r in ours["all"].items():
        p = (pub.get("all_5_bouts") or {}).get(m)
        print(f"  {td.MODEL_NAMES[m]:20s} ours: {_fmt(r)}")
        if p:
            print(f"  {'':20s} page: exact {p['exact']}%  partial {p['partial']}%  wrong {p['wrong']}%  "
                  f"({p['gold_seconds']} gold s)")
    print(f"  {'Always EN_GARDE (trivial)':20s} ours: {_fmt(trivial)}")
    print(f"\n{trivial['class_recall'].get('EN_GARDE', {}).get('seconds')} of {trivial['gold_seconds']} gold seconds are EN_GARDE "
          f"(standing still), so 'exact' is mostly en garde. 'On movement' leaves those moments out.")
    print("Our gold -> 8-class mapping is an assumption until Andre confirms it; small gaps vs. the page are expected.")


def cmd_download(a):
    if not os.path.exists(a.json) or a.refresh:
        print(f"Downloading {a.url} ...")
        _fetch(a.url, a.json)
    meta, touches = _load_touches(a.json)
    os.makedirs(a.clips, exist_ok=True)
    got = skip = fail = 0
    for i, t in enumerate(touches, 1):
        dst = os.path.join(a.clips, clip_name(t))
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            skip += 1
            continue
        try:
            _fetch(t.clip, dst)
            got += 1
            print(f"  [{i}/{len(touches)}] {os.path.basename(dst)}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(touches)}] FAILED {t.clip}: {e}")
    print(f"Clips: {got} downloaded, {skip} already there, {fail} failed -> {a.clips}")
    if fail:
        print("Re-run this step to retry the failed clips.")


def cmd_load(a):
    from engarde.store import Store
    meta, touches = _load_touches(a.json)
    os.makedirs(a.data, exist_ok=True)
    tax_path = os.path.join(a.data, "taxonomy.json")
    if os.path.exists(tax_path) and json.load(open(tax_path)).get("preset") != "tim8":
        sys.exit(f"{tax_path} uses another label set. Use a separate folder for Tim's data (default data_tim).")
    with open(tax_path, "w") as f:
        json.dump({"preset": "tim8", "note": "Tim's 8 footwork classes (Model comparison page)"}, f, indent=2)
    store = Store(a.data)
    mp = _read_mapping(a.data)
    by_bout: dict[str, list[td.TimTouch]] = {}
    for t in touches:
        by_bout.setdefault(t.bout, []).append(t)
    work = os.path.join(a.data, "_tim_work")
    os.makedirs(work, exist_ok=True)
    for bout, ts in by_bout.items():
        if bout in mp["bouts"]:
            print(f"{bout}: already loaded, skipping")
            continue
        ts = sorted(ts, key=lambda x: (x.point or 0, x.abs0))
        missing = [t for t in ts if not os.path.exists(os.path.join(a.clips, clip_name(t)))]
        if missing:
            print(f"{bout}: {len(missing)} clips missing; run 'download' first. Skipping this bout.")
            continue
        print(f"{bout}: joining {len(ts)} clips at {td.GRID_FPS} fps ...")
        parts, offsets, off = [], [], 0
        for t in ts:
            dst = os.path.join(work, os.path.splitext(clip_name(t))[0] + ".mp4")
            if not os.path.exists(dst):
                _normalise(os.path.join(a.clips, clip_name(t)), dst)
            n = _count_frames(dst)
            parts.append(dst)
            offsets.append((off, n))
            off += n
        lst = os.path.join(work, f"{bout}_list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{os.path.abspath(p)}'\n".replace("\\", "/") for p in parts)
        joined = os.path.join(work, f"{bout}.mp4")
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                        joined], check=True)
        v = store.add_video(joined, f"Tim {bout}: {ts[0].title}", move=True, capture_type="broadcast",
                            weapon_type="sabre")
        vm = store.index_video(v["video_id"])
        n_video = vm["index"]["n_frames"]
        if n_video != off:
            print(f"  note: joined video has {n_video} frames, clips add up to {off}; touch ranges are clipped to fit")
        left, right = ts[0].left_name, ts[0].right_name
        b = store.add_bout(v["video_id"], f"{bout} · {ts[0].title}",
                           left={"display_name": left, "athlete_id": _surname(left)},
                           right={"display_name": right, "athlete_id": _surname(right)})
        n_seg = 0
        for t, (o, n) in zip(ts, offsets):
            s, e = o, min(o + n, n_video)
            if e - s < 2:
                print(f"  touch P{t.point}: clip too short after joining, skipped")
                continue
            note = json.dumps({"tim_touch_id": t.id, "tim_bout": t.bout, "tim_point": t.point,
                               "light_frame": "approximate (clip end); Tim's file has no light time"})
            tch = store.add_touch(b["bout_id"], e - 1, notes=note, light_source="manual", start_frame=s, end_frame=e)
            for side, our_side in td.SIDES.items():
                for rs, re_, lab in td.frames_to_runs(td.gold_frames(t, side)):
                    rs, re_ = s + rs, min(s + re_, e)
                    if re_ <= rs:
                        continue
                    store.create_annotation(tch["touch_id"], our_side, rs, re_, lab, ANNOTATOR,
                                            label_source=a.label_source, _op="import")
                    n_seg += 1
            mp["touches"][t.id] = {"touch_id": tch["touch_id"], "bout": bout, "start_frame": s, "end_frame": e}
        mp["bouts"][bout] = {"video_id": v["video_id"], "bout_id": b["bout_id"], "touches": len(ts)}
        json.dump(mp, open(_mapping_path(a.data), "w"), indent=2)
        print(f"  loaded: {len(ts)} touches, {n_seg} gold segments (label_source={a.label_source})")
    print(f"Done. Data folder: {a.data}")


def cmd_analyze(a):
    from engarde.pipeline import runner
    from engarde.store import Store
    store = Store(a.data)
    mp = _read_mapping(a.data)
    if not mp["touches"]:
        sys.exit("No imported touches. Run 'load' first.")
    try:
        import torch
        print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "not available (running on CPU)")
    except Exception:  # noqa: BLE001
        print("GPU: PyTorch not importable")
    items = list(mp["touches"].items())
    ok = fail = 0
    for i, (tim_id, m) in enumerate(items, 1):
        try:
            runner.analyze_touch(store, m["touch_id"], method="rule_baseline", force=a.force)
            ok += 1
            print(f"  [{i}/{len(items)}] {m['bout']} {m['touch_id']} done")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(items)}] {m['bout']} {m['touch_id']} FAILED: {e}")
    print(f"Analysed {ok}, failed {fail}. Next: start_tim.bat, Insights > Models, train with no frozen test bout.")


def _our_predictor(store, mp: dict, model: str):
    """Returns predict(touch, side, t) for our model's held-out predictions, plus a label for reports."""
    if model == "rule":
        segs = {}
        for tim_id, m in mp["touches"].items():
            run = store.latest_run(m["touch_id"], method="rule_baseline")
            rows = store.predictions(m["touch_id"], run["run_id"]) if run else []
            segs[tim_id] = (m, rows)

        def p(t, side, x):
            if t.id not in segs:
                return None
            m, rows = segs[t.id]
            f = m["start_frame"] + int(round(x * td.GRID_FPS))
            for r in rows:
                if r["side"] == td.SIDES[side] and r["start_frame"] <= f < r["end_frame"]:
                    return "no_call" if r.get("no_call") else r["label"]
            return None
        return p, "Our rule baseline"
    oof_p = os.path.join(store.data_dir, "models", f"{model}.oof.json")
    if not os.path.exists(oof_p):
        sys.exit(f"No held-out predictions for model {model} ({oof_p}). Train it with at least 2 bouts and no "
                 "frozen test bout, then try again.")
    oof = json.load(open(oof_p))
    pred = {k: v for k, v in zip(oof["keys"], oof["y_pred"])}
    our_touch = {tim_id: m for tim_id, m in mp["touches"].items()}

    def p(t, side, x):
        m = our_touch.get(t.id)
        if not m:
            return None
        f = m["start_frame"] + int(round(x * td.GRID_FPS))
        return pred.get(f"{m['touch_id']}|{td.SIDES[side]}|{f}")
    meta_p = os.path.join(store.data_dir, "models", f"{model}.meta.json")
    method = json.load(open(meta_p)).get("method", "model") if os.path.exists(meta_p) else "model"
    return p, f"Ours: {method} ({model}), held-out bouts"


def cmd_compare(a):
    from engarde.store import Store
    meta, touches = _load_touches(a.json)
    store = Store(a.data)
    mp = _read_mapping(a.data)
    loaded = [t for t in touches if t.id in mp["touches"]]
    if not loaded:
        sys.exit("No imported touches. Run 'load' (and 'analyze', and train a model) first.")
    model = a.model
    if model == "latest":
        rows = store.db.q("SELECT model_id FROM models ORDER BY created_at DESC LIMIT 1")
        if not rows:
            sys.exit("No trained model yet. Train one in the app, or use --model rule.")
        model = rows[0]["model_id"]
    pred, name = _our_predictor(store, mp, model)
    results = {name: td.score(loaded, pred)}
    for m in td.TIM_MODELS:
        if any(t.lanes.get(m) for t in loaded):
            results[td.MODEL_NAMES[m]] = td.score(loaded, td.lane_predictor(m))
    results["Always EN_GARDE (trivial)"] = td.score(loaded, td.constant_predictor("EN_GARDE"))
    print(f"Same {len(loaded)} touches, same 1/{td.GRID_FPS} s grid, same scorer:")
    for k, r in results.items():
        print(f"  {k:42s} {_fmt(r)}")
    _class_table({k: v for k, v in results.items() if not k.startswith("Existing")})
    print("Tim's lanes are his held-out-bout predictions; ours are out-of-fold. Neither is a sealed test.")
    os.makedirs(os.path.join(a.data, "reports"), exist_ok=True)
    out = os.path.join(a.data, "reports", f"tim_compare_{model}.json")
    json.dump({"data_file_built": meta.get("built"), "touches": len(loaded), "grid_fps": td.GRID_FPS,
               "mapping_note": "gold -> 8-class mapping is our assumption (tim_data.GOLD_TO_TIM8)",
               "results": results}, open(out, "w"), indent=2)
    print(f"Report: {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "download", "load", "analyze", "compare"):
        sp = sub.add_parser(name)
        sp.add_argument("--json", default=DEF_JSON)
        sp.add_argument("--clips", default=DEF_CLIPS)
        sp.add_argument("--data", default=DEF_DATA)
        if name == "download":
            sp.add_argument("--url", default=DATA_URL)
            sp.add_argument("--refresh", action="store_true", help="download data.json again even if present")
        if name == "load":
            sp.add_argument("--label-source", default="adjudicated", choices=["adjudicated", "blind", "unknown"],
                            help="how Tim's tags are recorded; 'adjudicated' (expert final labels) by default")
        if name == "analyze":
            sp.add_argument("--force", action="store_true")
        if name == "compare":
            sp.add_argument("--model", default="latest", help="model id, 'latest', or 'rule'")
    a = ap.parse_args(argv)
    {"check": cmd_check, "download": cmd_download, "load": cmd_load, "analyze": cmd_analyze,
     "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    main()
