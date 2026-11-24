"""Tim's tagged touches from the En Garde Analytics "Model comparison" page (compare/data.json).

What the file holds (format seen on 2026-11-12, file built 2026-11-11):
    {"built": "...", "scorecard": {...}, "locks": {...},
     "touches": [{"id", "bout", "title", "point", "clip", "abs0", "dur", "existing_engine",
                  "gold":   [[side, start_s, end_s, LABEL], ...],      # human footwork tags ("Human Gold")
                  "intent": [[side, start_s, end_s, LABEL], ...],      # human intent tags
                  "lanes":  {model: {"L": [[start_s, end_s, LABEL, conf], ...], "R": [...]}},
                  "disagree": {...}}, ...]}
Times are seconds from the start of the touch clip; side is "L" or "R" (screen position).

This module only reads that file. It does three things:
  1. maps Tim's 24 gold footwork labels onto the 8 classes his models predict (TIM8),
  2. scores any per-moment predictions against the gold tags on a 1/30 s grid, the same way the
     Model comparison scorecard does (EXACT / PARTIAL same family / WRONG / NO READ),
  3. turns gold tags into per-frame labels so the touches can be loaded into our Store.

ASSUMPTION (to confirm with Andre, item 10 in the team's email): the gold -> TIM8 mapping and the
family groups below are ours, reverse-engineered from the page. With them, our scorer reproduces the
page's headline for XGBoost within about 2 points (page: 78.8 / 10.6 / 10.6; ours: about 80 / 10 / 10).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

GRID_FPS = 30
TIM8 = ["EN_GARDE", "STEPS_IN_PLACE", "STEPS_FORWARD", "STEPS_BACK", "BACK_FAMILY",
        "ADVANCE_LUNGE", "LUNGE", "RECOVERING"]
TIM_MODELS = ["existing", "xgb", "tcn", "lgb"]
MODEL_NAMES = {"existing": "Existing AI (clf3)", "xgb": "XGBoost", "tcn": "TCN", "lgb": "LightGBM (pose26)"}
SIDES = {"L": "left", "R": "right"}

# Gold footwork tag -> TIM8 class. None = not a footwork movement (blade actions); counted as WRONG
# for every model, like an unreadable gold tag, so all models face the same denominator.
GOLD_TO_TIM8: dict[str, str | None] = {
    "EN_GARDE": "EN_GARDE",
    "LUNGE": "LUNGE", "FLYING_LUNGE": "LUNGE",
    "ADVANCE_LUNGE": "ADVANCE_LUNGE", "DOUBLE_ADVANCE_LUNGE": "ADVANCE_LUNGE", "ADVANCE_FLUNGE": "ADVANCE_LUNGE",
    "RECOVERING": "BACK_FAMILY", "JUMP_BACK": "BACK_FAMILY", "PULL_ATTEMPT": "BACK_FAMILY",
    "PULL_PARRY": "STEPS_BACK", "PULL_PARRY_RIPOSTE": "STEPS_BACK",
    "ADVANCE": "STEPS_FORWARD", "ADVANCES": "STEPS_FORWARD", "ADVANCE_APPEL": "STEPS_FORWARD",
    "JUMP_FORWARD": "STEPS_FORWARD", "STEP_FINISH": "STEPS_FORWARD",
    "HALF_JUMP": "STEPS_IN_PLACE", "HALF_JUMP_DRIBBLE": "STEPS_IN_PLACE", "APPEL": "STEPS_IN_PLACE",
    "BACK_FOOT_HALF_JUMP": "STEPS_IN_PLACE",
    "ATTACK_OFF_LINE_PARRY": None, "REPRISE_PARRY": None, "REPRISE_LONG_ATTACK": None, "PARRY_ATTEMPT": None,
}

# Tim's raw tag (compare/data.json, the file from Andre) -> one of Tim's ten label names used in the team
# workspace. The raw tag is ALSO kept on every label (label_version = "tim10 (Tim tag: HALF_JUMP)"), so
# no detail from Andre's file is lost and this table can be changed and re-applied at any time.
# ASSUMPTIONS, to confirm with Tim: small in-place footwork (half jumps, appels) is filed under
# "En garde"; "recovering" (coming back from a lunge) under "Pull/retreat". Blade-only tags get no label.
GOLD_TO_TEN: dict[str, str | None] = {
    "EN_GARDE": "En garde",
    "HALF_JUMP": "En garde", "HALF_JUMP_DRIBBLE": "En garde", "APPEL": "En garde", "BACK_FOOT_HALF_JUMP": "En garde",
    "ADVANCE": "Step forward", "ADVANCES": "Step forward", "ADVANCE_APPEL": "Step forward",
    "JUMP_FORWARD": "Step forward", "STEP_FINISH": "Step forward",
    "ADVANCE_LUNGE": "Advance \u2192 lunge", "DOUBLE_ADVANCE_LUNGE": "Advance \u2192 lunge",
    "ADVANCE_FLUNGE": "Advance \u2192 lunge",
    "LUNGE": "Lunge", "FLYING_LUNGE": "Lunge",
    "REPRISE_LONG_ATTACK": "Reprise attack", "REPRISE_PARRY": "Reprise attack",
    "PULL_ATTEMPT": "Pull/retreat", "PULL_PARRY": "Pull/retreat", "PULL_PARRY_RIPOSTE": "Pull/retreat",
    "JUMP_BACK": "Pull/retreat", "RECOVERING": "Pull/retreat",
    "ATTACK_OFF_LINE_PARRY": None, "PARRY_ATTEMPT": None,
}


def gold_runs_ten(touch: "TimTouch", side: str, fps: int = GRID_FPS) -> list[tuple[int, int, str, str]]:
    """[(start, end, ten_name_label, raw_tag)] half-open frame runs for one side. A new run starts
    whenever the raw tag changes, so each label keeps the exact tag it came from."""
    runs: list[list] = []
    for i in range(n_frames(touch, fps)):
        raw = gold_at(touch, side, i / fps)
        lab = GOLD_TO_TEN.get(raw) if raw else None
        if lab is None:
            continue
        if runs and runs[-1][3] == raw and runs[-1][1] == i:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, lab, raw])
    return [tuple(r) for r in runs]


# Movement families for PARTIAL credit (same family, different word).
FAMILY = {"EN_GARDE": "still", "STEPS_IN_PLACE": "still", "STEPS_FORWARD": "forward",
          "ADVANCE_LUNGE": "lunge", "LUNGE": "lunge",
          "RECOVERING": "back", "STEPS_BACK": "back", "BACK_FAMILY": "back"}

# Our MVP 4-class taxonomy -> nearest TIM8 class (used to score the rule baseline / 4-class models).
OURS4_TO_TIM8 = {"Advance": "STEPS_FORWARD", "Retreat": "STEPS_BACK", "Lunge": "LUNGE", "Other/Still": "EN_GARDE"}


def to_tim8(label: str | None) -> str | None:
    """Accept TIM8 labels, our 4-class labels, or raw gold tags; return a TIM8 class or None."""
    if label is None:
        return None
    if label in FAMILY:
        return label
    if label in OURS4_TO_TIM8:
        return OURS4_TO_TIM8[label]
    return GOLD_TO_TIM8.get(label)


@dataclass
class TimTouch:
    id: str
    bout: str
    title: str
    point: int | None
    clip: str
    abs0: float
    dur: float
    gold: list = field(default_factory=list)      # [(side, start_s, end_s, raw_label)]
    intent: list = field(default_factory=list)
    lanes: dict = field(default_factory=dict)     # model -> {"L": [(s, e, label, conf)], "R": [...]}
    existing_engine: str | None = None

    @property
    def left_name(self) -> str:
        return self.title.split(" vs ")[0].strip() if " vs " in self.title else ""

    @property
    def right_name(self) -> str:
        return self.title.split(" vs ")[1].strip() if " vs " in self.title else ""


class TimDataError(ValueError):
    pass


def load(path: str) -> tuple[dict, list[TimTouch]]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "touches" not in d:
        raise TimDataError("not a Model comparison data file: no 'touches' list")
    touches = []
    for i, t in enumerate(d["touches"]):
        try:
            touches.append(TimTouch(
                id=str(t["id"]), bout=str(t["bout"]), title=t.get("title", ""), point=t.get("point"),
                clip=t["clip"], abs0=float(t.get("abs0") or 0), dur=float(t["dur"]),
                gold=[tuple(g) for g in t.get("gold") or []], intent=[tuple(g) for g in t.get("intent") or []],
                lanes={m: {s: [tuple(x) for x in (v or {}).get(s, [])] for s in ("L", "R")}
                       for m, v in (t.get("lanes") or {}).items()},
                existing_engine=t.get("existing_engine")))
        except (KeyError, TypeError, ValueError) as e:
            raise TimDataError(f"touch #{i}: missing or bad field ({e})") from e
    meta = {k: d.get(k) for k in ("built", "scorecard", "locks")}
    return meta, touches


def summary(touches: list[TimTouch]) -> dict:
    bouts: dict[str, dict] = {}
    raw: dict[str, int] = {}
    for t in touches:
        b = bouts.setdefault(t.bout, {"title": t.title, "touches": 0})
        b["touches"] += 1
        for g in t.gold:
            raw[g[3]] = raw.get(g[3], 0) + 1
    unmapped = sorted(k for k in raw if k not in GOLD_TO_TIM8)
    return {"touches": len(touches), "bouts": bouts, "gold_labels": dict(sorted(raw.items(), key=lambda x: -x[1])),
            "unknown_gold_labels": unmapped}


def gold_at(touch: TimTouch, side: str, t: float) -> str | None:
    """Raw gold label covering time t for side 'L'/'R'. Overlaps: the later tag in the list wins."""
    lab = None
    for g in touch.gold:
        if g[0] == side and g[1] <= t < g[2]:
            lab = g[3]
    return lab


def lane_at(segs, t: float) -> str | None:
    for s in segs:
        if s[0] <= t < s[1]:
            return s[2]
    return None


def n_frames(touch: TimTouch, fps: int = GRID_FPS) -> int:
    return max(1, math.ceil(touch.dur * fps - 1e-9))


def gold_frames(touch: TimTouch, side: str, fps: int = GRID_FPS) -> list[str | None]:
    """Per-frame TIM8 labels for one side (None = untagged or a blade-only tag)."""
    return [to_tim8(gold_at(touch, side, i / fps)) for i in range(n_frames(touch, fps))]


def frames_to_runs(labels: list[str | None]) -> list[tuple[int, int, str]]:
    """[(start, end, label)] half-open runs of identical non-None labels."""
    runs: list[list] = []
    for i, lab in enumerate(labels):
        if lab is None:
            continue
        if runs and runs[-1][2] == lab and runs[-1][1] == i:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, lab])
    return [tuple(r) for r in runs]


def score(touches: list[TimTouch], predict, fps: int = GRID_FPS) -> dict:
    """Score a predictor against gold on a 1/fps grid.

    predict(touch, side, t_seconds) -> label (any vocabulary to_tim8 understands), "no_call", or None.
    None and "no_call" both count as NO READ. Only moments with a gold tag are scored.
    """
    dt = 1.0 / fps
    tot = ex = pa = wr = nr = 0.0
    mv_tot = mv_ex = 0.0                      # moments whose gold tag is a footwork movement (not EN_GARDE)
    cls: dict[str, list[float]] = {}          # gold class -> [seconds, exact seconds]
    conf: dict[str, float] = {}
    per_bout: dict[str, dict] = {}
    for t in touches:
        pb = per_bout.setdefault(t.bout, {"gold_seconds": 0.0, "exact": 0.0})
        for side in ("L", "R"):
            for i in range(n_frames(t, fps)):
                x = i * dt
                g = gold_at(t, side, x)
                if g is None:
                    continue
                tot += dt
                pb["gold_seconds"] += dt
                gm = to_tim8(g)
                c = cls.setdefault(gm or "NOT_FOOTWORK", [0.0, 0.0])
                c[0] += dt
                moving = gm not in ("EN_GARDE", None)   # footwork movement; blade-only tags excluded
                if moving:
                    mv_tot += dt
                p = predict(t, side, x)
                if p is None or p == "no_call":
                    nr += dt
                    continue
                pm = to_tim8(p)
                if gm is not None and pm == gm:
                    ex += dt
                    c[1] += dt
                    if moving:
                        mv_ex += dt
                    pb["exact"] += dt
                elif gm is not None and pm is not None and FAMILY.get(pm) == FAMILY.get(gm):
                    pa += dt
                else:
                    wr += dt
                    key = f"{gm or g} -> {pm or p}"
                    conf[key] = conf.get(key, 0.0) + dt

    def pct(v):
        return round(100.0 * v / tot, 1) if tot else None

    return {"gold_seconds": round(tot, 1), "exact": pct(ex), "partial": pct(pa), "wrong": pct(wr), "no_read": pct(nr),
            "movement_seconds": round(mv_tot, 1),
            "exact_on_movement": round(100.0 * mv_ex / mv_tot, 1) if mv_tot else None,
            "class_recall": {k: {"seconds": round(v[0], 1), "exact": round(100.0 * v[1] / v[0], 1) if v[0] else None}
                             for k, v in sorted(cls.items(), key=lambda kv: -kv[1][0])},
            "top_confusions": [(k, round(v, 2)) for k, v in sorted(conf.items(), key=lambda kv: -kv[1])[:8]],
            "per_bout_exact": {b: (round(100 * v["exact"] / v["gold_seconds"], 1) if v["gold_seconds"] else None)
                               for b, v in per_bout.items()}}


def constant_predictor(label: str = "EN_GARDE"):
    """Trivial baseline: the same label at every moment (shows how much 'exact' comes for free)."""
    return lambda touch, side, t: label


def lane_predictor(model: str):
    def _p(touch: TimTouch, side: str, t: float):
        lane = touch.lanes.get(model, {}).get(side) or []
        return lane_at(lane, t) if lane else None
    return _p


def score_tim_models(touches: list[TimTouch], fps: int = GRID_FPS) -> dict:
    """Re-score Tim's own models with our scorer: on all touches, and where the Existing AI has reads."""
    out = {"all": {}, "where_existing_has_reads": {}}
    have = [t for t in touches if any(t.lanes.get("existing", {}).get(s) for s in ("L", "R"))]
    for m in TIM_MODELS:
        if any(t.lanes.get(m) for t in touches):
            out["all"][m] = score(touches, lane_predictor(m), fps)
            if have:
                out["where_existing_has_reads"][m] = score(have, lane_predictor(m), fps)
    return out


def published_scorecard(meta: dict) -> dict:
    """The page's own numbers, for side-by-side comparison."""
    sc = (meta or {}).get("scorecard") or {}
    return {k: {m: {kk: v.get(kk) for kk in ("gold_seconds", "exact", "partial", "wrong", "no_read")}
                for m, v in (val or {}).items() if isinstance(v, dict)}
            for k, val in sc.items() if isinstance(val, dict)}
