"""E2: bidirectional (non-causal) TCN, seq2seq per frame (PRD §7.4), trained under the SAME protocol as
E1 (ml.run_protocol): leave-one-bout-out CV, test firewall, temperature scaling, abstention scoring.

Architecture (PRD §7.4 a-d): 1x1 conv -> 64 channels; 6 residual blocks, each a non-causal dilated conv
(kernel 3, dilations 1,2,4,8,16,32 -> 127-frame receptive field) + ReLU + 1x1 conv + spatial dropout 0.2;
1x1 head for Layer-B footwork. About 0.1M parameters.

Loss: class-weighted cross-entropy (weights ~ 1/sqrt(freq), training folds only) on labelled frames +
lambda_s * T-MSE, MS-TCN++'s truncated smoothing loss on differences of LOG-probabilities between adjacent
frames (previous frame detached, clamped at tau^2, tau = 4, lambda_s = 0.15) — not a plain MSE on
probabilities.

Training: AdamW lr 1e-3, weight decay 0.01, 5-epoch linear warm-up then cosine decay, batch 16, up to
100 epochs, early stop after 12 epochs without Macro-F1 improvement on an inner early-stopping bout
(never the held-out fold, never the test set), gradient clipping 1.0. Augmentation: random
sub-sequences of 64-105 frames and 5-10% random feature dropout.

Inputs: the same per-frame kinematic features E1 uses (FEATURE_COLUMNS, PRD §6.5), standardised with
training-fold statistics only, plus one missing-value indicator per feature (PRD "TCN-D" variant).
Raw normalised skeletons (TCN-A/B/C ablations) are not wired in yet.
"""
from __future__ import annotations

import math
import os

import numpy as np

from .pipeline.features import FEATURE_COLUMNS

CFG = dict(channels=64, n_blocks=6, kernel=3, dropout=0.2, lr=1e-3, weight_decay=0.01, warmup=5, epochs=100,
           patience=12, batch=16, tau=4.0, lambda_s=0.15, crop_min=64, crop_max=105, feat_drop=(0.05, 0.10),
           grad_clip=1.0, min_epochs=10)


def _torch():
    import torch
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    return torch


def build_net(in_dim, n_classes, cfg=CFG):
    torch = _torch()
    nn = torch.nn

    class Block(nn.Module):
        def __init__(self, c, d):
            super().__init__()
            self.conv = nn.Conv1d(c, c, cfg["kernel"], padding=d, dilation=d)   # non-causal: sees past and future
            self.out = nn.Conv1d(c, c, 1)
            self.drop = nn.Dropout1d(cfg["dropout"])                               # spatial (channel) dropout

        def forward(self, x):
            return x + self.drop(self.out(torch.relu(self.conv(x))))

    class TCN(nn.Module):
        def __init__(self):
            super().__init__()
            c = cfg["channels"]
            self.inp = nn.Conv1d(in_dim, c, 1)
            self.blocks = nn.Sequential(*[Block(c, 2 ** i) for i in range(cfg["n_blocks"])])
            self.head = nn.Conv1d(c, n_classes, 1)

        def forward(self, x):          # x: [B, D, T] -> logits [B, C, T]
            return self.head(self.blocks(self.inp(x)))

    return TCN()


def receptive_field(cfg=CFG) -> int:
    return 1 + sum((cfg["kernel"] - 1) * 2 ** i for i in range(cfg["n_blocks"]))


def tmse_loss(logits, valid_pairs, tau=CFG["tau"]):
    """MS-TCN++ truncated MSE: mean over valid adjacent frame pairs and classes of
    min((log p_t - log p_{t-1}.detach())^2, tau^2)."""
    torch = _torch()
    lp = torch.log_softmax(logits, dim=1)
    d = (lp[:, :, 1:] - lp.detach()[:, :, :-1]) ** 2
    d = torch.clamp(d, max=tau * tau)
    m = valid_pairs.unsqueeze(1).expand_as(d)
    return (d * m).sum() / m.sum().clamp(min=1)


def _standardise(X, mean, std):
    Z = (X - mean) / std
    miss = np.isnan(Z)
    Z = np.where(miss, 0.0, Z)
    return np.concatenate([Z, miss.astype(float)], axis=1).astype(np.float32)   # [T, 2D]


class TCNMethod:
    name = "tcn"
    label = "Bidirectional TCN (E2)"

    def __init__(self, cfg=None):
        self.cfg = {**CFG, **(cfg or {})}

    # ---- protocol interface
    def fit(self, seqs, classes, seed=0):
        torch = _torch()
        rng = np.random.default_rng(seed)
        torch.manual_seed(seed)
        cfg = self.cfg
        allX = np.vstack([s["f"][FEATURE_COLUMNS].to_numpy(float) for s in seqs])
        with np.errstate(all="ignore"):
            mean = np.nanmean(allX, 0)
            std = np.nanstd(allX, 0)
        mean = np.where(np.isfinite(mean), mean, 0.0)
        std = np.where(np.isfinite(std) & (std > 1e-6), std, 1.0)
        data = []
        for s in seqs:
            X = _standardise(s["f"][FEATURE_COLUMNS].to_numpy(float), mean, std)
            y = np.array([classes.index(v) if (v is not None and v in classes) else -100 for v in s["y"]])
            data.append((X, y, s["bout_id"]))
        counts = np.array([max(1, sum(int((d[1] == k).sum()) for d in data)) for k in range(len(classes))], float)
        w = 1.0 / np.sqrt(counts)
        w = w / w.mean()
        # inner early-stopping bout (from the TRAINING fold only)
        bouts = sorted(set(d[2] for d in data))
        es_bout = bouts[-1] if len(bouts) >= 2 else None
        train_d = [d for d in data if d[2] != es_bout] if es_bout else data
        es_d = [d for d in data if d[2] == es_bout] if es_bout else []
        if not train_d or all((d[1] < 0).all() for d in train_d):
            train_d, es_d = data, []
        net = build_net(train_d[0][0].shape[1], len(classes), cfg)
        opt = torch.optim.AdamW(net.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
        E, warm = cfg["epochs"], cfg["warmup"]
        sched = torch.optim.lr_scheduler.LambdaLR(
            opt, lambda e: (e + 1) / warm if e < warm else 0.5 * (1 + math.cos(math.pi * (e - warm) / max(1, E - warm))))
        ce = torch.nn.CrossEntropyLoss(weight=torch.tensor(w, dtype=torch.float32), ignore_index=-100)
        best, best_state, bad = -1.0, None, 0
        for epoch in range(E):
            net.train()
            order = rng.permutation(len(train_d))
            for b0 in range(0, len(order), cfg["batch"]):
                batch = [self._augment(train_d[j], rng) for j in order[b0:b0 + cfg["batch"]]]
                X, Y, valid = self._pad(batch)
                if (Y >= 0).sum() == 0:
                    continue
                logits = net(torch.from_numpy(X))
                loss = ce(logits, torch.from_numpy(Y)) + cfg["lambda_s"] * tmse_loss(
                    logits, torch.from_numpy(valid[:, 1:] & valid[:, :-1]), cfg["tau"])
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), cfg["grad_clip"])
                opt.step()
            sched.step()
            if es_d:
                f1 = self._macro_f1(net, es_d, len(classes))
                if f1 > best + 1e-6:
                    best, bad = f1, 0
                    best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
                else:
                    bad += 1
                    if bad >= cfg["patience"] and epoch + 1 >= cfg["min_epochs"]:
                        break
        if best_state is not None:
            net.load_state_dict(best_state)
        net.eval()
        return {"net": net, "classes": list(classes), "mean": mean, "std": std, "epochs_run": epoch + 1,
                "early_stop_bout": es_bout, "early_stop_f1": round(best, 4) if es_d else None}

    def predict(self, fitted, s):
        return predict_array(fitted, s["f"][FEATURE_COLUMNS].to_numpy(float))

    def save(self, fitted, path_base):
        torch = _torch()
        path = path_base + ".pt"
        torch.save({"state_dict": fitted["net"].state_dict(), "classes": fitted["classes"], "mean": fitted["mean"],
                    "std": fitted["std"], "cfg": self.cfg, "in_dim": 2 * len(FEATURE_COLUMNS),
                    "feature_columns": FEATURE_COLUMNS}, path)
        return path

    def config(self):
        return {"cfg": self.cfg, "feature_columns": FEATURE_COLUMNS, "receptive_field": receptive_field(self.cfg),
                "inputs": "FEATURE_COLUMNS standardised on training folds + missing-value indicators"}

    # ---- helpers
    def _augment(self, item, rng):
        X, y, _ = item
        T = len(y)
        lo, hi = self.cfg["crop_min"], self.cfg["crop_max"]
        if T > lo:
            L = int(rng.integers(lo, min(hi, T) + 1))
            a = int(rng.integers(0, T - L + 1))
            X, y = X[a:a + L], y[a:a + L]
        p = rng.uniform(*self.cfg["feat_drop"])
        D = X.shape[1] // 2
        drop = rng.random((len(y), D)) < p
        X = X.copy()
        X[:, :D][drop] = 0.0
        X[:, D:][drop] = 1.0
        return X, y

    @staticmethod
    def _pad(batch):
        T = max(len(b[1]) for b in batch)
        D = batch[0][0].shape[1]
        X = np.zeros((len(batch), D, T), np.float32)
        Y = np.full((len(batch), T), -100, np.int64)
        valid = np.zeros((len(batch), T), bool)
        for i, (x, y) in enumerate(batch):
            X[i, :, :len(y)] = x.T
            Y[i, :len(y)] = y
            valid[i, :len(y)] = True
        return X, Y, valid

    def _macro_f1(self, net, data, C):
        torch = _torch()
        net.eval()
        yt, yp = [], []
        with torch.no_grad():
            for X, y, _ in data:
                if (y >= 0).sum() == 0:
                    continue
                p = net(torch.from_numpy(X.T[None])).argmax(1)[0].numpy()
                yt.append(y[y >= 0])
                yp.append(p[y >= 0])
        net.train()
        if not yt:
            return 0.0
        yt, yp = np.concatenate(yt), np.concatenate(yp)
        f1 = []
        for c in range(C):
            tp = np.sum((yp == c) & (yt == c))
            den = 2 * tp + np.sum((yp == c) & (yt != c)) + np.sum((yp != c) & (yt == c))
            f1.append(2 * tp / den if den else 0.0)
        return float(np.mean(f1))


def predict_array(fitted, X_raw) -> np.ndarray:
    torch = _torch()
    X = _standardise(X_raw, fitted["mean"], fitted["std"])
    if len(X) == 0:
        return np.zeros((0, len(fitted["classes"])))
    with torch.no_grad():
        logits = fitted["net"](torch.from_numpy(X.T[None].copy()))[0]
        return torch.softmax(logits, dim=0).T.numpy().astype(float)


_cache = {}


def load(path):
    torch = _torch()
    if path not in _cache:
        ck = torch.load(path, map_location="cpu", weights_only=False)
        net = build_net(ck["in_dim"], len(ck["classes"]), {**CFG, **ck["cfg"]})
        net.load_state_dict(ck["state_dict"])
        net.eval()
        _cache[path] = {"net": net, "classes": ck["classes"], "mean": ck["mean"], "std": ck["std"]}
    return _cache[path]


def predict_proba(store, model_id, f):
    from .ml import load_model_meta
    meta = load_model_meta(store, model_id)
    return predict_array(load(meta["path"]), f[FEATURE_COLUMNS].to_numpy(float))


def train(store, test_bouts=None, seed=0, report=None, cfg=None) -> dict:
    from .ml import run_protocol
    return run_protocol(store, TCNMethod(cfg), test_bouts=test_bouts, seed=seed, report=report)
