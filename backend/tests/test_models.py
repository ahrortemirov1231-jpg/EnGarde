"""E1 vs E2 under one protocol, evaluation helpers (PRD §9.2, §10.2, §10.3) and the faster feature code.
SYNTHETIC fixture only: nothing here measures recognition quality on real video."""
import json
import os
import shutil
import warnings

import numpy as np
import pandas as pd
import pytest

from engarde import evaluation as ev
from engarde import ml, tcn
from engarde.fixture import make_fixture
from engarde.pipeline import runner
from engarde.pipeline.features import FEATURE_COLUMNS
from engarde.store import Store

FAST_TCN = dict(epochs=25, patience=6, min_epochs=5)


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return make_fixture(str(tmp_path_factory.mktemp("fx_models")))


@pytest.fixture(scope="module")
def labelled_store(tmp_path_factory, fx):
    """Four synthetic bouts with distinct athletes and blind labels; the last bout is the frozen test bout."""
    s = Store(str(tmp_path_factory.mktemp("d_models")))
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
    return s, test_bout


# ---------------------------------------------------------------- feature code
def _window_features_reference(f, W):
    """The original per-frame loop, kept here as the reference implementation."""
    X = f[FEATURE_COLUMNS].to_numpy(dtype=float)
    T, D = X.shape
    h = W // 2
    out = np.full((T, D * 6), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for t in range(T):
            w = X[max(0, t - h): min(T, t + h + 1)]
            mn, mx = np.nanmin(w, 0), np.nanmax(w, 0)
            valid = ~np.isnan(w)
            first = np.array([w[valid[:, d], d][0] if valid[:, d].any() else np.nan for d in range(D)])
            last = np.array([w[valid[:, d], d][-1] if valid[:, d].any() else np.nan for d in range(D)])
            out[t] = np.concatenate([np.nanmean(w, 0), np.nanstd(w, 0), mn, mx, mx - mn, last - first])
    return out


@pytest.mark.parametrize("T,W", [(105, 15), (30, 9), (5, 21), (1, 15)])
def test_vectorised_window_features_match_reference(T, W):
    rng = np.random.default_rng(T)
    X = rng.normal(size=(T, len(FEATURE_COLUMNS)))
    X[rng.random(X.shape) < 0.2] = np.nan
    X[:, 3] = np.nan                                  # a fully missing feature
    f = pd.DataFrame(X, columns=FEATURE_COLUMNS)
    assert np.allclose(ml.window_features(f, W), _window_features_reference(f, W), equal_nan=True)


# ---------------------------------------------------------------- evaluation helpers
def test_calibration_report_bins_alarm_and_monotonicity():
    rng = np.random.default_rng(0)
    n = 400
    conf = rng.uniform(0.3, 1.0, n)
    y = np.zeros(n, int)
    correct = rng.random(n) < conf                    # well calibrated by construction
    P = np.zeros((n, 3))
    P[:, 0] = conf
    P[:, 1] = 1 - conf
    y[~correct] = 1
    rep = ev.calibration_report(P, y)
    assert rep["available"] and sum(b["n"] for b in rep["bins"]) == n
    assert rep["ece"] < 0.1 and 0 <= rep["brier"] <= 2
    assert rep["accuracy_monotonic_across_bins"] is True and not rep["overconfidence_alarm"]
    assert rep["risk_coverage"][-1]["coverage"] == 1.0
    # >90% of predictions above 0.9 -> AIRS over-confidence alarm, and confidence must not be shown
    P2 = np.tile([0.95, 0.05, 0.0], (100, 1))
    rep2 = ev.calibration_report(P2, np.r_[np.zeros(60, int), np.ones(40, int)])
    assert rep2["overconfidence_alarm"] and not rep2["show_confidence_to_coaches"]


def test_cluster_bootstrap_reliability_flags():
    classes = ["A", "B"]
    y = np.array(["A", "B"] * 50)
    one = ev.cluster_bootstrap(y, y, np.array(["b1"] * 100), classes)
    assert one["ci95"] is None and not one["reliable"] and one["point"] == 1.0
    groups = np.repeat([f"b{i}" for i in range(3)], [40, 30, 30])
    few = ev.cluster_bootstrap(y, y, groups, classes, B=200)
    assert few["ci95"] is not None and not few["reliable"] and "descriptive" in few["reason"]
    groups = np.repeat([f"b{i}" for i in range(5)], 20)
    pred = y.copy()
    pred[:10] = "no_call"
    ok = ev.cluster_bootstrap(y, pred, groups, classes, B=200)
    assert ok["reliable"] and ok["ci95"][0] <= ok["point"] <= ok["ci95"][1]


def test_compare_models_applies_prd_rule():
    rng = np.random.default_rng(1)
    n = 600
    keys = [f"t|left|{i}" for i in range(n)]
    y = rng.choice(["Advance", "Retreat", "Lunge"], n)
    groups = np.repeat([f"b{i}" for i in range(6)], 100)
    good = y.copy()
    bad = y.copy()
    flip = rng.random(n) < 0.4
    bad[flip] = "Retreat"
    base = {"keys": keys, "y_true": y.tolist(), "groups": groups.tolist(), "classes": ["Advance", "Lunge", "Retreat"]}
    r = ev.compare_models({**base, "y_pred": bad.tolist()}, {**base, "y_pred": good.tolist()})
    assert r["decision"] == "adopt E2" and r["difference_ci95"][0] > 0 and r["ci_reliable"]
    tiny = bad.copy()
    tiny[:5] = y[:5]                                   # < 3 points better
    r2 = ev.compare_models({**base, "y_pred": bad.tolist()}, {**base, "y_pred": tiny.tolist()})
    assert r2["decision"] == "adopt E1"
    other_truth = {**base, "y_true": np.roll(y, 1).tolist(), "y_pred": good.tolist()}
    assert ev.compare_models({**base, "y_pred": bad.tolist()}, other_truth)["available"] is False


# ---------------------------------------------------------------- TCN building blocks
def test_tcn_receptive_field_covers_window_and_is_small():
    assert tcn.receptive_field() == 127                # PRD §7.4: covers the 105-frame window
    net = tcn.build_net(2 * len(FEATURE_COLUMNS), 4)
    n_params = sum(p.numel() for p in net.parameters())
    assert 50_000 < n_params < 300_000
    import torch
    out = net(torch.zeros(2, 2 * len(FEATURE_COLUMNS), 105))
    assert tuple(out.shape) == (2, 4, 105)            # seq2seq: one prediction per frame


def test_tmse_is_zero_for_constant_and_truncated():
    import torch
    flat = torch.zeros(1, 3, 20)
    valid = torch.ones(1, 19, dtype=torch.bool)
    assert float(tcn.tmse_loss(flat, valid)) == 0.0
    spiky = torch.zeros(1, 3, 20)
    spiky[0, 0, ::2] = 1000.0                          # enormous jumps are clamped at tau^2
    assert float(tcn.tmse_loss(spiky, valid, tau=4.0)) <= 16.0 + 1e-5


# ---------------------------------------------------------------- one protocol for E1 and E2
def test_e1_and_e2_share_protocol_and_compare(labelled_store):
    s, test_bout = labelled_store
    e1 = ml.train(s, W=9, test_bouts=[test_bout])
    e2 = tcn.train(s, test_bouts=[test_bout], cfg=FAST_TCN)
    for res in (e1, e2):
        assert res["cv_summary"] is not None
        assert len([f for f in res["folds"] if "macro_f1_all_frames" in f]) == 3
        assert test_bout not in res["dev_bouts"] and res["test"]["n_frames"] > 0
        assert res["test"]["n_abstained"] > 0                    # occluded frames are counted, not hidden
        ci = res["cv_pooled_ci"]
        assert ci["n_clusters"] == 3 and ci["reliable"] is False and ci["ci95"] is not None
        assert "confusion" in res["test"] and res["test"]["bootstrap"]["n_clusters"] == 1
        assert os.path.exists(os.path.join(s.data_dir, "models", f"{res['model_id']}.oof.json"))
    assert e1["method"] == "xgboost" and e2["method"] == "tcn"
    # both models saw exactly the same out-of-fold frames, so they can be compared like for like
    cmp_ = ml.compare(s, e1["model_id"], e2["model_id"])
    assert cmp_["available"] and cmp_["same_dataset"] and cmp_["decision"] in ("adopt E1", "adopt E2")
    assert cmp_["ci_reliable"] is False and "provisional" in cmp_["warning"]
    # E2 inference through the pipeline writes probabilities that sum to 1
    tid = s.list_touches()[0]["touch_id"]
    r = runner.analyze_touch(s, tid, method="tcn", model_id=e2["model_id"])
    run = s.latest_run(tid)
    assert run["method"] == "tcn" and r["n_segments"] > 0 and run["status_label"].startswith("TCN")
    probs = [p for p in s.predictions(tid) if p["class_probs"]]
    assert probs and all(abs(sum(p["class_probs"].values()) - 1) < 0.02 for p in probs)
    # a model id of the wrong kind is refused rather than silently used
    with pytest.raises(ValueError):
        runner.analyze_touch(s, tid, method="tcn", model_id=e1["model_id"], force=True)


def test_build_dataset_old_format_still_works(labelled_store):
    s, _ = labelled_store
    d = ml.build_dataset(s, W=9)
    assert d["X"].shape[0] == len(d["y"]) == len(d["meta"]) and d["X"].shape[1] == len(FEATURE_COLUMNS) * 6
    assert {"touch_id", "bout_id", "side", "frame", "label_source", "evidence_ok"} <= set(d["meta"].columns)
