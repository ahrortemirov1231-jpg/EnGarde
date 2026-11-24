"""Recognition models under ONE evaluation protocol (PRD §3.6, §7, §9, §10).

E1 = per-frame XGBoost on windowed kinematic features (PRD §7.3).
E2 = bidirectional TCN, seq2seq per frame (PRD §7.4) — see engarde/tcn.py.

Both go through `run_protocol`, so the T2 comparison (PRD §1.3) is like for like:
 - leave-one-bout-out CV (groups = bout_id), athlete-overlap check,
 - class weights ~ 1/sqrt(freq) computed on training folds only,
 - temperature scaling fitted on a held-out calibration bout (only when >= 3 bouts),
 - test bouts behind a firewall: excluded from training/normalisation/thresholds, scored once, and
   only on blind/adjudicated labels,
 - metrics: Macro-F1 over ALL labelled frames (abstentions count as errors), coverage, abstention,
   plus Macro-F1 on covered frames — never accuracy-without-no_call alone,
 - NEW: out-of-fold predictions are kept, so CV gets a 95% interval resampled by bout (PRD §10.2) and
   two models can be compared on the same frames (evaluation.compare_models, PRD §10.3),
 - NEW: a calibration report (4 bins, ECE, Brier, risk-coverage, over-confidence alarm; PRD §9.2).
"""
from __future__ import annotations

import hashlib
import json
import os
import warnings

import numpy as np
import pandas as pd

from . import evaluation as ev
from . import taxonomy as tx
from .db import new_id, now_iso
from .intervals import segments_to_frames
from .pipeline.features import FEATURE_COLUMNS
from .pipeline.recognition import NO_EVIDENCE

XGB_PARAMS = dict(objective="multi:softprob", n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
                  colsample_bytree=0.8, min_child_weight=3, reg_lambda=1.0)
STATS = ("mean", "std", "min", "max", "range", "delta")
macro_f1 = ev.macro_f1          # kept for callers of the old name


# ------------------------------------------------------------------ features
def window_features(f: pd.DataFrame, W: int = 15) -> np.ndarray:
    """f: one side, ordered by frame. Returns [T, len(FEATURE_COLUMNS)*6] centred-window statistics.
    Windows are truncated at the sequence ends (NaN padding + nan-aware statistics). Vectorised: the
    earlier per-frame loop gave identical numbers but was ~15x slower."""
    X = f[FEATURE_COLUMNS].to_numpy(dtype=float)
    T, D = X.shape
    if T == 0:
        return np.zeros((0, D * len(STATS)))
    h = W // 2
    pad = np.full((h, D), np.nan)
    Xp = np.vstack([pad, X, pad])
    win = np.lib.stride_tricks.sliding_window_view(Xp, W, axis=0)      # [T, D, W]
    valid = ~np.isnan(win)
    any_v = valid.any(axis=2)
    first_i = np.argmax(valid, axis=2)
    last_i = W - 1 - np.argmax(valid[:, :, ::-1], axis=2)
    ti, di = np.indices((T, D))
    first = np.where(any_v, win[ti, di, first_i], np.nan)
    last = np.where(any_v, win[ti, di, last_i], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mn, mx = np.nanmin(win, axis=2), np.nanmax(win, axis=2)
        out = np.concatenate([np.nanmean(win, axis=2), np.nanstd(win, axis=2), mn, mx, mx - mn, last - first], axis=1)
    return out


def evidence_ok(f: pd.DataFrame) -> np.ndarray:
    from .pipeline.recognition import evidence_mask
    return evidence_mask(f)


# ------------------------------------------------------------------ data
def build_sequences(store, touch_ids=None) -> dict:
    """One sequence per (touch, side) that has human labels and features. Per-frame labels are None
    where a frame is unlabelled or carries a human state (Unknown / Unobservable): never trained on."""
    seqs = []
    untrained = set(tx.HUMAN_STATES) | set(store.tax.untrained)     # can't-tell states and phase markers
    excluded = {"Unknown": 0, "Unobservable": 0, "unlabelled": 0, "no_features": 0, "phase_markers": 0}
    touches = store.list_touches() if not touch_ids else [store.get_touch(t) for t in touch_ids]
    for t in touches:
        anns = store.annotations(t["touch_id"])
        if not anns:
            continue
        fp = os.path.join(store.stage_dir(t["touch_id"]), "features.parquet")
        if not os.path.exists(fp):
            excluded["no_features"] += 1
            continue
        F = pd.read_parquet(fp)
        fencers = {x["side"]: x for x in store.fencers(t["bout_id"])}
        for side in ("left", "right"):
            sa = [a for a in anns if a["side"] == side]
            if not sa:
                continue
            f = F[F.side == side].sort_values("frame").reset_index(drop=True)
            labels = segments_to_frames(sa, t["start_frame"], t["end_frame"])
            src = segments_to_frames([{**a, "label": a["label_source"]} for a in sa], t["start_frame"], t["end_frame"])
            n = min(len(f), len(labels))
            f, labels, src = f.iloc[:n].reset_index(drop=True), labels[:n], src[:n]
            y = []
            for lab in labels:
                if lab is None:
                    excluded["unlabelled"] += 1
                    y.append(None)
                elif lab in untrained:
                    excluded[lab if lab in tx.HUMAN_STATES else "phase_markers"] += 1
                    y.append(None)
                else:
                    y.append(lab)
            seqs.append({"touch_id": t["touch_id"], "bout_id": t["bout_id"], "side": side,
                         "athlete_id": fencers.get(side, {}).get("athlete_id"), "f": f,
                         "frames": f.frame.to_numpy(int), "y": y, "label_source": src,
                         "evidence_ok": evidence_ok(f)})       # computed ONCE per sequence (was once per frame)
    return {"seqs": seqs, "excluded": excluded}


def _frame_table(seqs) -> tuple[np.ndarray, pd.DataFrame]:
    ys, meta = [], []
    for si, s in enumerate(seqs):
        for i, lab in enumerate(s["y"]):
            if lab is None:
                continue
            ys.append(lab)
            meta.append({"seq": si, "i": i, "touch_id": s["touch_id"], "bout_id": s["bout_id"], "side": s["side"],
                         "frame": int(s["frames"][i]), "label_source": s["label_source"][i],
                         "evidence_ok": bool(s["evidence_ok"][i]), "athlete_id": s["athlete_id"]})
    return np.asarray(ys), pd.DataFrame(meta)


def _dataset_version(M: pd.DataFrame, y) -> str:
    cols = ["touch_id", "side", "frame", "label_source"]
    payload = json.dumps([M[cols].values.tolist() if len(M) else [], list(map(str, y))], default=str)
    return "ds_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def build_dataset(store, W=15, touch_ids=None) -> dict:
    """Per-frame windowed rows for E1 (kept for scripts that read the old format)."""
    d = build_sequences(store, touch_ids)
    y, M = _frame_table(d["seqs"])
    if len(M):
        Xs = [window_features(s["f"], W) for s in d["seqs"]]
        X = np.vstack([Xs[r.seq][r.i] for r in M.itertuples()])
    else:
        X = np.zeros((0, len(FEATURE_COLUMNS) * len(STATS)))
    return {"X": X, "y": y, "meta": M.drop(columns=["seq", "i"], errors="ignore"), "excluded": d["excluded"],
            "dataset_version": _dataset_version(M, y), "W": W}


# ------------------------------------------------------------------ scoring
def score(y_true, y_pred_raw, abstain, classes) -> dict:
    """y_pred with abstentions as the literal 'no_call' (counted wrong in the primary metric)."""
    y_true = np.asarray(y_true)
    y_pred = np.where(abstain, "no_call", y_pred_raw)
    cov = ~abstain
    per_class = {}
    for c in classes:
        tp = int(np.sum((y_pred == c) & (y_true == c)))
        per_class[c] = {"support": int(np.sum(y_true == c)), "tp": tp,
                        "pred": int(np.sum(y_pred == c))}
    return {
        "n_frames": int(len(y_true)),
        "macro_f1_all_frames": round(ev.macro_f1(y_true, y_pred, classes), 4),
        "coverage": round(float(cov.mean()), 4) if len(cov) else None,
        "abstention_rate": round(float(abstain.mean()), 4) if len(cov) else None,
        "n_abstained": int(abstain.sum()),
        "macro_f1_covered_frames": round(ev.macro_f1(y_true[cov], y_pred[cov], classes), 4) if cov.any() else None,
        "per_class": per_class,
        "note": "primary = macro_f1_all_frames (no_call counts as an error); covered-only F1 is secondary",
    }


def confusion(y_true, y_pred, classes) -> dict:
    """Rows = truth, columns = prediction (including no_call)."""
    cols = list(classes) + ["no_call"]
    return {"labels": cols, "matrix": [[int(np.sum((np.asarray(y_true) == a) & (np.asarray(y_pred) == b))) for b in cols]
                                       for a in classes]}


def _fit_temperature(logits, y_idx) -> float:
    best, bestT = np.inf, 1.0
    for T in np.exp(np.linspace(np.log(0.25), np.log(8), 200)):
        z = logits / T
        z = z - z.max(1, keepdims=True)
        lp = z - np.log(np.exp(z).sum(1, keepdims=True))
        nll = -lp[np.arange(len(y_idx)), y_idx].mean()
        if nll < best:
            best, bestT = nll, float(T)
    return bestT


def apply_temperature(P, T):
    if not T:
        return P
    z = np.log(np.clip(P, 1e-9, 1)) / T
    z = z - z.max(1, keepdims=True)
    return np.exp(z) / np.exp(z).sum(1, keepdims=True)


def _weights(y, classes):
    cnt = {c: max(1, int(np.sum(y == c))) for c in classes}
    return np.array([1.0 / np.sqrt(cnt[v]) for v in y])


# ------------------------------------------------------------------ E1 adapter
class XGBMethod:
    name = "xgboost"
    label = "XGBoost (E1)"

    def __init__(self, W=15):
        self.W = W
        self._win = {}

    def _windows(self, s):
        key = id(s["f"])
        if key not in self._win:
            self._win[key] = window_features(s["f"], self.W)
        return self._win[key]

    def fit(self, seqs, classes, seed=0):
        import xgboost as xgb
        X, y = [], []
        for s in seqs:
            Xw = self._windows(s)
            for i, lab in enumerate(s["y"]):
                if lab is not None and lab in classes:
                    X.append(Xw[i])
                    y.append(lab)
        X, y = np.vstack(X), np.asarray(y)
        yi = np.array([classes.index(v) for v in y])
        m = xgb.XGBClassifier(**XGB_PARAMS, num_class=len(classes), random_state=seed, tree_method="hist",
                              eval_metric="mlogloss")
        m.fit(X, yi, sample_weight=_weights(y, classes))
        return {"model": m, "classes": list(classes)}

    def predict(self, fitted, s):
        return fitted["model"].predict_proba(self._windows(s))

    def save(self, fitted, path_base):
        path = path_base + ".json"
        fitted["model"].save_model(path)
        return path

    def config(self):
        return {"W": self.W, "params": XGB_PARAMS, "feature_columns": FEATURE_COLUMNS}


# ------------------------------------------------------------------ the shared protocol
def run_protocol(store, method, test_bouts=None, seed=0, report=None, extra_meta=None) -> dict:
    d = build_sequences(store)
    seqs = d["seqs"]
    y, M = _frame_table(seqs)
    if len(y) == 0:
        raise ValueError("no labelled frames with features: annotate touches and run analysis first")
    if test_bouts is None:                      # default: every bout whose video is marked "Test"
        test_bouts = store.test_bout_ids()
    test_bouts = set(test_bouts or [])
    is_test = M.bout_id.isin(test_bouts).values
    if not (~is_test).any():
        raise ValueError("every labelled bout is marked as a test bout: mark at least one video as Training")
    classes = sorted(set(y[~is_test]))
    if len(classes) < 2:
        raise ValueError(f"need >= 2 footwork classes in training data, found {classes}")
    dev_bouts = sorted(set(M.bout_id[~is_test]))
    seq_bout = np.array([s["bout_id"] for s in seqs])
    seq_test = np.isin(seq_bout, list(test_bouts))
    warnings_ = []
    if test_bouts:
        dev_ath = set(M.athlete_id[~is_test].dropna())
        test_ath = set(M.athlete_id[is_test].dropna())
        if dev_ath & test_ath:
            warnings_.append(f"athlete leakage: {sorted(dev_ath & test_ath)} appear in both dev and test bouts")
        if M.athlete_id.isna().any():
            warnings_.append("some fencers have no athlete_id: athlete isolation cannot be verified")
    ev_ok = M.evidence_ok.values
    keys = (M.touch_id + "|" + M.side + "|" + M.frame.astype(str)).values

    def probs_for(fitted, mask_rows, T=None):
        """Per-frame probabilities (over `classes`) for the selected frame rows."""
        rows = M[mask_rows]
        out = np.zeros((len(rows), len(classes)))
        fc = fitted["classes"]
        cols = [classes.index(c) for c in fc]
        cache = {}
        for r_i, r in enumerate(rows.itertuples()):
            if r.seq not in cache:
                cache[r.seq] = apply_temperature(method.predict(fitted, seqs[r.seq]), T)
            out[r_i, cols] = cache[r.seq][r.i]
        return out

    folds, oof_pred, oof_mask = [], np.array([None] * len(y), dtype=object), np.zeros(len(y), bool)
    cv_mode = "leave_one_bout_out"
    if len(dev_bouts) >= 2:
        for k, b in enumerate(dev_bouts):
            tr_seq = [s for j, s in enumerate(seqs) if not seq_test[j] and s["bout_id"] != b]
            va = (~is_test) & (M.bout_id.values == b)
            trc = sorted(set(lab for s in tr_seq for lab in s["y"] if lab is not None))
            if len(trc) < 2:
                folds.append({"held_out_bout": b, "skipped": "training fold has < 2 classes"})
                continue
            fitted = method.fit(tr_seq, trc, seed)
            P = probs_for(fitted, va)
            pred = np.array(classes)[P.argmax(1)]
            abst = ~ev_ok[va]
            folds.append({"held_out_bout": b, **score(y[va], pred, abst, classes)})
            oof_pred[va] = np.where(abst, "no_call", pred)
            oof_mask |= va
            if report:
                report(0.1 + 0.6 * (k + 1) / len(dev_bouts))
    elif len(set(M.touch_id[~is_test])) >= 2:
        # Small-data fallback (v0.4.0): one labelled bout cannot be split by bout, so hold out one
        # touch at a time instead. Same fencers and camera on both sides of the split, so this number
        # is optimistic; it shows the training loop works, it is not evidence the model generalises.
        dev_touches = sorted(set(M.touch_id[~is_test]))
        groups = [dev_touches[i::min(5, len(dev_touches))] for i in range(min(5, len(dev_touches)))]
        for k, g in enumerate(groups):
            gset = set(g)
            tr_seq = [s for j, s in enumerate(seqs) if not seq_test[j] and s["touch_id"] not in gset]
            va = (~is_test) & M.touch_id.isin(gset).values
            trc = sorted(set(lab for s in tr_seq for lab in s["y"] if lab is not None))
            if len(trc) < 2:
                folds.append({"held_out_touches": g, "skipped": "training fold has < 2 classes"})
                continue
            fitted = method.fit(tr_seq, trc, seed)
            P = probs_for(fitted, va)
            pred = np.array(classes)[P.argmax(1)]
            abst = ~ev_ok[va]
            folds.append({"held_out_touches": g, **score(y[va], pred, abst, classes)})
            oof_pred[va] = np.where(abst, "no_call", pred)
            oof_mask |= va
            if report:
                report(0.1 + 0.6 * (k + 1) / len(groups))
        cv_mode = "within_bout_by_touch"
        warnings_.append("only one labelled training bout: scores come from holding out touches of the SAME bout "
                         "(optimistic). Label a second bout to get a real held-out-bout score.")
    else:
        warnings_.append("fewer than 2 labelled touches: the model was trained but no score can be computed yet")
    if len(dev_bouts) < ev.MIN_RELIABLE_CLUSTERS:
        warnings_.append("fewer than 5 bouts: report raw counts only; intervals are unreliable (PRD §10.2)")

    T, calib_bout, calib_report = None, None, None
    dev = ~is_test
    dev_seqs = [s for j, s in enumerate(seqs) if not seq_test[j]]
    if len(dev_bouts) >= 3:
        calib_bout = dev_bouts[-1]
        final = method.fit([s for s in dev_seqs if s["bout_id"] != calib_bout], classes, seed)
        cal_mask = dev & (M.bout_id.values == calib_bout)
        Pc = probs_for(final, cal_mask)
        yi = np.array([classes.index(v) if v in classes else -1 for v in y[cal_mask]])
        keep = yi >= 0
        if keep.sum() >= 20:
            T = _fit_temperature(np.log(np.clip(Pc[keep], 1e-9, 1)), yi[keep])
            calib_report = {**ev.calibration_report(apply_temperature(Pc, T), yi, covered=ev_ok[cal_mask]),
                            "set": f"calibration bout {calib_bout}",
                            "caveat": "same frames that fitted the temperature: optimistic; the test-set report is the honest one"}
        else:
            warnings_.append("calibration bout too small (< 20 frames): left uncalibrated")
    else:
        final = method.fit(dev_seqs, classes, seed)
        warnings_.append("fewer than 3 development bouts: no temperature scaling; scores are uncalibrated")

    test_metrics = None
    if is_test.any():
        tm = is_test & M.label_source.isin(["blind", "adjudicated"]).values
        excluded_ma = int((is_test & ~tm).sum())
        if tm.any():
            P = probs_for(final, tm, T)
            pred = np.array(classes)[P.argmax(1)]
            abst = ~ev_ok[tm]
            if T:
                abst = abst | (P.max(1) < 0.5)
            yt = y[tm]
            test_metrics = {**score(yt, pred, abst, classes), "excluded_model_assisted_frames": excluded_ma,
                            "confusion": confusion(yt, np.where(abst, "no_call", pred), classes),
                            "bootstrap": ev.cluster_bootstrap(yt, np.where(abst, "no_call", pred), M.bout_id.values[tm], classes, seed=seed)}
            if T:
                yi_t = np.array([classes.index(v) if v in classes else -1 for v in yt])
                test_metrics["calibration"] = {**ev.calibration_report(P, yi_t, covered=ev_ok[tm]), "set": "frozen test bouts"}
        else:
            test_metrics = {"unavailable": "no blind/adjudicated labels in test bouts",
                            "excluded_model_assisted_frames": excluded_ma}

    model_id = new_id({"xgboost": "xgb", "tcn": "tcn"}.get(method.name, method.name))
    mdir = os.path.join(store.data_dir, "models")
    os.makedirs(mdir, exist_ok=True)
    path = method.save(final, os.path.join(mdir, model_id))
    cv_summary, cv_ci, cv_confusion = None, None, None
    done = [f for f in folds if "macro_f1_all_frames" in f]
    if done:
        cv_summary = {k: {"mean": round(float(np.mean([f[k] for f in done if f[k] is not None])), 4),
                          "std": round(float(np.std([f[k] for f in done if f[k] is not None])), 4)}
                      for k in ("macro_f1_all_frames", "coverage", "abstention_rate")}
        cv_ci = ev.cluster_bootstrap(y[oof_mask], oof_pred[oof_mask].astype(str), M.bout_id.values[oof_mask], classes, seed=seed)
        cv_confusion = confusion(y[oof_mask], oof_pred[oof_mask].astype(str), classes)
        with open(os.path.join(mdir, f"{model_id}.oof.json"), "w") as fh:
            json.dump({"keys": keys[oof_mask].tolist(), "y_true": y[oof_mask].tolist(),
                       "y_pred": oof_pred[oof_mask].astype(str).tolist(), "groups": M.bout_id.values[oof_mask].tolist(),
                       "classes": classes}, fh)
    dsv = _dataset_version(M, y)
    metrics = {"folds": folds, "cv_summary": cv_summary, "cv_pooled_ci": cv_ci, "cv_confusion": cv_confusion,
               "test": test_metrics, "calibration": calib_report, "warnings": warnings_,
               "n_frames_train": int(dev.sum()), "excluded_frames": d["excluded"],
               "label_sources": M.label_source.value_counts().to_dict(),
               "class_counts": {c: int(np.sum(y[dev] == c)) for c in classes}, "classes": classes,
               "dev_bouts": dev_bouts, "test_bouts": sorted(test_bouts), "calibration_bout": calib_bout,
               "cv_mode": cv_mode if done else None, "label_set": store.tax.preset}
    meta = {"model_id": model_id, "method": method.name, "classes": classes, "temperature": T,
            "dataset_version": dsv, "seed": seed, **method.config(), **(extra_meta or {})}
    store.db.insert("models", {"model_id": model_id, "method": method.name, "path": path, "classes_json": json.dumps(classes),
                               "metrics_json": json.dumps(metrics, default=str), "config_json": json.dumps(meta, default=str),
                               "dataset_version": dsv, "created_at": now_iso()})
    with open(os.path.join(mdir, f"{model_id}.meta.json"), "w") as fh:
        json.dump({**meta, "metrics": metrics}, fh, indent=2, default=str)
    return {"model_id": model_id, "method": method.name, **metrics}


def train(store, W=15, test_bouts=None, seed=0, report=None) -> dict:
    """E1 XGBoost."""
    return run_protocol(store, XGBMethod(W), test_bouts=test_bouts, seed=seed, report=report)


# ------------------------------------------------------------------ inference / registry
def load_model_meta(store, model_id) -> dict:
    r = store.db.one("SELECT * FROM models WHERE model_id=?", (model_id,))
    if not r:
        raise ValueError(f"model {model_id} not found")
    path = r["path"]
    if path and not os.path.exists(path):          # app folder renamed or moved: the file is still in data/models
        cand = os.path.join(store.data_dir, "models", os.path.basename(path.replace("\\", "/")))
        if os.path.exists(cand):
            store.db.update("models", "model_id", model_id, {"path": cand})
            path = cand
    return {**json.loads(r["config_json"]), "method": r["method"], "path": path}


_cache = {}


def predict_frames(store, model_id, f: pd.DataFrame):
    meta = load_model_meta(store, model_id)
    if meta["method"] == "tcn":
        from . import tcn
        P = tcn.predict_proba(store, model_id, f)
    else:
        import xgboost as xgb
        if model_id not in _cache:
            m = xgb.XGBClassifier()
            m.load_model(meta["path"])
            _cache[model_id] = m
        P = _cache[model_id].predict_proba(window_features(f, meta["W"]))
    P = apply_temperature(P, meta.get("temperature"))
    labs = [meta["classes"][i] for i in P.argmax(1)]
    ok = evidence_ok(f)
    labs = [lab if ok[i] else NO_EVIDENCE for i, lab in enumerate(labs)]
    return labs, P


def list_models(store):
    out = []
    for r in store.db.q("SELECT * FROM models ORDER BY created_at DESC"):
        out.append({"model_id": r["model_id"], "method": r["method"], "created_at": r["created_at"],
                    "dataset_version": r["dataset_version"], "classes": json.loads(r["classes_json"]),
                    "metrics": json.loads(r["metrics_json"]), "config": json.loads(r["config_json"])})
    return out


def compare(store, model_a, model_b) -> dict:
    """T2: compare two models on the frames both predicted out-of-fold (PRD §10.3 decision rule)."""
    d = os.path.join(store.data_dir, "models")
    out = {}
    for k, mid in (("a", model_a), ("b", model_b)):
        meta = load_model_meta(store, mid)
        p = os.path.join(d, f"{mid}.oof.json")
        if not os.path.exists(p):
            return {"available": False, "reason": f"{mid} has no out-of-fold predictions (needs >= 2 development bouts)"}
        out[k] = (meta, json.load(open(p)))
    (ma, oa), (mb, ob) = out["a"], out["b"]
    names = {"xgboost": "E1", "tcn": "E2"}
    na, nb = names.get(ma["method"], model_a), names.get(mb["method"], model_b)
    if na == nb:
        na, nb = f"{na}:{model_a}", f"{nb}:{model_b}"
    res = ev.compare_models(oa, ob, na, nb)
    return {"model_a": model_a, "model_b": model_b, "method_a": ma["method"], "method_b": mb["method"],
            "same_dataset": ma.get("dataset_version") == mb.get("dataset_version"), **res}


def readiness(store) -> dict:
    """What can be trained right now, in plain words (Insights > Models). Training is never blocked
    by a small dataset; the steps below only say which numbers the data can support."""
    d = build_sequences(store)
    y, M = _frame_table(d["seqs"])
    test = set(store.test_bout_ids())
    names = {b["bout_id"]: b["name"] for b in store.db.q("SELECT bout_id, name FROM bouts")}
    if len(M):
        tr = M[~M.bout_id.isin(test)]
        train_bouts, test_bouts = sorted(set(tr.bout_id)), sorted(set(M.bout_id) & test)
        counts = {c: int(np.sum(np.asarray(y)[(~M.bout_id.isin(test)).values] == c)) for c in sorted(set(y))}
        n_touches = int(tr.touch_id.nunique())
    else:
        train_bouts, test_bouts, counts, n_touches = [], [], {}, 0
    nb, nc = len(train_bouts), sum(1 for v in counts.values() if v > 0)
    can = nb >= 1 and nc >= 2
    steps = [
        {"need": "1 labelled bout with at least 2 different labels", "ok": can, "gives": "a model can be trained"},
        {"need": "2 labelled bouts", "ok": nb >= 2, "gives": "a fair score (train on some bouts, test on another)"},
        {"need": "3 labelled bouts", "ok": nb >= 3, "gives": "confidence values that can be trusted (calibration)"},
        {"need": "5 labelled bouts", "ok": nb >= 5, "gives": "a reliable error range around the score"},
        {"need": "1 bout marked Test with blind labels", "ok": len(test_bouts) >= 1,
         "gives": "a sealed test the model never saw"},
    ]
    why = None
    if not can:
        why = ("No labelled frames yet: label at least one touch (and run Analyze on it)." if not len(M) else
               "All labelled bouts are marked Test: mark at least one video as Training." if nb == 0 else
               "Only one label is used so far: a model needs at least two different labels to tell apart.")
    return {"can_train": can, "why_not": why, "train_bouts": [names.get(b, b) for b in train_bouts],
            "test_bouts": [names.get(b, b) for b in test_bouts], "n_train_touches": n_touches,
            "frames_per_label": counts, "excluded_frames": d["excluded"], "steps": steps,
            "label_set": store.tax.preset}
