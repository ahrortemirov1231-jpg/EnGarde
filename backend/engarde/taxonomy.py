"""Label taxonomy and interval conventions shared by API, pipeline, exports and scoring.

Interval convention (used everywhere — UI, DB, CSV/JSON, scoring):
    [start_frame, end_frame)   half-open, in ORIGINAL video frame numbers (0-based).
    A segment covers frames start_frame .. end_frame-1.  Length = end_frame - start_frame.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

INTERVAL_CONVENTION = "[start_frame, end_frame) half-open, 0-based original video frame numbers"

# Layer B footwork — default MVP classes (PRD v1.12 §3.2). Exactly one per frame in training data.
DEFAULT_FOOTWORK = ["Advance", "Retreat", "Lunge", "Other/Still"]

# Classes that exist in the taxonomy but are NOT enabled until the sponsor defines them.
# They can be switched on via data/taxonomy.json; they are never claimed as reliably recognised.
PENDING_DEFINITION = ["Hop", "Recovery"]

# Human annotation states that are NOT footwork classes and must never collapse into Other/Still.
UNKNOWN = "Unknown"            # visible, but annotator cannot decide
UNOBSERVABLE = "Unobservable"  # the evidence is not visible (occluded / out of frame / cut)
HUMAN_STATES = [UNKNOWN, UNOBSERVABLE]
# System abstention is stored on predictions as no_call=True + no_call_reason (never a label).

LABEL_SOURCES = ["blind", "model_assisted", "adjudicated", "unknown"]
PREDICTION_SOURCES = ["rule", "model"]
EVENT_SOURCES = ["model", "rule", "expert"]

# Layer C physical events (v1.12 dropped foot_lift).
EVENT_TYPES = ["lunge_start", "lunge_land", "arm_extension_start", "arm_extension_full", "foot_contact"]
# Words that must never appear in an AIRS timeline (tactical / referee vocabulary).
FORBIDDEN_TIMELINE_WORDS = ["attack", "parry", "riposte", "reprise", "right-of-way", "right of way",
                            "priority", "preparation", "point", "touch awarded"]

ERROR_REASONS = ["tracking", "pose", "camera", "classification", "boundary", "label_ambiguity", "other"]
REVIEW_ACTIONS = ["confirm", "correct", "unknown", "unobservable", "reject"]

SIDES = ["left", "right"]

WINDOW_PRE_S = 3.0
WINDOW_POST_S = 0.5


# ---------------------------------------------------------------------------------------------
# Tim's label names (v0.4.0), as listed by the team in "Changes to Review" (Nov 2026). Spelling is
# kept exactly as given, including the arrow in "Advance → lunge".
#   movement labels: what a fencer's feet / body are doing. These are the classes a model learns.
#   phase markers:   where we are in the bout. A person can tag them, but they are NOT model classes
#                    (a marker says nothing about footwork), so marker frames are never trained on.
# v0.5.4: "Steps in place" and "Step back" added (seen by the team in Tim's tagged videos, Nov 20).
TIM_MOVEMENT = ["En garde", "Steps in place", "Step forward", "Step back", "Advance \u2192 lunge", "Lunge",
                "Reprise attack", "Pull/retreat"]
TIM_MARKERS = ["Start of touch", "End of touch", "Between touches", "No touch"]

# Old 4-class names -> Tim's names. ASSUMPTION until Tim / Andre send written definitions:
# "Other/Still" was our catch-all for "not stepping, not lunging"; Tim's "En garde" plays that role
# in his tagged data (it is about 69% of his gold time). Every relabel is written to annotation_log.
LEGACY_TO_TIM = {"Advance": "Step forward", "Retreat": "Pull/retreat", "Lunge": "Lunge", "Other/Still": "En garde"}
# Tim's data.json tag names (compare/data.json) -> the same ten labels, for importing his tagged bouts.
TIMJSON_TO_TIM = {
    "EN_GARDE": "En garde", "STEPS_IN_PLACE": "Steps in place", "ADVANCE": "Step forward", "ADVANCES": "Step forward",
    "STEPS_FORWARD": "Step forward", "ADVANCE_LUNGE": "Advance \u2192 lunge",
    "DOUBLE_ADVANCE_LUNGE": "Advance \u2192 lunge", "LUNGE": "Lunge", "FLYING_LUNGE": "Lunge",
    "REPRISE_LONG_ATTACK": "Reprise attack", "PULL_ATTEMPT": "Pull/retreat", "STEPS_BACK": "Step back",
    "BACK_FAMILY": "Pull/retreat", "JUMP_BACK": "Pull/retreat", "RECOVERING": "Pull/retreat",
}


def _key(label: str) -> str:
    """Loose key for matching label spellings: case, spaces, arrows, dashes and slashes ignored."""
    s = (label or "").lower().replace("\u2192", " ").replace("->", " ")
    return "".join(ch for ch in s if ch.isalnum())


def slug(label: str) -> str:
    """CSS-safe / file-safe form of a label, e.g. 'Advance → lunge' -> 'advance-lunge'."""
    out, prev = [], "-"
    for ch in (label or "").lower():
        ch = ch if ch.isalnum() else "-"
        if ch == "-" and prev == "-":
            continue
        out.append(ch)
        prev = ch
    return "".join(out).strip("-")


@dataclass
class Taxonomy:
    footwork: list[str]
    pending: list[str]
    markers: list[str] = field(default_factory=list)
    preset: str = "mvp4"
    legacy: dict = field(default_factory=dict)       # old label -> current label (rules, old exports)

    @property
    def annotation_labels(self) -> list[str]:
        return self.footwork + self.markers + HUMAN_STATES

    @property
    def untrained(self) -> list[str]:
        """Labels a person may use that are never used as model classes."""
        return self.markers + HUMAN_STATES

    def canon(self, label):
        """Map any known spelling (old 4-class name, Tim's JSON tag, ASCII arrow) to the label this
        data folder uses. Unknown labels are returned unchanged so validation can reject them."""
        if label is None:
            return None
        if label in self.annotation_labels:
            return label
        if label in self.legacy:
            return self.legacy[label]
        k = _key(label)
        for lab in self.annotation_labels:
            if _key(lab) == k:
                return lab
        for old, new in self.legacy.items():
            if _key(old) == k:
                return new
        return label

    def to_dict(self):
        return {
            "preset": self.preset,
            "footwork": self.footwork,
            "markers": self.markers,
            "legacy_names": self.legacy,
            "pending_definition": [p for p in self.pending if p not in self.footwork],
            "human_states": HUMAN_STATES,
            "label_sources": LABEL_SOURCES,
            "event_types": EVENT_TYPES,
            "error_reasons": ERROR_REASONS,
            "review_actions": REVIEW_ACTIONS,
            "interval_convention": INTERVAL_CONVENTION,
            "window": {"pre_s": WINDOW_PRE_S, "post_s": WINDOW_POST_S},
        }


# Tim's 8 footwork classes, as predicted by the models on his Model comparison page. Enabled with
# {"preset": "tim8"} in data/taxonomy.json (scripts/import_tim_data.py writes it). Keep a tim8 data
# folder separate from a 4-class one: the two label sets are not mixed in one training run.
TIM8_FOOTWORK = ["EN_GARDE", "STEPS_IN_PLACE", "STEPS_FORWARD", "STEPS_BACK", "BACK_FAMILY",
                 "ADVANCE_LUNGE", "LUNGE", "RECOVERING"]
PRESETS = {"mvp4": DEFAULT_FOOTWORK, "tim8": TIM8_FOOTWORK, "tim10": TIM_MOVEMENT}
# The preset a data folder gets when it has no data/taxonomy.json. Since v0.4.0 this is Tim's
# label names. Put {"preset": "mvp4"} in data/taxonomy.json to keep the old four classes.
DEFAULT_PRESET = "tim10"
TIM8_FROM_RULE = {"Advance": "STEPS_FORWARD", "Retreat": "STEPS_BACK", "Lunge": "LUNGE", "Other/Still": "EN_GARDE"}


def load_taxonomy(data_dir: str) -> Taxonomy:
    """data/taxonomy.json picks the label set: {"preset": "tim10"} (default: Tim's ten names),
    {"preset": "tim8"} (the 8 model classes on Tim's Model comparison page, used by the benchmark
    importer) or {"preset": "mvp4"} (the old Advance / Retreat / Lunge / Other-Still set, which may
    also enable extra classes with {"enabled_extra": ["Hop"]})."""
    path = os.path.join(data_dir, "taxonomy.json")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    # a folder that was configured for the old set (extra classes, no preset) stays on the old set
    preset = cfg.get("preset", "mvp4" if cfg.get("enabled_extra") else DEFAULT_PRESET)
    if preset not in PRESETS:
        raise ValueError(f"unknown taxonomy preset '{preset}'; choose one of {sorted(PRESETS)}")
    if preset == "tim10":
        legacy = {**LEGACY_TO_TIM, **TIMJSON_TO_TIM}
        return Taxonomy(footwork=list(TIM_MOVEMENT), pending=[], markers=list(TIM_MARKERS), preset=preset, legacy=legacy)
    if preset == "tim8":
        return Taxonomy(footwork=list(TIM8_FOOTWORK), pending=[], preset=preset)
    extra = [c for c in cfg.get("enabled_extra", []) if c in PENDING_DEFINITION]
    return Taxonomy(footwork=list(DEFAULT_FOOTWORK) + extra, pending=PENDING_DEFINITION, preset=preset)
