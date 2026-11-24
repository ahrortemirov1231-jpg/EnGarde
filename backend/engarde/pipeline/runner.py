"""Pipeline orchestration. Every stage writes its output to the touch's stages/ dir and downstream stages
read only those files (PRD §4). A stage is skipped (status 'cached') when its config hash and inputs
are unchanged."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys

import numpy as np
import pandas as pd

from .. import video as vid
from ..db import new_id, now_iso
from . import features as feat
from . import perception as per
from . import recognition as rec

STAGES = ["s1_window", "s2_tracking", "s3_pose", "s4_features", "s5_recognition", "s6_quality"]


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def versions() -> dict:
    out = {"python": sys.version.split()[0], "platform": platform.platform(), "numpy": np.__version__,
           "pandas": pd.__version__}
    for mod in ("cv2", "ultralytics", "torch", "xgboost", "sklearn"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except Exception:  # noqa: BLE001
            out[mod] = "not installed"
    try:
        out["git_commit"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                           cwd=os.path.dirname(__file__)).stdout.strip() or "uncommitted"
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                               cwd=os.path.dirname(__file__)).stdout.strip()
        out["git_dirty"] = bool(dirty)
    except Exception:  # noqa: BLE001
        out["git_commit"] = "unknown"
    return out


class Manifest:
    def __init__(self, d):
        self.path = os.path.join(d, "manifest.json")
        self.data = json.load(open(self.path)) if os.path.exists(self.path) else {}

    def fresh(self, stage, h, outputs):
        e = self.data.get(stage)
        return bool(e and e.get("hash") == h and e.get("status") in ("completed", "cached")
                    and all(os.path.exists(o) for o in outputs))

    def set(self, stage, **kw):
        self.data[stage] = {**self.data.get(stage, {}), **kw, "at": now_iso()}
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, default=str)


def prepare_touch(store, touch_id, report=None):
    """S1: window + exact frame extraction."""
    t = store.get_touch(touch_id)
    v = store.get_video(t["video_id"])
    sd = store.stage_dir(touch_id)
    os.makedirs(sd, exist_ok=True)
    times = store.touch_times(touch_id)
    out = vid.extract_frames_fast(v["path"], store.frame_index(v["video_id"]), times["frames"], store.frames_dir(touch_id))
    s1 = {"touch_id": touch_id, "video_id": v["video_id"], "light_frame": t["light_frame"],
          "light_time_s": t["light_time_s"], "lamp_sides": {"left": t["lamp_left"], "right": t["lamp_right"]},
          "start_frame": t["start_frame"], "end_frame": t["end_frame"], "window_clipped": bool(t["window_clipped"]),
          "interval_convention": "[start_frame, end_frame)", "frames": times["frames"], "t_s": times["t_s"],
          "rel_ms": times["rel_ms"]}
    with open(os.path.join(sd, "s1_window.json"), "w") as f:
        json.dump(s1, f)
    man = Manifest(sd)
    prev = man.data.get("s1_window", {})
    man.set("s1_window", status="completed", hash=_hash([t["start_frame"], t["end_frame"], t["light_frame"]]),
            info={**prev.get("info", {}), "frames_extraction": out} if prev else out)
    return out


def read_identity(store, touch_id):
    p = os.path.join(store.touch_dir(touch_id), "identity.json")
    return json.load(open(p)) if os.path.exists(p) else None


def write_identity(store, touch_id, left: int, right: int, reviewer: str):
    """Reviewer-confirmed fencer tracks for a touch. Kept outside stages/ so re-running never loses it."""
    if not reviewer:
        raise ValueError("reviewer is required")
    if int(left) == int(right):
        raise ValueError("left and right must be different tracks")
    os.makedirs(store.touch_dir(touch_id), exist_ok=True)
    d = {"left": int(left), "right": int(right), "confirmed_by": reviewer, "confirmed_at": now_iso()}
    with open(os.path.join(store.touch_dir(touch_id), "identity.json"), "w") as f:
        json.dump(d, f, indent=2)
    with open(os.path.join(store.touch_dir(touch_id), "identity_log.jsonl"), "a") as f:   # append-only history
        f.write(json.dumps({**d, "op": "set"}) + "\n")
    return d


def clear_identity(store, touch_id, reviewer: str):
    p = os.path.join(store.touch_dir(touch_id), "identity.json")
    if os.path.exists(p):
        os.remove(p)
        with open(os.path.join(store.touch_dir(touch_id), "identity_log.jsonl"), "a") as f:
            f.write(json.dumps({"op": "clear", "by": reviewer, "at": now_iso()}) + "\n")


def _skeleton_for(store, video_id):
    p = os.path.join(store.video_dir(video_id), "imported_skeleton.json")
    return p if os.path.exists(p) else None


def analyze_touch(store, touch_id, report=None, pose_source="auto", method="rule_baseline", model_id=None,
                  rule_cfg=None, force=False):
    t = store.get_touch(touch_id)
    v = store.get_video(t["video_id"])
    sd = store.stage_dir(touch_id)
    os.makedirs(sd, exist_ok=True)
    man = Manifest(sd)
    status = {}
    rep = report or (lambda *a, **k: None)
    times = store.touch_times(touch_id)
    frames, t_s = times["frames"], times["t_s"]
    T = len(frames)

    # ---- S1
    rep(0.02, "s1_window")
    if force or not os.path.exists(os.path.join(sd, "s1_window.json")) or \
            len(os.listdir(store.frames_dir(touch_id)) if os.path.isdir(store.frames_dir(touch_id)) else []) < T:
        prepare_touch(store, touch_id)
        status["s1_window"] = "completed"
    else:
        status["s1_window"] = "cached"

    def frame_iter():
        return vid.read_frames(store.frames_dir(touch_id), frames)

    # ---- S2 detect (YOLO + ByteTrack, cached) -> S2 select (rules / reviewer) -> S3 pose
    skel = _skeleton_for(store, v["video_id"]) if pose_source == "auto" else (
        pose_source.split(":", 1)[1] if pose_source.startswith("import:") else None)
    source_kind = "import" if skel else "yolo"
    calib = json.loads(v["calibration_json"]) if v["calibration_json"] else {}
    piste = calib.get("piste_polygon")
    tracks_p, det_p, pose_p = (os.path.join(sd, n) for n in ("tracks.parquet", "detections.parquet", "pose.npz"))
    kp_p = os.path.join(sd, "detections_kp.npy")
    identity = read_identity(store, touch_id)
    forced = {"left": identity["left"], "right": identity["right"]} if identity else None
    hd = _hash({"source": source_kind, "skeleton": skel, "weights": per.DEFAULT_WEIGHTS,
                "frames": [frames[0], frames[-1]], "v": 3})
    det_files = [det_p] + ([kp_p] if source_kind == "yolo" else [])
    legacy_ok = "s2_detect" not in man.data and man.data.get("s2_tracking", {}).get("status") == "completed" \
        and man.data.get("s2_tracking", {}).get("info", {}).get("backend") == ("imported_skeleton" if source_kind == "import"
                                                                               else "ultralytics-yolo-pose+bytetrack")
    if not force and all(os.path.exists(x) for x in det_files) and (man.fresh("s2_detect", hd, det_files) or legacy_ok):
        status["s2_detect"] = "cached"
        if legacy_ok:
            old = man.data["s2_tracking"]["info"]
            man.set("s2_detect", status="completed", hash=hd,
                    info={k: old[k] for k in old if k not in ("selection", "quality")})
    elif source_kind == "yolo":
        rep(0.05, "s2_detect")
        try:
            det, kps, src = per.run_yolo_track(frame_iter(), T, v["fps_measured"] or v["fps_nominal"] or 30, sd,
                                               report=lambda p: rep(0.05 + 0.55 * p, "s2_detect"))
        except per.PoseBackendUnavailable as e:
            man.set("s2_tracking", status="failed", hash=None, error=str(e))
            raise
        det.to_parquet(det_p)    # every tracked person is kept on disk, not only the two fencers
        np.save(kp_p, kps)
        man.set("s2_detect", status="completed", hash=hd, info=src)
        status["s2_detect"] = "completed"
    else:
        pd.DataFrame(columns=["frame", "track_id", "x1", "y1", "x2", "y2", "score"]).to_parquet(det_p)
        man.set("s2_detect", status="completed", hash=hd, info={"backend": "imported_skeleton"})
        status["s2_detect"] = "completed"
    h2 = _hash({"detect": hd, "select": per.SELECT_VERSION, "forced": forced, "piste": piste})
    if not force and status.get("s2_detect") == "cached" and man.fresh("s2_tracking", h2, [tracks_p, pose_p]):
        status["s2_tracking"] = status["s3_pose"] = "cached"
    else:
        rep(0.6, "s2_select")
        if source_kind == "import":
            tracks, pose, src = per.load_imported_skeleton(skel, frames)
            sel_info = {"status": "imported", "piste_filter": False}
        else:
            src = man.data["s2_detect"]["info"]
            det = pd.read_parquet(det_p)
            kps = np.load(kp_p)
            appearance = per.track_appearance(det, store.frames_dir(touch_id))
            tracks, sel_info = per.select_fencers(det, frames, piste, appearance=appearance, forced=forced)
            if identity:
                sel_info["identity_confirmed"] = identity
            if tracks.empty:
                tracks = per.add_track_flags(pd.DataFrame([{"frame": f, "side": s_, "present": False, "track_id": -1,
                                                            "x1": np.nan, "y1": np.nan, "x2": np.nan, "y2": np.nan,
                                                            "score": np.nan, "det_row": -1}
                                                           for s_ in ("left", "right") for f in frames]))
            pose = per.pose_arrays(tracks, kps, frames)
        tracks.to_parquet(tracks_p)
        per.save_pose_npz(pose_p, pose, frames, src)
        tq = {s_: {"present_frac": float(tracks[tracks.side == s_].present.mean()),
                   "track_breaks": int(tracks[tracks.side == s_].track_break.sum()),
                   "id_switch_suspect": int(tracks[tracks.side == s_].id_switch_suspect.sum()),
                   "identity_uncertain_frames": int(tracks[tracks.side == s_].identity_uncertain.sum()),
                   "kp_visible_frac": float(pose[s_]["mask"][:, per.CORE9].mean()),
                   "kp_interpolated": int(pose[s_]["interp"].sum())} for s_ in ("left", "right")}
        man.set("s2_tracking", status="completed", hash=h2, info={**src, "selection": sel_info, "quality": tq})
        man.set("s3_pose", status="completed", hash=h2, info={"keypoint_conf_min": per.KP_CONF_MIN,
                                                               "max_interp_gap": per.MAX_INTERP_GAP,
                                                               "smoothing": "SG(5) for features only; raw kept"})
        status["s2_tracking"] = status["s3_pose"] = "completed"
    tracks = per.add_track_flags(pd.read_parquet(tracks_p))   # flags always from the current rules
    pose, _, pose_src = per.load_pose_npz(pose_p)

    # ---- S4
    feats_p, cam_p = os.path.join(sd, "features.parquet"), os.path.join(sd, "camera.parquet")
    h4 = _hash([h2, "s4", 10])
    if not force and man.fresh("s4_features", h4, [feats_p, cam_p]):
        status["s4_features"] = "cached"
    else:
        rep(0.65, "s4_features")
        boxes = {}
        for r in tracks.itertuples():
            if r.present:
                boxes.setdefault(r.frame, []).append((r.x1, r.y1, r.x2, r.y2))
        cam = feat.camera_motion(frame_iter(), boxes, report=lambda p: rep(0.65 + 0.15 * p, "s4_features"), n=T)
        cam.to_parquet(cam_p)
        F, fmeta = feat.compute_features(pose, tracks, frames, t_s, v["width"], cam)
        F.to_parquet(feats_p)
        man.set("s4_features", status="completed", hash=h4, info=fmeta)
        status["s4_features"] = "completed"
    F = pd.read_parquet(feats_p)

    # ---- S5 + S6
    rep(0.85, "s5_recognition")
    run_id = new_id("run")
    cfg = {**rec.DEFAULT_RULES, **(rule_cfg or {})}
    model_meta, calibrated = None, False
    per_side_labels, per_side_probs = {}, {}
    if method == "rule_baseline":
        for side in ("left", "right"):
            f = F[F.side == side].sort_values("frame").reset_index(drop=True)
            labs, _ = rec.rule_labels(f, cfg)
            per_side_labels[side] = labs
            per_side_probs[side] = None
        status_label = "Rule baseline / unvalidated"
        model_version = "rule_baseline@" + _hash(cfg)
    elif method in ("xgboost", "tcn"):
        from .. import ml
        model_meta = ml.load_model_meta(store, model_id)
        if model_meta["method"] != method:
            raise ValueError(f"model {model_id} was trained as {model_meta['method']}, not {method}")
        for side in ("left", "right"):
            f = F[F.side == side].sort_values("frame").reset_index(drop=True)
            labs, probs = ml.predict_frames(store, model_id, f)
            per_side_labels[side], per_side_probs[side] = labs, probs
        calibrated = bool(model_meta.get("temperature"))
        status_label = {"xgboost": "XGBoost", "tcn": "TCN"}[method] + " / " + \
            ("temperature-calibrated" if calibrated else "uncalibrated scores")
        model_version = f"{method}@{model_id}"
    else:
        raise ValueError(f"unknown method {method}")

    seg_rows = []
    frame_rows = []
    for side in ("left", "right"):
        f = F[F.side == side].sort_values("frame").reset_index(drop=True)
        labs = per_side_labels[side]
        probs = per_side_probs[side]
        segs = rec.postprocess(labs, cfg)
        for s in segs:
            s.start += t["start_frame"]
            s.end += t["start_frame"]
            mean_prob, class_probs, top2 = None, None, None
            if probs is not None:
                P = probs[s.start - t["start_frame"]: s.end - t["start_frame"]]
                classes = model_meta["classes"]
                mp_ = P.mean(axis=0)
                class_probs = {c: round(float(x), 4) for c, x in zip(classes, mp_)}
                order = np.argsort(-mp_)
                if s.label in classes:
                    mean_prob = float(mp_[classes.index(s.label)])
                top2 = classes[order[1]] if len(order) > 1 and classes[order[0]] == s.label else classes[order[0]]
            q = rec.quality_gate(s, f, t["start_frame"], mean_prob, calibrated)
            # spans without evidence carry NO label at all (never silently Other/Still)
            # the rule baseline thinks in four internal classes; they are written under this data
            # folder's label names (e.g. Advance -> "Step forward" with Tim's labels)
            label = store.tax.canon(s.label) if s.label != rec.NO_EVIDENCE else "none"
            seg_rows.append({"segment_id": new_id("prd"), "kind": "prediction", "touch_id": touch_id, "side": side,
                             "start_frame": s.start, "end_frame": s.end, "label": label, "run_id": run_id,
                             "class_probs_json": json.dumps(class_probs) if class_probs else None,
                             "calibrated_prob": mean_prob if calibrated else None, "top2_label": top2,
                             "created_at": now_iso(), **q,
                             "no_call_reason": q["no_call_reason"] if s.label != rec.NO_EVIDENCE
                             else ",".join(sorted(set(["pose_missing"] + (q["no_call_reason"] or "").split(",")) - {""}))})
        for i, fr in enumerate(f.frame):
            frame_rows.append({"frame": int(fr), "side": side, "raw_label": labs[i],
                               **({f"p_{c}": float(probs[i][k]) for k, c in enumerate(model_meta["classes"])}
                                  if probs is not None else {})})
    pd.DataFrame(frame_rows).to_parquet(os.path.join(sd, f"pred_{run_id}.parquet"))

    stage_status = {**status, "s5_recognition": "completed", "s6_quality": "completed"}
    run = {"run_id": run_id, "touch_id": touch_id, "method": method, "status_label": status_label,
           "model_version": model_version, "dataset_version": (model_meta or {}).get("dataset_version"),
           "config_json": json.dumps({"rules": cfg if method == "rule_baseline" else None, "pose_source": pose_src,
                                      "quality_thresholds": rec.QUALITY_THRESHOLDS, "model": model_meta}),
           "versions_json": json.dumps(versions()), "stage_status_json": json.dumps(stage_status),
           "calibrated": int(calibrated), "created_at": now_iso()}
    with store.db.transaction() as db:
        db.insert("prediction_runs", run)
        for r in seg_rows:
            db.insert("action_segments", r)
    # rule events (source = rule) from gated intervals
    for side in ("left", "right"):
        f = F[F.side == side].sort_values("frame").reset_index(drop=True)
        rows = [r for r in seg_rows if r["side"] == side]
        for e in rec.rule_events(rows, f, t["start_frame"], cfg):
            store.add_event(touch_id, side, e["event_type"], e["frame"], "rule", run_id=run_id, note=e["note"])
    man.set("s5_recognition", status="completed", run_id=run_id, method=method)
    man.set("s6_quality", status="completed", run_id=run_id)
    from .. import exports
    exports.write_stage_outputs(store, touch_id, run_id)
    rep(1.0, "done")
    return {"run_id": run_id, "stage_status": stage_status, "n_segments": len(seg_rows),
            "no_call": sum(r["no_call"] for r in seg_rows), "pose_source": pose_src}
