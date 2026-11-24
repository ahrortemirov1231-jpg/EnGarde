"""Exports (CSV/JSON) and re-imports. Exported files are data, not screenshots: annotations,
reviews and expert events can be re-imported to restore state exactly (frame numbers are the source
of truth; times are recomputed from the frame index and checked)."""
from __future__ import annotations

import csv
import io
import json
import os

from . import taxonomy as tx
from .db import now_iso
from .intervals import IntervalError

RECORD_FIELDS = ["video_id", "bout_id", "touch_id", "athlete_id", "side", "weapon_type", "fps", "capture_type",
                 "light_frame", "start_frame", "end_frame", "t_start_ms", "t_end_ms", "label", "label_source",
                 "annotator_id", "ambiguity_flag", "label_version", "pred_label", "class_probs", "calibrated_prob",
                 "top2_label", "no_call_reason", "track_quality", "pose_quality", "camera_motion_flag",
                 "model_version", "dataset_version", "run_id"]
EXTRA_FIELDS = ["segment_id", "kind", "no_call", "derived_from_segment_id", "interval_convention", "is_synthetic"]


# Label -> the physical word used in the blind AIRS timeline. Tactical names (e.g. "Reprise attack")
# are never printed there: the timeline may only describe movement, not what it means.
AIRS_WORD = {"Advance": "advance", "Retreat": "retreat", "Step forward": "advance", "Pull/retreat": "retreat",
             "Advance \u2192 lunge": "advance", "Reprise attack": "forward_action", "Step back": "retreat",
             "STEPS_FORWARD": "advance", "STEPS_BACK": "retreat", "BACK_FAMILY": "retreat", "ADVANCE_LUNGE": "advance"}


def _ctx(store, touch):
    v = store.get_video(touch["video_id"])
    fencers = {f["side"]: f for f in store.fencers(touch["bout_id"])}
    idx = store.frame_index(touch["video_id"])
    return v, fencers, idx


def _t_ms(store, touch, frame, idx):
    """Time of a frame boundary relative to the light. end_frame is exclusive: its time is the
    timestamp of frame end_frame (or last frame + median frame interval at the video end)."""
    t = idx["t"]
    if frame < len(t):
        return round(float((t[frame] - t[touch["light_frame"]]) * 1000), 3)
    dt = float(t[-1] - t[-2]) if len(t) > 1 else 0.0
    return round(float((t[-1] + dt - t[touch["light_frame"]]) * 1000), 3)


def segment_records(store, touch_ids=None, kind="annotation", run="latest") -> list[dict]:
    touches = [store.get_touch(t) for t in touch_ids] if touch_ids else store.list_touches()
    out = []
    for touch in touches:
        v, fencers, idx = _ctx(store, touch)
        if kind == "annotation":
            segs = store.annotations(touch["touch_id"])
            runrow = None
        else:
            segs = store.predictions(touch["touch_id"])
            runrow = store.latest_run(touch["touch_id"]) if segs else None
        for s in segs:
            out.append({
                "video_id": v["video_id"], "bout_id": touch["bout_id"], "touch_id": touch["touch_id"],
                "athlete_id": fencers.get(s["side"], {}).get("athlete_id"), "side": s["side"],
                "weapon_type": v["weapon_type"], "fps": v["fps_measured"], "capture_type": v["capture_type"],
                "light_frame": touch["light_frame"], "start_frame": s["start_frame"], "end_frame": s["end_frame"],
                "t_start_ms": _t_ms(store, touch, s["start_frame"], idx), "t_end_ms": _t_ms(store, touch, s["end_frame"], idx),
                "label": s["label"] if kind == "annotation" else None,
                "label_source": s.get("label_source"), "annotator_id": s.get("annotator_id"),
                "ambiguity_flag": s.get("ambiguity_flag"), "label_version": s.get("label_version"),
                "pred_label": s["label"] if kind == "prediction" else None,
                "class_probs": json.dumps(s["class_probs"]) if s.get("class_probs") else None,
                "calibrated_prob": s.get("calibrated_prob"), "top2_label": s.get("top2_label"),
                "no_call_reason": s.get("no_call_reason"), "track_quality": s.get("track_quality"),
                "pose_quality": s.get("pose_quality"), "camera_motion_flag": s.get("camera_motion_flag"),
                "model_version": runrow["model_version"] if runrow else None,
                "dataset_version": runrow["dataset_version"] if runrow else None,
                "run_id": s.get("run_id"), "segment_id": s["segment_id"], "kind": kind,
                "no_call": s.get("no_call"), "derived_from_segment_id": s.get("derived_from_segment_id"),
                "interval_convention": "[start_frame,end_frame)", "is_synthetic": v["is_synthetic"]})
    return out


def to_csv(rows: list[dict], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    return buf.getvalue()


# --------------------------------------------------------------------------- review.csv (PRD §9.4)
REVIEW_CSV_FIELDS = ["prediction_segment_id", "video_id", "touch_id", "side", "video_link", "start_frame", "end_frame",
                     "t_start_ms", "t_end_ms", "pred_label", "top2_label", "calibrated_prob", "prob_status",
                     "no_call", "no_call_reason", "track_quality", "pose_quality", "camera_motion_flag", "run_id",
                     "model_version",
                     # filled in by the coach:
                     "review_action", "corrected_label", "corrected_start_frame", "corrected_end_frame",
                     "error_reason", "reviewer", "comment"]


def review_csv_rows(store, touch_ids=None, base_url="http://localhost:8000"):
    rows = []
    for r in segment_records(store, touch_ids, kind="prediction"):
        run = store.db.one("SELECT calibrated, method FROM prediction_runs WHERE run_id=?", (r["run_id"],))
        rows.append({**r, "prediction_segment_id": r["segment_id"],
                     "video_link": f"{base_url}/#/review/{r['touch_id']}?frame={r['start_frame']}",
                     "prob_status": "calibrated" if run and run["calibrated"] else
                     ("none (rule baseline)" if run and run["method"] == "rule_baseline" else "uncalibrated")})
    return rows


def import_review_csv(store, text: str) -> dict:
    """Rows with review_action filled become append-only review records. Already-imported rows
    (same prediction, reviewer, action, label, bounds) are skipped so re-import is idempotent."""
    done, skipped, errors = 0, 0, []
    for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        act = (r.get("review_action") or "").strip()
        if not act:
            continue
        def _int(k):
            v = (r.get(k) or "").strip()
            return int(v) if v else None
        try:
            prev = store.db.q("SELECT * FROM review_records WHERE prediction_segment_id=? AND reviewer=? AND action=?",
                              (r["prediction_segment_id"], r.get("reviewer"), act))
            if any((p["corrected_label"] or "") == (r.get("corrected_label") or "") for p in prev):
                skipped += 1
                continue
            store.review_prediction(r["prediction_segment_id"], act, (r.get("reviewer") or "").strip(),
                                    corrected_label=(r.get("corrected_label") or "").strip() or None,
                                    corrected_start=_int("corrected_start_frame"), corrected_end=_int("corrected_end_frame"),
                                    error_reason=(r.get("error_reason") or "").strip() or None,
                                    comment=r.get("comment") or None)
            done += 1
        except Exception as e:  # noqa: BLE001 — report per row, keep going
            errors.append({"line": i, "error": f"{type(e).__name__}: {e}"})
    return {"imported": done, "skipped_duplicates": skipped, "errors": errors}


# --------------------------------------------------------------------------- AIRS timeline (PRD §11.1)
def airs_timeline(store, touch_id, arm="automated") -> dict:
    """arm='automated' -> rule/model events + latest-run intervals; arm='expert' -> expert events only.
    Only physical actions; times correspond to real frames; no referee vocabulary."""
    touch = store.get_touch(touch_id)
    idx = store.frame_index(touch["video_id"])
    lines = []
    if arm == "expert":
        evs = store.events(touch_id, sources=["expert"])
        preds = []
    else:
        run = store.latest_run(touch_id)
        evs = [e for e in store.events(touch_id, sources=["rule", "model"]) if run and e["run_id"] == run["run_id"]]
        preds = store.predictions(touch_id)
    items = []
    for e in evs:
        if e["determinable"] and e["event_frame"] is not None:
            p = f"p={e['event_prob']:.2f}, " if e["event_prob"] is not None else ""
            items.append((e["event_time_ms"], f"{e['event_time_ms']:+.0f}ms {e['side']} {e['event_type']} "
                                              f"({p}{e['source']}, frame {e['event_frame']})"))
        else:
            items.append((None, f"{e['side']} {e['event_type']}: not determinable ({e['source']}"
                                + (f"; {e['note']}" if e.get("note") else "") + ")"))
    have_evidence = {"left": False, "right": False}
    for s in preds:
        if s["no_call"]:
            a, b = _t_ms(store, touch, s["start_frame"], idx), _t_ms(store, touch, s["end_frame"], idx)
            items.append((a, f"{a:+.0f}..{b:+.0f}ms {s['side']} feet: not determinable ({s['no_call_reason']})"))
            continue
        have_evidence[s["side"]] = True
        word = AIRS_WORD.get(s["label"])   # physical words only; lunge_start is already an event
        if word:
            ms = _t_ms(store, touch, s["start_frame"], idx)
            src = "rule" if s["class_probs"] is None else "model"
            items.append((ms, f"{ms:+.0f}ms {s['side']} {word}_start ({src}, frame {s['start_frame']})"))
    if arm != "expert":
        for side in ("left", "right"):
            if not have_evidence[side]:
                items.append((None, f"{side} feet: not determinable"))
    items.sort(key=lambda it: (it[0] is None, it[0] if it[0] is not None else 0))
    lines = [txt for _, txt in items]
    text = "\n".join(lines)
    low = text.lower()
    bad = [w for w in tx.FORBIDDEN_TIMELINE_WORDS if w in low]
    if bad:
        raise ValueError(f"timeline contains referee vocabulary {bad}; refusing to export")
    lights = {}
    if touch["lamp_left"] is not None:
        lights["left"] = bool(touch["lamp_left"])
    if touch["lamp_right"] is not None:
        lights["right"] = bool(touch["lamp_right"])
    return {"context": {"lights": lights, "timeline": text}, "timeline_txt": text, "arm": arm,
            "note": "left/right = screen position (AIRS convention); times relative to the light"}


# --------------------------------------------------------------------------- stage outputs
def write_stage_outputs(store, touch_id, run_id):
    sd = store.stage_dir(touch_id)
    rows = review_csv_rows(store, [touch_id])
    with open(os.path.join(sd, "review.csv"), "w", encoding="utf-8-sig", newline="") as f:
        f.write(to_csv(rows, REVIEW_CSV_FIELDS))
    a = airs_timeline(store, touch_id)
    with open(os.path.join(sd, "airs_timeline.txt"), "w", encoding="utf-8") as f:
        f.write(a["timeline_txt"] + "\n")
    with open(os.path.join(sd, "context.json"), "w", encoding="utf-8") as f:
        json.dump(a["context"], f, indent=2)
    run = store.db.one("SELECT * FROM prediction_runs WHERE run_id=?", (run_id,))
    with open(os.path.join(sd, f"run_{run_id}.json"), "w") as f:
        json.dump({**run, "config": json.loads(run["config_json"]), "versions": json.loads(run["versions_json"]),
                   "stage_status": json.loads(run["stage_status_json"])}, f, indent=2)


def run_manifests(store, touch_ids=None):
    touches = touch_ids or [t["touch_id"] for t in store.list_touches()]
    out = []
    for tid in touches:
        for r in store.db.q("SELECT * FROM prediction_runs WHERE touch_id=? ORDER BY created_at", (tid,)):
            out.append({**{k: v for k, v in r.items() if not k.endswith("_json")},
                        "config": json.loads(r["config_json"] or "null"),
                        "versions": json.loads(r["versions_json"] or "null"),
                        "stage_status": json.loads(r["stage_status_json"] or "null")})
    return out


# --------------------------------------------------------------------------- bundle export / import
def export_bundle(store, video_id=None) -> dict:
    vids = [store.get_video(video_id)] if video_id else store.list_videos()
    out = {"format": "engarde-bundle/1", "exported_at": now_iso(), "interval_convention": tx.INTERVAL_CONVENTION,
           "label_set": store.tax.preset, "videos": []}
    for v in vids:
        vv = {k: v[k] for k in ("video_id", "title", "sha256", "n_frames", "fps_measured", "width", "height",
                                "capture_type", "weapon_type", "is_synthetic")}
        vv["bouts"] = []
        for b in store.db.q("SELECT * FROM bouts WHERE video_id=?", (v["video_id"],)):
            bb = {**b, "fencers": store.fencers(b["bout_id"]), "touches": []}
            for t in store.list_touches(bout_id=b["bout_id"]):
                idx = store.frame_index(v["video_id"])
                anns = [{**a, "t_start_ms": _t_ms(store, t, a["start_frame"], idx),
                         "t_end_ms": _t_ms(store, t, a["end_frame"], idx)} for a in store.annotations(t["touch_id"])]
                bb["touches"].append({
                    **{k: t[k] for k in ("touch_id", "seq", "light_frame", "light_time_s", "lamp_left", "lamp_right",
                                         "light_source", "start_frame", "end_frame", "window_clipped", "notes")},
                    "annotations": anns,
                    "expert_events": store.events(t["touch_id"], sources=["expert"]),
                    "reviews": store.reviews(t["touch_id"])})
            vv["bouts"].append(bb)
        out["videos"].append(vv)
    return out


def import_bundle(store, bundle: dict, target_video_id=None) -> dict:
    """Restores bouts, fencers, touches, annotations and expert events onto an already-imported
    video (matched by sha256, or target_video_id). Existing segment ids are skipped (idempotent).
    Review records are restored as-is (they reference prediction ids; skipped if that prediction
    does not exist in this database)."""
    if bundle.get("format") != "engarde-bundle/1":
        raise ValueError("not an engarde bundle")
    stats = {"touches": 0, "annotations": 0, "events": 0, "reviews": 0, "skipped": 0, "time_mismatches": []}
    for vv in bundle["videos"]:
        if target_video_id:
            v = store.get_video(target_video_id)
        else:
            v = store.db.one("SELECT * FROM videos WHERE sha256=?", (vv["sha256"],))
            if not v:
                raise ValueError(f"video with sha256 {vv['sha256'][:12]}… not imported; import the video file first")
        if v["n_frames"] != vv["n_frames"]:
            raise ValueError(f"frame count differs ({v['n_frames']} vs {vv['n_frames']}); refusing to map labels")
        for bb in vv["bouts"]:
            if not store.db.one("SELECT 1 x FROM bouts WHERE bout_id=?", (bb["bout_id"],)):
                store.db.insert("bouts", {"bout_id": bb["bout_id"], "video_id": v["video_id"], "name": bb["name"],
                                          "created_at": bb.get("created_at") or now_iso()})
                for f in bb["fencers"]:
                    store.db.insert("fencers", {k: f[k] for k in ("fencer_id", "side", "athlete_id", "display_name")}
                                    | {"bout_id": bb["bout_id"]})
            for tt in bb["touches"]:
                if not store.db.one("SELECT 1 x FROM touches WHERE touch_id=?", (tt["touch_id"],)):
                    store.db.insert("touches", {**{k: tt[k] for k in ("touch_id", "seq", "light_frame", "light_time_s",
                                                                      "lamp_left", "lamp_right", "light_source",
                                                                      "start_frame", "end_frame", "window_clipped",
                                                                      "notes")},
                                                "bout_id": bb["bout_id"], "video_id": v["video_id"],
                                                "created_at": now_iso()})
                    stats["touches"] += 1
                touch = store.get_touch(tt["touch_id"])
                idx = store.frame_index(v["video_id"])
                for a in tt["annotations"]:
                    if store.db.one("SELECT 1 x FROM action_segments WHERE segment_id=?", (a["segment_id"],)):
                        stats["skipped"] += 1
                        continue
                    store.create_annotation(tt["touch_id"], a["side"], a["start_frame"], a["end_frame"], a["label"],
                                            a["annotator_id"], label_source=a["label_source"],
                                            ambiguity_flag=a["ambiguity_flag"], derived_from=a.get("derived_from_segment_id"),
                                            segment_id=a["segment_id"], label_version=a.get("label_version") or "v1",
                                            _op="import")
                    for k, fr in (("t_start_ms", a["start_frame"]), ("t_end_ms", a["end_frame"])):
                        if k in a and abs(_t_ms(store, touch, fr, idx) - a[k]) > 0.5:
                            stats["time_mismatches"].append({"segment_id": a["segment_id"], k: a[k]})
                    stats["annotations"] += 1
                for e in tt.get("expert_events", []):
                    if not store.db.one("SELECT 1 x FROM events WHERE event_id=?", (e["event_id"],)):
                        store.db.insert("events", e)
                        stats["events"] += 1
                for r in tt.get("reviews", []):
                    if store.db.one("SELECT 1 x FROM review_records WHERE review_id=?", (r["review_id"],)):
                        continue
                    if store.db.one("SELECT 1 x FROM action_segments WHERE segment_id=?", (r["prediction_segment_id"],)):
                        store.db.insert("review_records", r)
                        stats["reviews"] += 1
    return stats


def import_annotations_csv(store, text: str, annotator_override=None) -> dict:
    """Training-label CSV (export format) -> annotations. label_source is kept from the file;
    legacy rows without a source become 'unknown' (never 'blind')."""
    n, skipped, errors = 0, 0, []
    for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            sid = (r.get("segment_id") or "").strip() or None
            if sid and store.db.one("SELECT 1 x FROM action_segments WHERE segment_id=?", (sid,)):
                skipped += 1
                continue
            src = (r.get("label_source") or "").strip() or "unknown"
            if src not in tx.LABEL_SOURCES:
                raise IntervalError(f"bad label_source {src}")
            store.create_annotation(r["touch_id"], r["side"], int(r["start_frame"]), int(r["end_frame"]), r["label"],
                                    annotator_override or r.get("annotator_id") or "import", label_source=src,
                                    ambiguity_flag=(r.get("ambiguity_flag") or "0") in ("1", "True", "true"),
                                    segment_id=sid, label_version=r.get("label_version") or "v1", _op="import")
            n += 1
        except Exception as e:  # noqa: BLE001
            errors.append({"line": i, "error": f"{type(e).__name__}: {e}"})
    return {"imported": n, "skipped_duplicates": skipped, "errors": errors}


# --------------------------------------------------------------------------- shared labels (v0.4.0)
def _records_to_videos(rows: list[dict]) -> list[dict]:
    """Annotation records (the 'Training labels' CSV / JSON export) -> the bundle shape."""
    vids: dict = {}
    for r in rows:
        v = vids.setdefault(r.get("video_id") or "unknown", {
            "video_id": r.get("video_id"), "sha256": None, "n_frames": None, "fps_measured": _num(r.get("fps")),
            "title": None, "_touches": {}, "_fencers": {}})
        if r.get("athlete_id"):
            v["_fencers"][r.get("side")] = r.get("athlete_id")
        t = v["_touches"].setdefault(r.get("touch_id"), {
            "touch_id": r.get("touch_id"), "light_frame": int(float(r["light_frame"])), "lamp_left": None,
            "lamp_right": None, "light_source": "manual", "start_frame": None, "end_frame": None, "notes": None,
            "annotations": [], "expert_events": [], "reviews": []})
        t["annotations"].append({
            "segment_id": (r.get("segment_id") or "").strip() or None, "side": r["side"],
            "start_frame": int(float(r["start_frame"])), "end_frame": int(float(r["end_frame"])), "label": r["label"],
            "label_source": (r.get("label_source") or "").strip() or "unknown",
            "annotator_id": r.get("annotator_id") or "import",
            "ambiguity_flag": str(r.get("ambiguity_flag") or "0") in ("1", "True", "true"),
            "label_version": r.get("label_version") or "v1"})
    out = []
    for v in vids.values():
        fenc = [{"side": s, "athlete_id": a, "display_name": a} for s, a in v.pop("_fencers").items()]
        out.append({**v, "bouts": [{"name": None, "fencers": fenc, "touches": list(v.pop("_touches").values())}]})
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_labels_file(raw: bytes, filename: str = "") -> list[dict]:
    """Accepts the three files the app can export: the full bundle (.json), the training-labels
    JSON (a list of records) and the training-labels CSV. Returns videos in the bundle shape."""
    text = raw.decode("utf-8-sig")
    if filename.lower().endswith(".csv") or not text.lstrip().startswith(("{", "[")):
        rows = [r for r in csv.DictReader(io.StringIO(text)) if (r.get("label") or "").strip()]
        if not rows or "light_frame" not in rows[0]:
            raise ValueError("this CSV is not a training-labels export (it needs light_frame, start_frame, "
                             "end_frame, side and label columns)")
        return _records_to_videos(rows)
    data = json.loads(text)
    if isinstance(data, dict) and data.get("format") == "engarde-bundle/1":
        return data["videos"]
    if isinstance(data, list):
        rows = [r for r in data if r.get("label")]
        if not rows:
            raise ValueError("the file holds no labels")
        return _records_to_videos(rows)
    raise ValueError("not a labels file: use Export > Full bundle or Export > Training labels")


def _match_video(store, vv, target_video_id):
    if target_video_id:
        return store.get_video(target_video_id), "chosen by you"
    if vv.get("sha256"):
        v = store.db.one("SELECT video_id FROM videos WHERE sha256=?", (vv["sha256"],))
        if v:
            return store.get_video(v["video_id"]), "same video file (fingerprint match)"
    if vv.get("video_id"):
        v = store.db.one("SELECT video_id FROM videos WHERE video_id=?", (vv["video_id"],))
        if v:
            return store.get_video(v["video_id"]), "same video id (exported from this computer)"
    return None, None


def import_labels(store, videos: list[dict], target_video_id=None, actor="import") -> dict:
    """Bring a teammate's labels onto the same video on this computer.

    * the video is matched by file fingerprint (sha256), or chosen explicitly;
    * a touch in the file is matched to a local touch whose light frame is within 3 frames, so two
      people who both ran the automatic analysis do not end up with duplicate touches;
    * a label that would overlap a label already here is NOT imported (the local label wins) and is
      listed under 'conflicts' for a person to decide;
    * label names are translated to this data folder's label set; label_source (blind / ...) is kept.
    Running the same import twice changes nothing."""
    if target_video_id and len(videos) > 1:
        raise ValueError("the file holds labels for several videos; import it without choosing one video")
    rep = {"videos": [], "touches_created": 0, "touches_matched": 0, "labels_imported": 0, "already_here": 0,
           "conflicts": [], "errors": [], "unmatched_videos": [], "events": 0}
    for vv in videos:
        v, how = _match_video(store, vv, target_video_id)
        n_file = sum(len(t["annotations"]) for b in vv["bouts"] for t in b["touches"])
        if not v:
            rep["unmatched_videos"].append({"title": vv.get("title") or vv.get("video_id"), "labels": n_file,
                                            "why": "this video file is not on this computer yet (or it is a different "
                                                   "copy). Upload the same file first, or choose the video by hand."})
            continue
        if not v["indexed"]:
            rep["errors"].append({"video": v["title"], "error": "video is still being indexed; try again in a minute"})
            continue
        store.ensure_unlocked(v["video_id"])
        n = v["n_frames"]
        if vv.get("n_frames") and vv["n_frames"] != n:
            rep["errors"].append({"video": v["title"], "error": f"frame count differs (file {vv['n_frames']}, here {n}): "
                                  "not the same video file, labels were not imported"})
            continue
        if vv.get("fps_measured") and v["fps_measured"] and abs(vv["fps_measured"] - v["fps_measured"]) > 0.05:
            rep["errors"].append({"video": v["title"], "error": f"frame rate differs (file {vv['fps_measured']:.2f}, here "
                                  f"{v['fps_measured']:.2f}): not the same video file, labels were not imported"})
            continue
        mx = max([a["end_frame"] for b in vv["bouts"] for t in b["touches"] for a in t["annotations"]] or [0])
        if mx > n:
            rep["errors"].append({"video": v["title"], "error": f"labels reach frame {mx} but this video has {n} frames"})
            continue
        local_bouts = store.db.q("SELECT bout_id FROM bouts WHERE video_id=? ORDER BY created_at, rowid", (v["video_id"],))
        vrep = {"video_id": v["video_id"], "title": v["title"], "matched_by": how, "labels_in_file": n_file, "imported": 0}
        for bb in vv["bouts"]:
            if local_bouts:
                bout_id = local_bouts[0]["bout_id"]
            else:
                f = {x["side"]: x for x in bb.get("fencers", [])}
                bout_id = store.add_bout(v["video_id"], bb.get("name") or v["title"],
                                         left={k: f.get("left", {}).get(k) for k in ("athlete_id", "display_name")},
                                         right={k: f.get("right", {}).get(k) for k in ("athlete_id", "display_name")})["bout_id"]
                local_bouts = [{"bout_id": bout_id}]
            for tt in sorted(bb["touches"], key=lambda t: t["light_frame"]):
                local = store.db.one("SELECT touch_id FROM touches WHERE touch_id=? AND video_id=?",
                                     (tt.get("touch_id"), v["video_id"]))
                if not local:
                    local = store.db.one("SELECT touch_id FROM touches WHERE video_id=? AND ABS(light_frame-?)<=3 "
                                         "ORDER BY ABS(light_frame-?) LIMIT 1", (v["video_id"], tt["light_frame"], tt["light_frame"]))
                if local:
                    touch = store.get_touch(local["touch_id"])
                    rep["touches_matched"] += 1
                else:
                    try:
                        touch = store.add_touch(bout_id, int(tt["light_frame"]), tt.get("lamp_left"), tt.get("lamp_right"),
                                                tt.get("notes") or f"imported from {actor}'s labels",
                                                light_source=tt.get("light_source") or "manual",
                                                start_frame=tt.get("start_frame"), end_frame=tt.get("end_frame"))
                    except Exception as e:  # noqa: BLE001
                        rep["errors"].append({"video": v["title"], "light_frame": tt["light_frame"], "error": str(e)})
                        continue
                    rep["touches_created"] += 1
                for a in tt["annotations"]:
                    sid = a.get("segment_id")
                    if sid and store.db.one("SELECT 1 x FROM action_segments WHERE segment_id=?", (sid,)):
                        rep["already_here"] += 1
                        continue
                    s0, e0 = max(a["start_frame"], touch["start_frame"]), min(a["end_frame"], touch["end_frame"])
                    src = a.get("label_source") if a.get("label_source") in tx.LABEL_SOURCES else "unknown"
                    who = {"touch": touch["seq"], "side": a["side"], "label": store.tax.canon(a["label"]),
                           "frames": [a["start_frame"], a["end_frame"]], "by": a.get("annotator_id")}
                    if e0 <= s0:
                        rep["errors"].append({**who, "error": "outside this touch's window"})
                        continue
                    same = store.db.one("SELECT 1 x FROM action_segments WHERE kind='annotation' AND deleted=0 AND touch_id=? "
                                        "AND side=? AND start_frame=? AND end_frame=? AND label=?",
                                        (touch["touch_id"], a["side"], s0, e0, who["label"]))
                    if same:
                        rep["already_here"] += 1
                        continue
                    try:
                        store.create_annotation(touch["touch_id"], a["side"], s0, e0, a["label"],
                                                a.get("annotator_id") or actor, label_source=src,
                                                ambiguity_flag=a.get("ambiguity_flag", False), segment_id=sid,
                                                label_version=a.get("label_version") or "v1", _op="import")
                        rep["labels_imported"] += 1
                        vrep["imported"] += 1
                    except Exception as e:  # noqa: BLE001 - overlap with a local label, unknown label name, ...
                        (rep["conflicts"] if type(e).__name__ == "Conflict" else rep["errors"]).append({**who, "error": str(e)})
                for e in tt.get("expert_events", []):
                    if store.db.one("SELECT 1 x FROM events WHERE event_id=?", (e["event_id"],)):
                        continue
                    if e.get("event_frame") is not None and not touch["start_frame"] <= e["event_frame"] < touch["end_frame"]:
                        continue
                    store.db.insert("events", {**e, "touch_id": touch["touch_id"], "run_id": None})
                    rep["events"] += 1
        rep["videos"].append(vrep)
    store.audit(actor, "import_labels", target_video_id or "auto",
                {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()})
    return rep
