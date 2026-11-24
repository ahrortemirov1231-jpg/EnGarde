"""Interval utilities. All intervals are [start, end) half-open (see taxonomy.INTERVAL_CONVENTION)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class IntervalError(ValueError):
    pass


@dataclass
class Seg:
    start: int
    end: int
    label: str


def validate_interval(start, end, lo: int, hi: int, what: str = "segment"):
    """lo/hi: allowed range [lo, hi) — normally the touch analysis window."""
    if not isinstance(start, int) or not isinstance(end, int) or isinstance(start, bool) or isinstance(end, bool):
        raise IntervalError(f"{what}: start_frame/end_frame must be integers")
    if end <= start:
        raise IntervalError(f"{what}: end_frame ({end}) must be > start_frame ({start}); intervals are [start, end)")
    if start < lo or end > hi:
        raise IntervalError(f"{what}: [{start}, {end}) is outside the touch window [{lo}, {hi})")


def find_overlap(start: int, end: int, others: list[dict]) -> dict | None:
    for o in others:
        if start < o["end_frame"] and o["start_frame"] < end:
            return o
    return None


def frames_to_segments(labels: list[str]) -> list[Seg]:
    segs: list[Seg] = []
    for i, lab in enumerate(labels):
        if segs and segs[-1].label == lab and segs[-1].end == i:
            segs[-1].end = i + 1
        else:
            segs.append(Seg(i, i + 1, lab))
    return segs


def segments_to_frames(segs: list[dict], lo: int, hi: int, fill: str | None = None) -> list[str | None]:
    out: list[str | None] = [fill] * (hi - lo)
    for s in segs:
        for f in range(max(lo, s["start_frame"]), min(hi, s["end_frame"])):
            out[f - lo] = s["label"]
    return out


def median_filter_labels(labels: list[str], k: int = 5) -> list[str]:
    """Categorical median (mode within a centred window); ties keep the original label."""
    if k <= 1 or len(labels) < 2:
        return list(labels)
    h = k // 2
    out = []
    for i in range(len(labels)):
        win = labels[max(0, i - h): i + h + 1]
        vals, counts = np.unique(np.asarray(win, dtype=object), return_counts=True)
        best = counts.max()
        cands = [v for v, c in zip(vals, counts) if c == best]
        out.append(labels[i] if labels[i] in cands else cands[0])
    return out


def merge_short(segs: list[Seg], min_len: int = 4, protected: tuple[str, ...] = ()) -> list[Seg]:
    """Merge segments shorter than min_len into the longer neighbour (PRD §7.5: < 4 frames)."""
    segs = [Seg(s.start, s.end, s.label) for s in segs]
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, s in enumerate(segs):
            if s.end - s.start >= min_len or s.label in protected:
                continue
            left = segs[i - 1] if i > 0 else None
            right = segs[i + 1] if i + 1 < len(segs) else None
            tgt = max([n for n in (left, right) if n is not None], key=lambda n: n.end - n.start)
            if tgt is left:
                left.end = s.end
            else:
                right.start = s.start
            segs.pop(i)
            changed = True
            break
    # coalesce equal neighbours
    out: list[Seg] = []
    for s in segs:
        if out and out[-1].label == s.label and out[-1].end == s.start:
            out[-1].end = s.end
        else:
            out.append(s)
    return out


def coverage_check(segs: list[dict], lo: int, hi: int) -> dict:
    """PRD §3.5: one Layer-B label per fencer per frame with no gaps. Returns gaps and overlaps."""
    cover = np.zeros(hi - lo, dtype=int)
    for s in segs:
        a, b = max(lo, s["start_frame"]), min(hi, s["end_frame"])
        if b > a:
            cover[a - lo:b - lo] += 1
    gaps, overlaps = [], []
    for arr, val, out in ((cover == 0, None, gaps), (cover > 1, None, overlaps)):
        i = 0
        while i < len(arr):
            if arr[i]:
                j = i
                while j < len(arr) and arr[j]:
                    j += 1
                out.append([lo + i, lo + j])
                i = j
            else:
                i += 1
    return {"gaps": gaps, "overlaps": overlaps, "complete": not gaps and not overlaps}
