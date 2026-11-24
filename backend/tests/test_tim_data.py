"""Importer and scorer for Tim's Model comparison data (compare/data.json).
Uses a small hand-made file in the same format; the first touch copies the real file's first touch."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from engarde import taxonomy as tx
from engarde import tim_data as td
from engarde.store import Store

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import import_tim_data as imp  # noqa: E402

T0 = {  # first touch of the real file (bout rec-62), lanes trimmed to xgb/tcn/lgb as published
    "id": "b4010fcd-7b0d-4cea-b28d-000854d44511", "bout": "rec-62", "title": "Colin HEATHCOCK vs SZABO Matyas",
    "point": 1, "clip": "https://example.invalid/62/p1.mp4", "abs0": 5.41, "dur": 5.37, "existing_engine": None,
    "gold": [["L", 0.26, 3.75, "EN_GARDE"], ["R", 0.26, 4.03, "EN_GARDE"], ["R", 4.43, 4.71, "LUNGE"]],
    "intent": [["L", 3.79, 4.39, "PREPARATION_STEPS_INTENT"]],
    "lanes": {"existing": {"L": [], "R": []},
              "xgb": {"L": [[0, 3.77, "EN_GARDE", 0.99], [3.77, 3.87, "STEPS_IN_PLACE", 0.93],
                            [3.87, 4.57, "STEPS_FORWARD", 0.93], [4.57, 4.97, "BACK_FAMILY", 0.78],
                            [4.97, 5.4, "STEPS_BACK", 0.67]],
                      "R": [[0, 3.77, "EN_GARDE", 1], [3.77, 3.87, "STEPS_IN_PLACE", 0.9],
                            [3.87, 4.33, "STEPS_FORWARD", 0.9], [4.33, 4.83, "LUNGE", 0.92],
                            [4.83, 5.4, "RECOVERING", 0.93]]}},
    "disagree": {"existing": None, "xgb": 4, "tcn": 4}}
T1 = {"id": "aaaa1111-0000", "bout": "rec-62", "title": "Colin HEATHCOCK vs SZABO Matyas", "point": 2,
      "clip": "https://example.invalid/62/p2.mp4", "abs0": 30.0, "dur": 3.0, "existing_engine": None,
      "gold": [["L", 0.0, 1.0, "ADVANCE"], ["L", 0.5, 2.0, "ADVANCE_LUNGE"], ["R", 0.0, 3.0, "REPRISE_PARRY"]],
      "intent": [], "lanes": {"xgb": {"L": [[0, 3, "STEPS_FORWARD", 0.8]], "R": [[0, 3, "EN_GARDE", 0.9]]}}}
T2 = {"id": "bbbb2222-0000", "bout": "rec-85", "title": "MORRILL vs HRYCIUK S", "point": 1,
      "clip": "https://example.invalid/85/p1.mp4", "abs0": 2.0, "dur": 2.0, "existing_engine": "clf3",
      "gold": [["L", 0.0, 2.0, "LUNGE"], ["R", 0.0, 2.0, "PULL_ATTEMPT"]], "intent": [],
      "lanes": {"existing": {"L": [[0, 2, "LUNGE", 0.9]], "R": []},
                "xgb": {"L": [[0, 2, "ADVANCE_LUNGE", 0.7]], "R": [[0, 2, "STEPS_BACK", 0.6]]}}}


@pytest.fixture()
def data_file(tmp_path):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"built": "2026-11-11", "scorecard": {"all_5_bouts": {"xgb": {
        "gold_seconds": 1.0, "exact": 1, "partial": 0, "wrong": 0, "no_read": 0}}}, "locks": {},
        "touches": [T0, T1, T2]}))
    return str(p)


def test_load_and_summary(data_file):
    meta, ts = td.load(data_file)
    assert meta["built"] == "2026-11-11" and len(ts) == 3
    s = td.summary(ts)
    assert s["bouts"]["rec-62"]["touches"] == 2 and s["unknown_gold_labels"] == []
    assert ts[2].left_name == "MORRILL" and ts[2].right_name == "HRYCIUK S"


def test_bad_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"nope": 1}))
    with pytest.raises(td.TimDataError):
        td.load(str(p))


def test_gold_frames_last_tag_wins_and_runs(data_file):
    _, ts = td.load(data_file)
    t1 = ts[1]
    assert td.n_frames(t1) == 90
    lab = td.gold_frames(t1, "L")
    assert lab[0] == "STEPS_FORWARD"            # ADVANCE alone
    assert lab[20] == "ADVANCE_LUNGE"           # overlap 0.5-1.0: the later tag wins
    assert lab[70] is None                      # untagged after 2.0 s
    assert set(td.gold_frames(t1, "R")) == {None}   # blade-only tag is not footwork
    runs = td.frames_to_runs(lab)
    assert runs[0] == (0, 15, "STEPS_FORWARD") and runs[1][2] == "ADVANCE_LUNGE"


def test_score_categories(data_file):
    _, ts = td.load(data_file)
    t2 = [ts[2]]
    perfect = td.score(t2, lambda t, s, x: td.to_tim8(td.gold_at(t, s, x)))
    assert perfect["exact"] == 100.0 and perfect["gold_seconds"] == 4.0
    fam = td.score(t2, lambda t, s, x: "ADVANCE_LUNGE" if s == "L" else "STEPS_BACK")
    assert fam["exact"] == 0.0 and fam["partial"] == 100.0       # same family both sides
    none = td.score(t2, lambda t, s, x: None)
    assert none["no_read"] == 100.0
    ours4 = td.score(t2, lambda t, s, x: "Lunge" if s == "L" else "Retreat")
    assert ours4["exact"] == 50.0 and ours4["partial"] == 50.0   # 4-class labels are understood
    blade = td.score([ts[1]], lambda t, s, x: "EN_GARDE")
    assert blade["wrong"] > 0                                     # blade-only gold scores WRONG for everyone
    t0 = [ts[0]]
    triv = td.score(t0, td.constant_predictor("EN_GARDE"))
    assert triv["exact"] > 80 and triv["exact_on_movement"] == 0.0   # still-heavy touch: exact is free, movement is not
    assert triv["class_recall"]["EN_GARDE"]["exact"] == 100.0 and triv["class_recall"]["LUNGE"]["exact"] == 0.0


def test_score_tim_models(data_file):
    meta, ts = td.load(data_file)
    r = td.score_tim_models(ts)
    assert set(r["all"]) >= {"xgb", "existing"}
    assert r["where_existing_has_reads"]["existing"]["exact"] == 50.0
    assert td.published_scorecard(meta)["all_5_bouts"]["xgb"]["exact"] == 1


def test_taxonomy_preset(tmp_path):
    (tmp_path / "taxonomy.json").write_text(json.dumps({"preset": "tim8"}))
    assert tx.load_taxonomy(str(tmp_path)).footwork == tx.TIM8_FOOTWORK == td.TIM8
    (tmp_path / "taxonomy.json").write_text(json.dumps({"preset": "nope"}))
    with pytest.raises(ValueError):
        tx.load_taxonomy(str(tmp_path))
    (tmp_path / "taxonomy.json").write_text(json.dumps({"enabled_extra": ["Hop"]}))
    assert tx.load_taxonomy(str(tmp_path)).footwork == tx.DEFAULT_FOOTWORK + ["Hop"]


def test_check_command(data_file, capsys):
    imp.main(["check", "--json", data_file])
    out = capsys.readouterr().out
    assert "3 touches in 2 bouts" in out and "XGBoost" in out


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_load_and_compare_end_to_end(data_file, tmp_path, capsys):
    _, ts = td.load(data_file)
    clips = tmp_path / "clips"
    clips.mkdir()
    for t in ts:   # synthetic stand-in clips at 25 fps (the importer resamples to 30)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc=size=320x240:rate=25:duration={t.dur}", "-pix_fmt", "yuv420p",
                        str(clips / imp.clip_name(t))], check=True)
    data = tmp_path / "data_tim"
    imp.main(["load", "--json", data_file, "--clips", str(clips), "--data", str(data)])
    s = Store(str(data))
    assert s.tax.footwork == td.TIM8
    mp = json.load(open(data / "tim_import.json"))
    assert set(mp["bouts"]) == {"rec-62", "rec-85"} and len(mp["touches"]) == 3
    m0 = mp["touches"][T0["id"]]
    anns = s.annotations(m0["touch_id"])
    assert anns and {a["label"] for a in anns} <= set(td.TIM8)
    assert {a["label_source"] for a in anns} == {"adjudicated"}
    assert m0["end_frame"] - m0["start_frame"] in (161, 162)          # 5.37 s at 30 fps
    imp.main(["load", "--json", data_file, "--clips", str(clips), "--data", str(data)])   # idempotent
    assert len(s.list_touches()) == 3
    # fake held-out predictions that equal the gold tags -> 100% exact for "ours"
    keys, preds = [], []
    for t in ts:
        m = mp["touches"][t.id]
        for side, our in td.SIDES.items():
            for i, lab in enumerate(td.gold_frames(t, side)):
                f = m["start_frame"] + i
                if f < m["end_frame"] and lab:
                    keys.append(f"{m['touch_id']}|{our}|{f}")
                    preds.append(lab)
    os.makedirs(data / "models", exist_ok=True)
    json.dump({"keys": keys, "y_pred": preds}, open(data / "models" / "fake.oof.json", "w"))
    capsys.readouterr()
    imp.main(["compare", "--json", data_file, "--data", str(data), "--model", "fake"])
    rep = json.load(open(data / "reports" / "tim_compare_fake.json"))
    ours = [v for k, v in rep["results"].items() if k.startswith("Ours")][0]
    # every footwork moment is exact; the only gap is the blade-only gold tag (no footwork label to predict)
    assert ours["wrong"] == 0.0 and ours["partial"] == 0.0 and ours["exact"] > 80
    assert "XGBoost" in rep["results"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_tim_bouts_into_team_workspace(data_file, tmp_path):
    """v0.5.1: the benchmark folder's bouts are copied into a team folder with Tim's ten label names,
    straight from the raw tags, with the finished analysis and without touching the benchmark folder."""
    import team_add_tim_bouts as add
    from engarde.pipeline import runner
    _, ts = td.load(data_file)
    clips = tmp_path / "clips"
    clips.mkdir()
    for t in ts:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc=size=320x240:rate=25:duration={t.dur}", "-pix_fmt", "yuv420p",
                        str(clips / imp.clip_name(t))], check=True)
    src, dst = tmp_path / "data_tim", tmp_path / "team"
    imp.main(["load", "--json", data_file, "--clips", str(clips), "--data", str(src)])
    s = Store(str(src))
    mp = json.load(open(src / "tim_import.json"))
    first = mp["touches"][T0["id"]]["touch_id"]
    runner.analyze_touch(s, first)                                   # one touch has finished analysis
    before = {t["touch_id"]: [(a["label"], a["start_frame"]) for a in s.annotations(t["touch_id"])] for t in s.list_touches()}
    out = add.add_bouts(str(src), data_file, str(dst), log=lambda *_: None)
    d = Store(str(dst))
    assert out["bouts_added"] == 2 and out["touches"] == 3 and len(d.list_videos()) == 2 and len(d.list_touches()) == 3
    labs = {}
    for t in d.list_touches():
        for a in d.annotations(t["touch_id"]):
            labs.setdefault(a["label"], set()).add(a["label_version"])
            assert a["label_source"] == "adjudicated" and a["annotator_id"] == "tim_gold"
    assert set(labs) <= set(d.tax.footwork)
    # raw tags that the 8-class benchmark folder had to drop or merge are kept here, each with its original tag
    assert labs["Reprise attack"] == {"tim10 (Tim tag: REPRISE_PARRY)"}
    assert labs["Pull/retreat"] == {"tim10 (Tim tag: PULL_ATTEMPT)"}
    assert "tim10 (Tim tag: ADVANCE)" in labs["Step forward"] and "Advance → lunge" in labs
    # the finished analysis came along: first guesses show, in the team's label names, and frames are re-made on demand
    preds = d.predictions(first)
    assert preds and {p["label"] for p in preds} - {"none"} <= set(d.tax.footwork)
    assert os.path.exists(os.path.join(d.stage_dir(first), "features.parquet"))
    v = d.get_video(d.get_touch(first)["video_id"])
    assert v["linked"] and v["path"].startswith(str(dst)) and v["split"] is None
    runner.prepare_touch(d, first)
    assert len(os.listdir(d.frames_dir(first))) == d.get_touch(first)["end_frame"] - d.get_touch(first)["start_frame"]
    # twice = no change; the benchmark folder is untouched
    again = add.add_bouts(str(src), data_file, str(dst), log=lambda *_: None)
    assert again["bouts_added"] == 0 and again["bouts_already_there"] == 2 and len(d.list_touches()) == 3
    s2 = Store(str(src))
    assert s2.tax.footwork == td.TIM8
    assert {t["touch_id"]: [(a["label"], a["start_frame"]) for a in s2.annotations(t["touch_id"])] for t in s2.list_touches()} == before
    # and the labels can train a model in the team folder
    assert d.db.one("SELECT COUNT(*) n FROM audit_log WHERE action='import_tim_bouts'")["n"] == 2
