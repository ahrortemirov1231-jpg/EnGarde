"""Acceptance tests for the MVP loop. Uses the SYNTHETIC fixture (imported ground-truth skeleton) so the
suite runs on CPU in about a minute; nothing here measures recognition quality on real video."""
import json
import os
import shutil
import sqlite3
import subprocess

import numpy as np
import pytest

from engarde import exports, ml, stats
from engarde import taxonomy as tx
from engarde.fixture import make_fixture
from engarde.intervals import IntervalError, merge_short, median_filter_labels, Seg, coverage_check
from engarde.pipeline import runner
from engarde.pipeline.features import _side_features, deriv
from engarde.store import Conflict, Store


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    return make_fixture(str(tmp_path_factory.mktemp("fx")))


def _setup(data_dir, fx, athletes=("A1", "A2"), name="bout"):
    s = Store(str(data_dir))
    v = s.add_video(fx["video"], "SYNTHETIC", is_synthetic=True)
    s.index_video(v["video_id"])
    shutil.copy(fx["skeleton"], os.path.join(s.video_dir(v["video_id"]), "imported_skeleton.json"))
    b = s.add_bout(v["video_id"], name, left={"athlete_id": athletes[0]}, right={"athlete_id": athletes[1]})
    t = s.add_touch(b["bout_id"], 150, 1, 0)
    return s, v, b, t


@pytest.fixture()
def env(tmp_path, fx):
    s, v, b, t = _setup(tmp_path / "data", fx)
    res = runner.analyze_touch(s, t["touch_id"])
    return {"s": s, "v": v, "b": b, "t": s.get_touch(t["touch_id"]), "run": res, "dir": tmp_path / "data", "fx": fx}


# ---------------------------------------------------------------- 1. video -> touch -> review
def test_video_touch_window_and_pipeline(env):
    s, t = env["s"], env["t"]
    v = s.get_video(env["v"]["video_id"])
    assert v["n_frames"] == 240 and v["width"] == 1280 and abs(v["fps_measured"] - 30) < 0.01
    # window = [-3.0 s, +0.5 s] by timestamps: 90 frames before, light, 15 after (inclusive) => [60, 166)
    assert (t["start_frame"], t["end_frame"]) == (60, 166)
    times = s.touch_times(t["touch_id"])
    assert times["rel_ms"][0] == pytest.approx(-3000, abs=0.5) and times["rel_ms"][-1] == pytest.approx(500, abs=0.5)
    assert all(v == "completed" or v == "cached" for v in env["run"]["stage_status"].values())
    for f in ("s1_window.json", "tracks.parquet", "pose.npz", "features.parquet", "review.csv", "airs_timeline.txt",
              "context.json", "manifest.json"):
        assert os.path.exists(os.path.join(s.stage_dir(t["touch_id"]), f)), f
    preds = s.predictions(t["touch_id"])
    assert preds and {p["side"] for p in preds} == {"left", "right"}


def test_window_clipped_at_video_end(env):
    s, b = env["s"], env["b"]
    t2 = s.add_touch(b["bout_id"], 232, 1, 1)
    assert t2["end_frame"] == 240 and t2["window_clipped"] == 1


def test_exact_frames_match_decoder(env):
    """Extracted JPEG for frame n equals frame n from an independent ffmpeg decode (select=eq(n,..))."""
    import cv2
    s, t = env["s"], env["t"]
    n = 123
    out = os.path.join(str(env["dir"]), "ff_123.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", s.get_video(t["video_id"])["path"], "-vf",
                    f"select=eq(n\\,{n})", "-frames:v", "1", "-fps_mode", "passthrough", out], check=True)
    a = cv2.imread(os.path.join(s.frames_dir(t["touch_id"]), f"{n:06d}.jpg")).astype(int)
    b = cv2.imread(out).astype(int)
    assert np.abs(a - b).mean() < 3.0  # JPEG noise only
    c = cv2.imread(os.path.join(s.frames_dir(t["touch_id"]), f"{n + 1:06d}.jpg")).astype(int)
    assert np.abs(c - b).mean() > np.abs(a - b).mean()   # and it is not the neighbour


# ---------------------------------------------------------------- 2. sides are independent
def test_left_right_independent(env):
    s, tid = env["s"], env["t"]["touch_id"]
    s.create_annotation(tid, "left", 60, 90, "Advance", "ann1", label_source="blind")
    s.create_annotation(tid, "right", 60, 90, "Retreat", "ann1", label_source="blind")  # same frames, other side: ok
    with pytest.raises(Conflict):
        s.create_annotation(tid, "left", 80, 100, "Lunge", "ann1")
    a = {x["side"]: x for x in s.annotations(tid)}
    # old names are accepted and stored under Tim's names (v0.4.0)
    assert a["left"]["label"] == "Step forward" and a["right"]["label"] == "Pull/retreat"


# ---------------------------------------------------------------- 3. edits survive restart
def test_edit_persists_after_restart(env):
    s, tid = env["s"], env["t"]["touch_id"]
    a = s.create_annotation(tid, "left", 70, 80, "Advance", "ann1")
    s.update_annotation(a["segment_id"], {"label": "Lunge", "start_frame": 72, "end_frame": 95}, "ann1")
    s.db.conn.close()
    s2 = Store(str(env["dir"]))
    b = [x for x in s2.annotations(tid) if x["segment_id"] == a["segment_id"]][0]
    assert (b["label"], b["start_frame"], b["end_frame"]) == ("Lunge", 72, 95)
    hist = s2.db.q("SELECT op FROM annotation_log WHERE segment_id=? ORDER BY log_id", (a["segment_id"],))
    assert [h["op"] for h in hist] == ["create", "update"]


# ---------------------------------------------------------------- 4. export -> import round trip
def test_bundle_and_csv_roundtrip(env, tmp_path):
    s, tid = env["s"], env["t"]["touch_id"]
    s.create_annotation(tid, "left", 60, 92, "Other/Still", "ann1", label_source="blind")
    s.create_annotation(tid, "left", 92, 110, "Advance", "ann1", label_source="blind")
    s.create_annotation(tid, "right", 95, 115, "Unobservable", "ann2", label_source="adjudicated")
    s.add_event(tid, "left", "lunge_start", 126, "expert")
    bundle = json.loads(json.dumps(exports.export_bundle(s), default=str))
    csv_text = exports.to_csv(exports.segment_records(s, kind="annotation"), exports.RECORD_FIELDS + exports.EXTRA_FIELDS)
    before = sorted((a["side"], a["start_frame"], a["end_frame"], a["label"], a["label_source"]) for a in s.annotations(tid))
    before_ms = sorted((r["side"], r["t_start_ms"], r["t_end_ms"]) for r in exports.segment_records(s, kind="annotation"))
    # fresh database, same video file
    s2 = Store(str(tmp_path / "fresh"))
    v2 = s2.add_video(env["fx"]["video"], "again")
    s2.index_video(v2["video_id"])
    st = exports.import_bundle(s2, bundle)
    assert st["annotations"] == 3 and st["events"] == 1 and not st["time_mismatches"]
    after = sorted((a["side"], a["start_frame"], a["end_frame"], a["label"], a["label_source"]) for a in s2.annotations(tid))
    after_ms = sorted((r["side"], r["t_start_ms"], r["t_end_ms"]) for r in exports.segment_records(s2, kind="annotation"))
    assert before == after and before_ms == after_ms
    assert exports.import_bundle(s2, bundle)["annotations"] == 0  # idempotent
    # CSV import into a third database
    s3 = Store(str(tmp_path / "csv"))
    v3 = s3.add_video(env["fx"]["video"], "csv")
    s3.index_video(v3["video_id"])
    exports.import_bundle(s3, {**bundle, "videos": [{**bundle["videos"][0], "bouts": [
        {**b, "touches": [{**t, "annotations": [], "expert_events": [], "reviews": []} for t in b["touches"]]}
        for b in bundle["videos"][0]["bouts"]]}]})
    r = exports.import_annotations_csv(s3, csv_text)
    assert r["imported"] == 3 and not r["errors"]
    assert sorted((a["side"], a["start_frame"], a["end_frame"], a["label"], a["label_source"]) for a in s3.annotations(tid)) == before


# ---------------------------------------------------------------- 5. corrections never overwrite predictions
def test_review_appends_and_prediction_immutable(env):
    s, tid = env["s"], env["t"]["touch_id"]
    p = [x for x in s.predictions(tid) if not x["no_call"]][0]
    snap = s.db.one("SELECT * FROM action_segments WHERE segment_id=?", (p["segment_id"],))
    r = s.review_prediction(p["segment_id"], "correct", "coach", corrected_label="Lunge",
                            corrected_start=p["start_frame"], corrected_end=p["end_frame"] - 1, error_reason="boundary")
    assert r["label_source"] == "model_assisted" and r["resulting_annotation_id"]
    assert s.db.one("SELECT * FROM action_segments WHERE segment_id=?", (p["segment_id"],)) == snap
    with pytest.raises(sqlite3.DatabaseError):
        s.db.x("UPDATE action_segments SET label='X' WHERE segment_id=?", (p["segment_id"],))
    with pytest.raises(sqlite3.DatabaseError):
        s.db.x("DELETE FROM review_records WHERE review_id=?", (r["review_id"],))
    ann = s.db.one("SELECT * FROM action_segments WHERE segment_id=?", (r["resulting_annotation_id"],))
    assert ann["derived_from_segment_id"] == p["segment_id"] and ann["label_source"] == "model_assisted"
    # a second review is appended, the first stays
    s.review_prediction(p["segment_id"], "reject", "coach2")
    assert len(s.reviews(tid)) == 2


def test_blind_firewall(env):
    s, tid = env["s"], env["t"]["touch_id"]
    a = s.create_annotation(tid, "left", 60, 70, "Other/Still", "blinder", label_source="blind")
    assert a["label_source"] == "blind"
    s.mark_predictions_viewed(tid, "blinder")
    b = s.create_annotation(tid, "left", 70, 80, "Advance", "blinder", label_source="blind")
    assert b["label_source"] == "model_assisted"   # asked for blind, but has seen predictions
    c = s.update_annotation(a["segment_id"], {"end_frame": 69}, "blinder")
    assert c["label_source"] == "model_assisted"   # editing after viewing downgrades provenance
    other = s.create_annotation(tid, "right", 60, 70, "Other/Still", "someone_else", label_source="blind")
    assert other["label_source"] == "blind"


# ---------------------------------------------------------------- 6. interval validation
def test_interval_validation(env):
    s, t = env["s"], env["t"]
    tid = t["touch_id"]
    for args in [(t["start_frame"] - 1, 70), (100, t["end_frame"] + 1), (80, 80), (90, 85)]:
        with pytest.raises(IntervalError):
            s.create_annotation(tid, "left", *args, "Advance", "a")
    with pytest.raises(IntervalError):
        s.create_annotation(tid, "left", 70.5, 80, "Advance", "a")
    with pytest.raises(IntervalError):
        s.create_annotation(tid, "middle", 70, 80, "Advance", "a")
    with pytest.raises(IntervalError):
        s.create_annotation(tid, "left", 70, 80, "Hop", "a")          # not enabled until defined
    with pytest.raises(IntervalError):
        s.create_annotation(tid, "left", 70, 80, "Advance", "")       # no annotator
    s.create_annotation(tid, "left", 70, 80, "Advance", "a")
    with pytest.raises(Conflict):
        s.create_annotation(tid, "left", 79, 85, "Retreat", "a")      # [70,80) and [79,85) overlap
    s.create_annotation(tid, "left", 80, 85, "Retreat", "a")          # touching boundary is fine
    with pytest.raises(IntervalError):
        s.add_touch(env["b"]["bout_id"], 9999)


def test_unknown_unobservable_nocall_distinct(env):
    s, tid = env["s"], env["t"]["touch_id"]
    s.create_annotation(tid, "left", 60, 70, tx.UNKNOWN, "a")
    s.create_annotation(tid, "left", 70, 80, tx.UNOBSERVABLE, "a")
    labs = {a["label"] for a in s.annotations(tid)}
    assert {tx.UNKNOWN, tx.UNOBSERVABLE} <= labs and "Other/Still" not in labs
    nc = [p for p in s.predictions(tid) if p["no_call"]]
    assert nc and all(p["label"] != "Other/Still" for p in nc if "pose_missing" in p["no_call_reason"])


# ---------------------------------------------------------------- 7. missing pose / tracking -> no_call
def test_occlusion_becomes_no_call(env):
    s, tid = env["s"], env["t"]["touch_id"]
    right = [p for p in s.predictions(tid) if p["side"] == "right"]
    occl = [p for p in right if p["start_frame"] <= 155 < p["end_frame"]][0]
    assert occl["no_call"] and "pose_missing" in occl["no_call_reason"]


def test_tracking_failure_is_reported_not_hidden(env):
    """YOLO on stick figures finds no people: every interval must be no_call, none labelled."""
    s, tid = env["s"], env["t"]["touch_id"]
    res = runner.analyze_touch(s, tid, pose_source="yolo", force=True)
    preds = s.predictions(tid, res["run_id"])
    assert preds and all(p["no_call"] and "pose_missing" in p["no_call_reason"] for p in preds)
    man = json.load(open(os.path.join(s.stage_dir(tid), "manifest.json")))
    assert man["s2_tracking"]["info"]["selection"]["status"] in ("no_tracks", "no_tracks_in_piste")


# ---------------------------------------------------------------- 8/9. no invented scores or metres
def test_rule_baseline_has_no_probabilities_and_no_model_scores(env):
    s, tid = env["s"], env["t"]["touch_id"]
    run = s.latest_run(tid)
    assert run["status_label"] == "Rule baseline / unvalidated" and run["calibrated"] == 0
    assert all(p["class_probs"] is None and p["calibrated_prob"] is None for p in s.predictions(tid))
    assert ml.list_models(s) == []
    rows = exports.review_csv_rows(s, [tid])
    assert {r["prob_status"] for r in rows} == {"none (rule baseline)"}


def test_no_metric_distance_without_calibration(env):
    s, tid = env["s"], env["t"]["touch_id"]
    s.create_annotation(tid, "left", 60, 90, "Advance", "a")
    ins = stats.insights(s)
    assert ins["metrics"]["launch_distance_m"]["available"] is False
    assert ins["metrics"]["launch_distance_m"]["value"] is None
    assert ins["metrics"]["recovery_duration_ms"]["available"] is False
    assert ins["by_side"]["right"]["Lunge"]["mean_ms"] is None   # missing -> None, never 0


# ---------------------------------------------------------------- AIRS
def test_airs_timeline_physical_only_and_real_frames(env):
    s, tid = env["s"], env["t"]["touch_id"]
    a = exports.airs_timeline(s, tid)
    txt = a["timeline_txt"]
    for w in tx.FORBIDDEN_TIMELINE_WORDS:
        assert w not in txt.lower()
    assert "right feet: not determinable" in txt   # occlusion window
    idx = s.frame_index(env["t"]["video_id"])
    for line in txt.splitlines():
        if "frame " in line and line.endswith(")") and "ms " in line:
            ms = float(line.split("ms")[0])
            fr = int(line.rsplit("frame ", 1)[1].rstrip(")"))
            assert ms == pytest.approx((idx["t"][fr] - idx["t"][150]) * 1000, abs=0.6)
    assert a["context"]["lights"] == {"left": True, "right": False}
    s.add_event(tid, "left", "lunge_start", 126, "expert")
    e = exports.airs_timeline(s, tid, arm="expert")["timeline_txt"]
    assert "-800ms left lunge_start (expert, frame 126)" in e


def test_review_csv_import_roundtrip(env):
    import csv
    import io
    s, tid = env["s"], env["t"]["touch_id"]
    rows = exports.review_csv_rows(s, [tid])
    target = [r for r in rows if not r["no_call"]][0]
    target.update(review_action="confirm", reviewer="coachcsv")
    text = exports.to_csv(rows, exports.REVIEW_CSV_FIELDS)
    r1 = exports.import_review_csv(s, text)
    assert r1["imported"] == 1 and not r1["errors"]
    assert exports.import_review_csv(s, text)["skipped_duplicates"] == 1
    assert list(csv.DictReader(io.StringIO(text)))[0].keys() >= {"video_link", "top2_label", "calibrated_prob"}


# ---------------------------------------------------------------- features / intervals units
def test_mirroring_makes_both_fencers_attack_right():
    T = 20
    t = np.arange(T) / 30.0
    xy = np.zeros((T, 17, 2))
    xy[:, :, 1] = 300
    xy[:, 0, 1] = 100        # nose high
    xy[:, 15, 1] = xy[:, 16, 1] = 500
    for k in range(T):       # right fencer moving LEFT on screen (towards the opponent)
        xy[k, :, 0] = 900 - 5 * k
        xy[k, 15, 0] = 900 - 5 * k - 40   # front (towards opponent = screen left)
        xy[k, 16, 0] = 900 - 5 * k + 40
    mask = np.ones((T, 17), np.uint8)
    df, meta = _side_features(xy, mask, np.ones((T, 17)), "right", 1280, t, np.full(T, 400.0), np.zeros(T))
    assert np.nanmedian(df.hip_vel) > 0 and np.nanmedian(df.foot_spread) > 0 and meta["mirrored"]


def test_derivative_robust_to_irregular_timing():
    t = np.array([0, .033, .043, .076, .110, .120, .153, .186, .219, .229, .262])
    x = 2.0 * t + np.array([0, .002, -.002, .001, 0, -.001, .002, 0, -.002, .001, 0])
    d = deriv(x, t)
    assert np.nanmax(np.abs(d - 2.0)) < 0.5


def test_interval_helpers():
    assert merge_short([Seg(0, 10, "A"), Seg(10, 12, "B"), Seg(12, 30, "A")], 4) == [Seg(0, 30, "A")]
    assert median_filter_labels(list("AAABAAA"), 5) == list("AAAAAAA")
    cov = coverage_check([{"start_frame": 0, "end_frame": 5}, {"start_frame": 7, "end_frame": 10}], 0, 10)
    assert cov["gaps"] == [[5, 7]] and not cov["complete"]


def test_vfr_window_uses_timestamps(tmp_path, fx):
    """A variable-frame-rate copy: the window is cut by real timestamps, not frame counts."""
    vfr = str(tmp_path / "vfr.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", fx["video"], "-vf",
                    "setpts='if(lt(N,120),N*(1/30)/TB,(120*(1/30)+(N-120)*(1/15))/TB)'", "-fps_mode", "vfr",
                    "-c:v", "libx264", "-preset", "ultrafast", vfr], check=True)
    s = Store(str(tmp_path / "d"))
    v = s.add_video(vfr, "vfr")
    s.index_video(v["video_id"])
    assert s.get_video(v["video_id"])["vfr"] == 1
    b = s.add_bout(v["video_id"], "b")
    t = s.add_touch(b["bout_id"], 150)
    times = s.touch_times(t["touch_id"])
    assert times["rel_ms"][0] >= -3000.5 and times["rel_ms"][-1] <= 500.5
    # after frame 120 frames are 1/15 s apart => only ~8 frames in the +0.5 s tail
    assert t["end_frame"] - 150 <= 8


# ---------------------------------------------------------------- XGBoost with bout/athlete isolation
def test_xgboost_train_reports_f1_coverage_abstention(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    labels = json.load(open(fx["labels"]))
    for k, ath in enumerate([("A1", "A2"), ("A3", "A4"), ("A5", "A6"), ("A7", "A8")]):
        v = s.add_video(fx["video"], f"synthetic {k}", is_synthetic=True)
        s.index_video(v["video_id"])
        shutil.copy(fx["skeleton"], os.path.join(s.video_dir(v["video_id"]), "imported_skeleton.json"))
        b = s.add_bout(v["video_id"], f"b{k}", left={"athlete_id": ath[0]}, right={"athlete_id": ath[1]})
        t = s.add_touch(b["bout_id"], 150, 1, 0)
        runner.analyze_touch(s, t["touch_id"])
        for side, segs in labels["segments"].items():
            for sg in segs:
                lo, hi = max(sg["start_frame"], t["start_frame"]), min(sg["end_frame"], t["end_frame"])
                if hi > lo:
                    s.create_annotation(t["touch_id"], side, lo, hi, sg["label"], "fixture",
                                        label_source="blind", _op="import")
    test_bout = s.db.q("SELECT bout_id FROM bouts ORDER BY created_at, rowid")[-1]["bout_id"]
    res = ml.train(s, W=9, test_bouts=[test_bout])
    assert res["cv_summary"] is not None
    for k in ("macro_f1_all_frames", "coverage", "abstention_rate"):
        assert k in res["cv_summary"]
    assert len([f for f in res["folds"] if "macro_f1_all_frames" in f]) == 3        # leave-one-bout-out over 3 dev bouts
    assert test_bout not in res["dev_bouts"] and res["test"]["n_frames"] > 0
    assert not any("athlete leakage" in w for w in res["warnings"])
    # abstentions are counted: the occluded right-fencer frames cannot be 'covered'
    assert res["test"]["n_abstained"] > 0 and res["test"]["coverage"] < 1.0
    # inference through the pipeline
    tid = s.list_touches()[0]["touch_id"]
    r = runner.analyze_touch(s, tid, method="xgboost", model_id=res["model_id"])
    run = s.latest_run(tid)
    assert run["method"] == "xgboost" and r["n_segments"] > 0
    probs = [p for p in s.predictions(tid) if p["class_probs"]]
    assert probs and all(abs(sum(p["class_probs"].values()) - 1) < 0.02 for p in probs)


def test_athlete_leakage_is_flagged(tmp_path, fx):
    s = Store(str(tmp_path / "d"))
    labels = json.load(open(fx["labels"]))
    for k in range(2):
        v = s.add_video(fx["video"], f"s{k}", is_synthetic=True)
        s.index_video(v["video_id"])
        shutil.copy(fx["skeleton"], os.path.join(s.video_dir(v["video_id"]), "imported_skeleton.json"))
        b = s.add_bout(v["video_id"], f"b{k}", left={"athlete_id": "SAME"}, right={"athlete_id": f"R{k}"})
        t = s.add_touch(b["bout_id"], 150, 1, 0)
        runner.analyze_touch(s, t["touch_id"])
        for side, segs in labels["segments"].items():
            for sg in segs:
                lo, hi = max(sg["start_frame"], t["start_frame"]), min(sg["end_frame"], t["end_frame"])
                if hi > lo:
                    s.create_annotation(t["touch_id"], side, lo, hi, sg["label"], "fx", label_source="blind", _op="import")
    last = s.db.q("SELECT bout_id FROM bouts ORDER BY created_at, rowid")[-1]["bout_id"]
    res = ml.train(s, W=9, test_bouts=[last])
    assert any("athlete leakage" in w for w in res["warnings"])


def test_resolution_change_is_not_squashed(tmp_path):
    """Screen recordings change resolution mid-stream. Frames must be fitted into one canvas with
    their aspect ratio intact (regression: OpenCV rescaled them to the first frame's size)."""
    import cv2
    from engarde import video as vid
    parts = []
    for k, (w, h) in enumerate([(640, 360), (640, 480)]):
        frames_dir = tmp_path / f"p{k}"
        frames_dir.mkdir()
        for i in range(10):
            img = np.full((h, w, 3), 200, np.uint8)
            cv2.rectangle(img, (270, h // 2 - 50), (370, h // 2 + 50), (0, 0, 255), -1)   # a red 100x100 square
            cv2.imwrite(str(frames_dir / f"{i:03d}.png"), img)
        out = tmp_path / f"p{k}.webm"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "30", "-i", str(frames_dir / "%03d.png"),
                        "-c:v", "libvpx-vp9", "-deadline", "realtime", "-pix_fmt", "yuv420p",
                        "-output_ts_offset", str(k * 10 / 30), str(out)], check=True)
        parts.append(out)
    lst = tmp_path / "l.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    joined = str(tmp_path / "joined.webm")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", joined], check=True)
    cv = vid.canvas_size(joined)
    assert (cv["width"], cv["height"]) == (640, 480) and cv["variable"]
    idx = vid.build_frame_index(joined)
    assert len(idx["t"]) == 20
    for f, img in vid.decode_range(joined, idx, 0, 20):
        assert img.shape[:2] == (480, 640)
        dark = np.argwhere((img[..., 2] > 150) & (img[..., 1] < 90))
        hgt = dark[:, 0].max() - dark[:, 0].min() + 1
        wid = dark[:, 1].max() - dark[:, 1].min() + 1
        assert abs(hgt - wid) <= 4, (f, hgt, wid)       # still a square, not squashed


def test_pair_plausibility_flags_background_person():
    """A tiny box far up the image (spectator / background) paired with a full-size fencer is flagged."""
    import pandas as pd
    from engarde.pipeline.perception import add_track_flags
    rows = []
    for f in range(10):
        rows.append(dict(frame=f, side="left", present=True, track_id=1, x1=100, y1=200, x2=260, y2=700, score=.9, det_row=-1))
        small = f >= 5   # from frame 5 the 'right' track is a small person in the stands
        rows.append(dict(frame=f, side="right", present=True, track_id=2, x1=900, y1=60 if small else 210,
                         x2=960 if small else 1060, y2=220 if small else 705, score=.9, det_row=-1))
    tr = add_track_flags(pd.DataFrame(rows))
    r = tr[tr.side == "right"].set_index("frame")
    assert not r.loc[0:4, "pair_implausible"].any()
    assert r.loc[5:9, "pair_implausible"].all() and r.loc[5:9, "identity_uncertain"].all()


def _det(rows):
    import pandas as pd
    return pd.DataFrame(rows, columns=["frame", "track_id", "x1", "y1", "x2", "y2", "score"])


def test_selection_prefers_white_plausible_pair_and_reviewer_wins():
    from engarde.pipeline.perception import select_fencers
    frames = list(range(20))
    rows = []
    for f in frames:
        rows.append([f, 1, 100, 200, 260, 700, .9])     # fencer A
        rows.append([f, 2, 900, 210, 1060, 705, .9])    # fencer B
        rows.append([f, 3, 1150, 150, 1330, 760, .9])   # referee: similar size, dark suit
        rows.append([f, 4, 600, 700, 800, 1000, .9])    # audience head at the bottom
    det = _det(rows)
    app = {1: 0.45, 2: 0.50, 3: 0.02, 4: 0.20}
    tr, info = select_fencers(det, frames, appearance=app)
    assert info["status"] == "two_fencers" and info["assignment"] == {"left": 1, "right": 2}
    # dark referee is never eligible even though larger
    assert [c for c in info["candidates"] if c["track_id"] == 3][0]["eligible"] is False
    # reviewer override wins, even against the automatic choice
    tr2, info2 = select_fencers(det, frames, appearance=app, forced={"left": 1, "right": 3})
    assert info2["status"] == "reviewer_assigned" and info2["assignment"] == {"left": 1, "right": 3}
    assert set(tr2[tr2.side == "right"].track_id) == {3}


def test_selection_refuses_background_pair_when_main_fencer_has_no_partner():
    from engarde.pipeline.perception import select_fencers
    frames = list(range(20))
    rows = []
    for f in frames:
        rows.append([f, 1, 100, 100, 300, 1000, .9])    # main fencer, large
        rows.append([f, 2, 1500, 300, 1580, 680, .9])   # small background fencer
        rows.append([f, 3, 1800, 310, 1880, 690, .9])   # small background fencer
        rows.append([f, 4, 900, 400, 1150, 1050, .9])   # audience silhouette (dark)
    tr, info = select_fencers(_det(rows), frames, appearance={1: .5, 2: .5, 3: .5, 4: .1})
    assert info["status"] == "no_plausible_pair"
    assert tr.identity_uncertain.all()          # -> every frame no_call until a reviewer assigns
