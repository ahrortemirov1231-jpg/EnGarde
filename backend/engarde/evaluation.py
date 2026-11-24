"""Evaluation helpers shared by every recognition model (PRD §9.2, §10.2, §10.3).

 - calibration_report: the 4 confidence bins of PRD §9.2 (accuracy and n per bin), ECE, Brier score,
   a risk-coverage curve, the AIRS over-confidence alarm and the PRD §10.3 monotonicity check.
 - cluster_bootstrap: 95% intervals resampled BY BOUT (PRD §10.2). With fewer than 5 bouts the interval
   is still computed but marked unreliable, and raw numerators/denominators are always reported.
 - compare_models: E2 vs E1 on the SAME out-of-fold frames with a paired cluster bootstrap and the
   pre-registered rule "E2 beats E1 by < 3 points, or the 95% CI includes 0: adopt E1".

Nothing here tunes anything: these functions only read predictions that were already made.
"""
from __future__ import annotations

import numpy as np

CONF_BINS = [(0.9, 1.0001, ">= 0.9"), (0.7, 0.9, "0.7-0.9"), (0.4, 0.7, "0.4-0.7"), (0.0, 0.4, "< 0.4")]
MIN_RELIABLE_CLUSTERS = 5          # PRD §10.2
E2_MIN_GAIN = 0.03                 # PRD §10.3: E2 must beat E1 by >= 3 points (Macro-F1)


def macro_f1(y_true, y_pred, classes) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    f1s = []
    for c in classes:
        tp = np.sum((y_pred == c) & (y_true == c))
        fp = np.sum((y_pred == c) & (y_true != c))
        fn = np.sum((y_pred != c) & (y_true == c))
        denom = 2 * tp + fp + fn
        f1s.append(2 * tp / denom if denom else 0.0)
    return float(np.mean(f1s)) if f1s else float("nan")


def calibration_report(P: np.ndarray, y_idx: np.ndarray, covered: np.ndarray | None = None,
                       n_ece_bins: int = 10) -> dict:
    """P: [N, C] (calibrated) class probabilities; y_idx: true class index per row (-1 = class unseen in
    training: always counted wrong). `covered` restricts the report to frames the gate did not abstain on."""
    P = np.asarray(P, float)
    y_idx = np.asarray(y_idx)
    if covered is not None:
        P, y_idx = P[covered], y_idx[covered]
    n = len(y_idx)
    if n == 0:
        return {"available": False, "reason": "no covered frames to calibrate"}
    conf = P.max(1)
    pred = P.argmax(1)
    correct = (pred == y_idx)
    bins = []
    for lo, hi, name in CONF_BINS:
        m = (conf >= lo) & (conf < hi)
        bins.append({"bin": name, "n": int(m.sum()), "correct": int(correct[m].sum()),
                     "accuracy": round(float(correct[m].mean()), 4) if m.any() else None,
                     "mean_confidence": round(float(conf[m].mean()), 4) if m.any() else None})
    # ECE on equal-width bins
    edges = np.linspace(0, 1, n_ece_bins + 1)
    ece = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        m = (conf > a) & (conf <= b) if a > 0 else (conf >= a) & (conf <= b)
        if m.any():
            ece += m.mean() * abs(correct[m].mean() - conf[m].mean())
    onehot = np.zeros_like(P)
    ok = y_idx >= 0
    onehot[np.arange(n)[ok], y_idx[ok]] = 1.0
    brier = float(np.mean(np.sum((P - onehot) ** 2, axis=1)))
    # risk-coverage: keep the most confident frames first
    order = np.argsort(-conf, kind="stable")
    cum_acc = np.cumsum(correct[order]) / np.arange(1, n + 1)
    points = sorted(set([max(1, int(round(n * q))) for q in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)]))
    rc = [{"coverage": round(k / n, 3), "accuracy": round(float(cum_acc[k - 1]), 4), "threshold": round(float(conf[order][k - 1]), 4)}
          for k in points]
    accs = [b["accuracy"] for b in bins if b["accuracy"] is not None]      # high -> low confidence
    monotonic = all(accs[i] >= accs[i + 1] for i in range(len(accs) - 1)) if len(accs) >= 2 else None
    share_09 = float((conf >= 0.9).mean())
    return {
        "available": True, "n": int(n), "accuracy": round(float(correct.mean()), 4),
        "bins": bins, "ece": round(float(ece), 4), "brier": round(brier, 4), "risk_coverage": rc,
        "share_above_0_9": round(share_09, 4),
        "overconfidence_alarm": bool(share_09 > 0.9),            # PRD §9.2 (AIRS rule)
        "accuracy_monotonic_across_bins": monotonic,
        "show_confidence_to_coaches": bool(monotonic) and not share_09 > 0.9,   # PRD §10.3, Phase 3 rule
        "note": "4-bin report per PRD §9.2; ECE uses %d equal-width bins" % n_ece_bins,
    }


def _resample_clusters(groups, B, seed):
    rng = np.random.default_rng(seed)
    uniq = np.array(sorted(set(groups)))
    pos = {g: np.flatnonzero(groups == g) for g in uniq}
    for _ in range(B):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        yield np.concatenate([pos[g] for g in pick])


def cluster_bootstrap(y_true, y_pred, groups, classes, B: int = 1000, seed: int = 0) -> dict:
    """95% interval of Macro-F1 (no_call counted wrong) with bouts as the resampling unit."""
    y_true, y_pred, groups = np.asarray(y_true), np.asarray(y_pred), np.asarray(groups)
    k = len(set(groups))
    point = macro_f1(y_true, y_pred, classes)
    out = {"metric": "macro_f1_all_frames", "point": round(point, 4), "n_frames": int(len(y_true)),
           "n_clusters": int(k), "correct_frames": int(np.sum(y_true == y_pred)), "B": B}
    if k < 2:
        return {**out, "ci95": None, "reliable": False,
                "reason": "fewer than 2 bouts: no bootstrap interval; report raw counts only"}
    vals = [macro_f1(y_true[ix], y_pred[ix], classes) for ix in _resample_clusters(groups, B, seed)]
    lo, hi = np.percentile(vals, [2.5, 97.5])
    out.update(ci95=[round(float(lo), 4), round(float(hi), 4)], reliable=bool(k >= MIN_RELIABLE_CLUSTERS))
    if k < MIN_RELIABLE_CLUSTERS:
        out["reason"] = f"only {k} bouts (< {MIN_RELIABLE_CLUSTERS}): interval is descriptive only (PRD §10.2)"
    return out


def compare_models(oof_a: dict, oof_b: dict, name_a: str = "E1", name_b: str = "E2", B: int = 1000, seed: int = 0) -> dict:
    """Paired comparison on the frames both models predicted out-of-fold.
    oof_*: {"keys": [touch_id|side|frame], "y_true": [...], "y_pred": [...], "groups": [bout_id], "classes": [...]}"""
    ia = {k: i for i, k in enumerate(oof_a["keys"])}
    common = [k for k in oof_b["keys"] if k in ia]
    if not common:
        return {"available": False, "reason": "the two models share no out-of-fold frames (different data or labels)"}
    ib = {k: i for i, k in enumerate(oof_b["keys"])}
    sa = np.array([ia[k] for k in common])
    sb = np.array([ib[k] for k in common])
    yt = np.asarray(oof_a["y_true"])[sa]
    if not np.array_equal(yt, np.asarray(oof_b["y_true"])[sb]):
        return {"available": False, "reason": "ground-truth labels differ between the two runs; retrain both on the same labels"}
    pa, pb = np.asarray(oof_a["y_pred"])[sa], np.asarray(oof_b["y_pred"])[sb]
    g = np.asarray(oof_a["groups"])[sa]
    classes = sorted(set(oof_a["classes"]) | set(oof_b["classes"]))
    fa, fb = macro_f1(yt, pa, classes), macro_f1(yt, pb, classes)
    k = len(set(g))
    res = {"available": True, "n_frames": int(len(yt)), "n_bouts": int(k),
           name_a: round(fa, 4), name_b: round(fb, 4), "difference": round(fb - fa, 4),
           "rule": f"{name_b} is adopted only if it beats {name_a} by >= {E2_MIN_GAIN:.2f} Macro-F1 AND the 95% CI of the difference excludes 0 (PRD §10.3)"}
    if k >= 2:
        d = [macro_f1(yt[ix], pb[ix], classes) - macro_f1(yt[ix], pa[ix], classes) for ix in _resample_clusters(g, B, seed)]
        lo, hi = np.percentile(d, [2.5, 97.5])
        res["difference_ci95"] = [round(float(lo), 4), round(float(hi), 4)]
        res["ci_reliable"] = bool(k >= MIN_RELIABLE_CLUSTERS)
        ci_excludes_0 = lo > 0
    else:
        res["difference_ci95"] = None
        res["ci_reliable"] = False
        ci_excludes_0 = False
    adopt_b = (fb - fa) >= E2_MIN_GAIN and ci_excludes_0
    res["decision"] = f"adopt {name_b}" if adopt_b else f"adopt {name_a}"
    res["decision_reason"] = (f"{name_b} gains {fb - fa:+.3f} and the interval excludes 0" if adopt_b else
                              (f"gain {fb - fa:+.3f} is below {E2_MIN_GAIN:.2f}" if (fb - fa) < E2_MIN_GAIN
                               else "the 95% interval of the difference includes 0"))
    if not res["ci_reliable"]:
        res["warning"] = f"fewer than {MIN_RELIABLE_CLUSTERS} bouts: the decision is provisional (PRD §10.2)"
    return res
