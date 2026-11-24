"""v0.4.0: Tim's label names, touches in time order, delete video, lock, train/test split, shared
labels (import), small-data training. SYNTHETIC fixture only."""
import json
import os
import shutil
import sqlite3

import pytest

from engarde import exports, ml
from engarde import taxonomy as tx
from engarde.fixture import make_fixture
from engarde.pipeline import runner
from engarde.store import Conflict, Locked, NotFound, Store

TIM10 = ["Start of touch", "En garde", "Steps in place", "Step forward", "Step back", "Advance → lunge", "Lunge",
         "Between touches", "Reprise attack", "Pull/retreat", "No touch", "End of touch"]


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return make_fixture(str(tmp_path_factory.mktemp("fx4")))


def _bout(s, fx, name="bout", athletes=("A1", "A2"), lights=(150,), labels=True):
    v = s.add_video(fx["video"], name, is_synthetic=True)
    s.index_video(v["video_id"])
    shutil.copy(fx["skeleton"], os.path.join(s.video_dir(v["video_id"]), "imported_skeleton.json"))
    b = s.add_bout(v["video_id"], name, left={"athlete_id": athletes[0]}, right={"athlete_id": athletes[1]})
    lab = json.load(open(fx["labels"]))
    out = []
    for lf in lights:
        t = s.add_touch(b["bout_id"], lf, 1, 0)
        runner.analyze_touch(s, t["touch_id"])
        if labels:
            for side in ("left", "right"):
                for g in lab["segments"][side]:
                    a, e, name_ = g["start_frame"], g["end_frame"], g["label"]
                    a2, e2 = max(a, t["start_frame"]), min(e, t["end_frame"])
                    if e2 > a2:
                        s.create_annotation(t["touch_id"], side, a2, e2, name_, "tester", label_source="blind")
        out.append(t)
    return v, b, out


# ---------------------------------------------------------------- labels
def test_default_labels_are_tims_ten(tmp_path):
    t = Store(str(tmp_path / "d")).tax
    assert t.preset == "tim10"
    assert sorted(t.footwork + t.markers) == sorted(TIM10)
    # old names, Tim's JSON tags and ASCII spellings all land on the same labels
    assert t.canon("Advance") == "Step forward" and t.canon("Retreat") == "Pull/retreat"
    assert t.canon("Other/Still") == "En garde" and t.canon("Lunge") == "Lunge"
    assert t.canon("ADVANCE_LUNGE") == t.canon("advance -> lunge") == "Advance → lunge"
    assert t.canon("something else") == "something else"       # unknown stays unknown (and is rejected)
    assert tx.slug("Advance → lunge") == "advance-lunge" and tx.slug("Pull/retreat") == "pull-retreat"


def test_old_folder_settings_still_work(tmp_path):
    (tmp_path / "taxonomy.json").write_text(json.dumps({"preset": "mvp4"}))
    assert tx.load_taxonomy(str(tmp_path)).footwork == tx.DEFAULT_FOOTWORK
    (tmp_path / "taxonomy.json").write_text(json.dumps({"enabled_extra": ["Hop"]}))
    assert tx.load_taxonomy(str(tmp_path)).footwork == tx.DEFAULT_FOOTWORK + ["Hop"]
    (tmp_path / "taxonomy.json").write_text(json.dumps({"preset": "tim8"}))
    assert tx.load_taxonomy(str(tmp_path)).footwork == tx.TIM8_FOOTWORK


def test_rule_guesses_and_new_labels_use_tims_names(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    v, b, (t,) = _bout(s, fx)
    names = {p["label"] for p in s.predictions(t["touch_id"])} - {"none"}
    assert names and names <= set(s.tax.footwork), names
    assert {a["label"] for a in s.annotations(t["touch_id"])} <= set(s.tax.annotation_labels)
    with pytest.raises(Exception):
        s.create_annotation(t["touch_id"], "left", 60, 62, "Flying sidekick", "x", replace_overlaps=True)
    # the blind AIRS timeline never prints a tactical word, whatever the label set
    txt = exports.airs_timeline(s, t["touch_id"])["timeline_txt"].lower()
    assert "reprise" not in txt and "attack" not in txt


def test_upgrade_renames_old_labels_and_keeps_history(tmp_path, fx):
    d = tmp_path / "d"
    (d).mkdir()
    (d / "taxonomy.json").write_text(json.dumps({"preset": "mvp4"}))      # a data folder made by v0.3.0
    s = Store(str(d))
    v, b, (t,) = _bout(s, fx)
    before = [(a["segment_id"], a["label"]) for a in s.annotations(t["touch_id"])]
    assert {l for _, l in before} <= set(tx.DEFAULT_FOOTWORK)
    s.db.conn.execute("DROP TABLE app_flags")                              # ... v0.3.0 had no such table
    s.db.conn.close()
    os.remove(d / "taxonomy.json")
    s2 = Store(str(d))                                                     # first start of v0.4.0
    assert s2.label_migration["renamed"] == len([1 for _, l in before if l != "Lunge"])
    after = {a["segment_id"]: a["label"] for a in s2.annotations(t["touch_id"])}
    assert all(after[sid] == tx.LEGACY_TO_TIM[old] for sid, old in before)
    assert os.path.exists(d / "engarde.sqlite.before-v0.4.0")              # untouched copy of the old database
    log = s2.db.q("SELECT op, snapshot_json FROM annotation_log WHERE segment_id=? ORDER BY log_id", (before[0][0],))
    assert json.loads(log[0]["snapshot_json"])["label"] == before[0][1] and log[-1]["op"] == "relabel"
    assert Store(str(d)).label_migration["renamed"] == 0                   # runs once


# ---------------------------------------------------------------- touches in time order
def test_touches_listed_and_numbered_by_time(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    v, b, ts = _bout(s, fx, lights=(150, 232), labels=False)
    early = s.add_touch(b["bout_id"], 100)                                 # added last, happens first
    rows = s.list_touches(video_id=v["video_id"])
    assert [r["light_frame"] for r in rows] == [100, 150, 232] and [r["seq"] for r in rows] == [1, 2, 3]
    assert s.get_touch(early["touch_id"])["seq"] == 1 and s.get_touch(ts[1]["touch_id"])["seq"] == 3
    assert [t["seq"] for t in exports.export_bundle(s, v["video_id"])["videos"][0]["bouts"][0]["touches"]] == [1, 2, 3]


# ---------------------------------------------------------------- delete / lock / split
def test_delete_video_removes_everything_and_backs_up_labels(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    v, b, (t,) = _bout(s, fx, "to delete")
    keep, _, (tk,) = _bout(s, fx, "to keep", athletes=("B1", "B2"))
    for touch in (t, tk):                                       # one review record on each video
        s.review_prediction(s.predictions(touch["touch_id"])[0]["segment_id"], "reject", "rev")
    r = s.delete_video(v["video_id"], "tester")
    assert r["touches"] == 1 and r["labels"] > 0 and os.path.exists(r["backup"])
    with pytest.raises(NotFound):
        s.get_video(v["video_id"])
    for table in ("touches", "bouts"):
        assert s.db.one(f"SELECT COUNT(*) n FROM {table} WHERE video_id=?", (v["video_id"],))["n"] == 0
    for table in ("action_segments", "review_records", "prediction_runs", "events"):
        assert s.db.one(f"SELECT COUNT(*) n FROM {table} WHERE touch_id=?", (t["touch_id"],))["n"] == 0
    assert not os.path.exists(s.video_dir(v["video_id"])) and not os.path.exists(s.touch_dir(t["touch_id"]))
    assert s.annotations(tk["touch_id"]) and os.path.exists(s.video_dir(keep["video_id"]))   # the other video is intact
    # review records are still append-only in normal use
    with pytest.raises(sqlite3.IntegrityError):
        s.db.x("DELETE FROM review_records")
    assert s.db.one("SELECT COUNT(*) n FROM review_records")["n"] == 1
    # the backup brings the labels back when the same file is uploaded again
    v2 = s.add_video(fx["video"], "again", is_synthetic=True)
    s.index_video(v2["video_id"])
    rep = exports.import_labels(s, json.load(open(r["backup"]))["videos"], v2["video_id"], "tester")
    assert rep["labels_imported"] == r["labels"] and rep["touches_created"] == 1


def test_lock_blocks_changes_until_unlocked(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    v, b, (t,) = _bout(s, fx)
    a = s.annotations(t["touch_id"])[0]
    s.set_lock(v["video_id"], True, "Mrunal")
    for fn in (lambda: s.update_annotation(a["segment_id"], {"label": "Lunge"}, "x"),
               lambda: s.delete_annotation(a["segment_id"], "x"),
               lambda: s.create_annotation(t["touch_id"], "left", 60, 70, "Lunge", "x", replace_overlaps=True),
               lambda: s.add_touch(b["bout_id"], 100),
               lambda: s.delete_video(v["video_id"], "x")):
        with pytest.raises(Locked):
            fn()
    assert s.annotations(t["touch_id"])[0]["label"] == a["label"]
    s.set_lock(v["video_id"], False, "someone else")            # no admin list -> anyone may unlock, name is logged
    s.update_annotation(a["segment_id"], {"label": "Lunge"}, "x")
    with open(os.path.join(s.data_dir, "team.json"), "w") as f:
        json.dump({"admins": ["Ahror"]}, f)
    s.set_lock(v["video_id"], True, "Mrunal")
    with pytest.raises(Locked):
        s.set_lock(v["video_id"], False, "Jayant")              # admin list set -> only the locker or an admin
    s.set_lock(v["video_id"], False, "ahror")
    assert [r["action"] for r in s.db.q("SELECT action FROM audit_log ORDER BY log_id")] == ["lock", "unlock", "lock", "unlock"]


def test_test_videos_are_never_trained_on(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    bouts = [_bout(s, fx, f"bout {i}", athletes=(f"L{i}", f"R{i}")) for i in range(3)]
    s.set_split(bouts[2][0]["video_id"], "test")
    assert s.test_bout_ids() == [bouts[2][1]["bout_id"]]
    m = ml.train(s)                                             # no test_bouts given -> uses the split
    assert m["test_bouts"] == [bouts[2][1]["bout_id"]] and bouts[2][1]["bout_id"] not in m["dev_bouts"]
    assert m["cv_mode"] == "leave_one_bout_out" and m["test"]["n_frames"] > 0
    r = ml.readiness(s)
    assert r["can_train"] and len(r["train_bouts"]) == 2 and len(r["test_bouts"]) == 1
    for b in bouts[:2]:
        s.set_split(b[0]["video_id"], "test")
    with pytest.raises(ValueError, match="mark at least one video as Training"):
        ml.train(s)


def test_training_does_not_wait_for_more_data(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    assert ml.readiness(s)["can_train"] is False
    _bout(s, fx, lights=(150, 232))                             # ONE labelled bout, two touches
    r = ml.readiness(s)
    assert r["can_train"] and [x["ok"] for x in r["steps"]][:2] == [True, False]
    m = ml.train(s)
    assert m["cv_mode"] == "within_bout_by_touch" and m["cv_summary"]["macro_f1_all_frames"]["mean"] is not None
    assert any("optimistic" in w for w in m["warnings"])
    assert set(m["classes"]) <= set(s.tax.footwork)             # phase markers / can't-tell are never classes


def test_phase_markers_are_not_model_classes(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    v, b, (t,) = _bout(s, fx)
    s.create_annotation(t["touch_id"], "left", 60, 70, "Start of touch", "x", replace_overlaps=True)
    d = ml.build_sequences(s)
    assert d["excluded"]["phase_markers"] == 10
    assert all(y not in s.tax.markers for q in d["seqs"] for y in q["y"])


# ---------------------------------------------------------------- shared labels
def test_import_a_teammates_labels(tmp_path, fx):
    a = Store(str(tmp_path / "mrunal"))
    va, ba, (ta,) = _bout(a, fx, "bout", athletes=("tse", "kov"))
    n = len(a.annotations(ta["touch_id"]))
    files = {"bundle.json": json.dumps(exports.export_bundle(a, va["video_id"])).encode(),
             "labels.json": json.dumps(exports.segment_records(a, kind="annotation")).encode(),
             "labels.csv": ("﻿" + exports.to_csv(exports.segment_records(a, kind="annotation"),
                                                     exports.RECORD_FIELDS + exports.EXTRA_FIELDS)).encode()}
    for name, raw in files.items():
        b = Store(str(tmp_path / ("ahror_" + name)))
        vb = b.add_video(fx["video"], "same file, other computer", is_synthetic=True)
        b.index_video(vb["video_id"])
        shutil.copy(fx["skeleton"], os.path.join(b.video_dir(vb["video_id"]), "imported_skeleton.json"))
        bb = b.add_bout(vb["video_id"], "bout")
        mine = b.add_touch(bb["bout_id"], 151)                  # found by the automatic analysis, 1 frame off
        vids = exports.parse_labels_file(raw, name)
        target = None if name == "bundle.json" else vb["video_id"]     # the bundle carries the file fingerprint
        rep = exports.import_labels(b, vids, target, "Ahror")
        assert rep["labels_imported"] == n and rep["touches_matched"] == 1 and rep["touches_created"] == 0, (name, rep)
        got = b.annotations(mine["touch_id"])
        assert len(got) == n and {g["label_source"] for g in got} == {"blind"} and {g["annotator_id"] for g in got} == {"tester"}
        again = exports.import_labels(b, vids, target, "Ahror")       # idempotent
        assert again["labels_imported"] == 0 and again["already_here"] == n
    # an old (v0.3.0) export with the old label names is translated on the way in
    old = [{**r, "label": {"Step forward": "Advance", "Pull/retreat": "Retreat", "En garde": "Other/Still"}.get(r["label"], r["label"]),
            "segment_id": None} for r in exports.segment_records(a, kind="annotation")]
    c = Store(str(tmp_path / "c"))
    vc = c.add_video(fx["video"], "c", is_synthetic=True)
    c.index_video(vc["video_id"])
    rep = exports.import_labels(c, exports.parse_labels_file(json.dumps(old).encode(), "old.json"), vc["video_id"], "x")
    tc = c.list_touches(video_id=vc["video_id"])
    assert rep["labels_imported"] == n and rep["touches_created"] == 1 and len(tc) == 1
    assert {x["label"] for x in c.annotations(tc[0]["touch_id"])} <= set(c.tax.annotation_labels)
    # a different video is refused instead of mapped wrongly; the local label wins on a clash
    assert exports.import_labels(c, [{**exports.parse_labels_file(files["bundle.json"])[0], "sha256": "x" * 64, "video_id": "nope"}])["unmatched_videos"]
    clash = [{**old[0], "label": "Lunge", "start_frame": old[0]["start_frame"] + 1}]
    rep = exports.import_labels(c, exports.parse_labels_file(json.dumps(clash).encode(), "x.json"), vc["video_id"], "x")
    assert rep["labels_imported"] == 0 and len(rep["conflicts"]) == 1


def test_frames_extracted_once_when_two_requests_collide(tmp_path, fx):
    import threading
    s = Store(str(tmp_path / "d"))
    v = s.add_video(fx["video"], "v", is_synthetic=True)
    s.index_video(v["video_id"])
    t = s.add_touch(s.add_bout(v["video_id"], "b")["bout_id"], 150)
    errs = []

    def go():
        try:
            runner.prepare_touch(s, t["touch_id"])
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    th = [threading.Thread(target=go) for _ in range(3)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert not errs and len(os.listdir(s.frames_dir(t["touch_id"]))) == t["end_frame"] - t["start_frame"]


def test_app_folder_can_be_renamed(tmp_path, fx):
    """v0.5.2: the database stores full paths; after the folder is renamed everything is still found."""
    old, new = tmp_path / "engarde-mvp-v0.3.0" / "data", tmp_path / "EnGarde" / "data"
    s = Store(str(old))
    bouts = [_bout(s, fx, f"b{i}", athletes=(f"L{i}", f"R{i}")) for i in range(2)]
    m = ml.train(s)
    t = bouts[0][2][0]
    s.db.conn.close()
    os.rename(tmp_path / "engarde-mvp-v0.3.0", tmp_path / "EnGarde")
    s2 = Store(str(new))
    vids = s2.list_videos()
    assert len(vids) == 2 and all(v["linked"] and v["path"].startswith(str(new)) for v in vids)
    assert s2.get_video(bouts[0][0]["video_id"])["linked"]
    shutil.rmtree(s2.frames_dir(t["touch_id"]))
    runner.prepare_touch(s2, t["touch_id"])                          # decodes from the video at its new place
    assert len(os.listdir(s2.frames_dir(t["touch_id"]))) == t["end_frame"] - t["start_frame"]
    r = runner.analyze_touch(s2, t["touch_id"], method="xgboost", model_id=m["model_id"])   # model file found too
    assert r["n_segments"] > 0 and ml.load_model_meta(s2, m["model_id"])["path"].startswith(str(new))
