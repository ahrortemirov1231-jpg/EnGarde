"""FastAPI app. Run:  ENGARDE_DATA=./data uvicorn engarde.api:app --app-dir backend --port 8000"""
from __future__ import annotations

import contextvars
import json
import os
import shutil
import tempfile
import time

import numpy as np
import pandas as pd
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import auth, backup, exports, ml, stats, tcn
from . import taxonomy as tx
from .intervals import IntervalError, coverage_check
from .jobs import JobRunner
from .pipeline import perception as per
from .pipeline import runner
from .store import Conflict, Locked, NotFound, Store

DATA = os.environ.get("ENGARDE_DATA", os.path.join(os.path.dirname(__file__), "..", "..", "data"))
store = Store(DATA)
jobs = JobRunner(store)
jobs.register("index_video", lambda vid, report, **kw: store.index_video(vid) and {"indexed": vid})
jobs.register("prepare_touch", lambda tid, report, **kw: runner.prepare_touch(store, tid, report))
jobs.register("analyze_touch", lambda tid, report, **kw: runner.analyze_touch(store, tid, report, **kw))
jobs.register("train_xgboost", lambda _t, report, **kw: ml.train(store, report=report, **kw))
jobs.register("train_tcn", lambda _t, report, **kw: tcn.train(store, report=report, **kw))
from . import auto  # noqa: E402
jobs.register("auto_analyze", lambda vid_, report, **kw: auto.auto_analyze(store, vid_, report, **kw))
from . import bulk  # noqa: E402
jobs.register("bulk_import_folder", lambda _t, report, **kw: bulk.import_folder(store, report=report, jobs=jobs, **kw))
jobs.register("bulk_import_links", lambda _t, report, **kw: bulk.import_links(store, report=report, jobs=jobs, **kw))

VERSION = "0.5.6"
STARTED_AT = time.strftime("%Y-%m-%d %H:%M")

# ------------------------------------------------------------------ team mode (sign-in)
# ENGARDE_TEAM=1 (serve_team.bat): every /api call needs a signed-in person, and that person's name is
# used for every label, review, lock and delete, whatever the page sends. Without it: no sign-in.
TEAM = auth.team_mode()
_current: contextvars.ContextVar = contextvars.ContextVar("engarde_user", default=None)
_throttle = auth.Throttle()
_OPEN = {"/api/auth/me", "/api/auth/login", "/api/auth/logout"}


async def _gate(request: Request):
    """Runs before every route. async on purpose: the signed-in user is put in a context variable
    here, and FastAPI copies the context into the worker thread that runs the route."""
    if not TEAM:
        return
    path = request.url.path
    if not path.startswith("/api/") or path in _OPEN:
        return
    # v0.5.6: the lookup runs in a worker thread. Run directly here it waited for the database lock on
    # the server's main loop, so one slow request elsewhere froze every request (the Nov 23 outage).
    user = await run_in_threadpool(auth.user_for_token, store.db, request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(401, "Please sign in.")
    _current.set(user)
    if request.method not in ("GET", "HEAD", "OPTIONS") and user["role"] == "viewer" \
            and path not in ("/api/auth/password",):
        raise HTTPException(403, "Your account can look but not change anything. Ask an admin for member access.")


def who(given=None):
    """The name to record for an action: the signed-in person in team mode, else what the page sent."""
    u = _current.get()
    return u["name"] if (TEAM and u) else given


def _is_admin():
    u = _current.get()
    return None if not TEAM else bool(u and u["role"] == "admin")


def _need_admin(what="do this"):
    if TEAM and not _is_admin():
        raise HTTPException(403, f"Only an admin can {what}.")


def _need_team():
    if not TEAM:
        raise HTTPException(404, "team mode is off on this computer (start the app with serve_team.bat)")


app = FastAPI(title="Engarde Analytics MVP", version=VERSION, dependencies=[Depends(_gate)],
              docs_url=None if TEAM else "/docs", redoc_url=None, openapi_url=None if TEAM else "/openapi.json")
if TEAM:
    backup.start_schedule(store)
    backup.keep_awake()


@app.exception_handler(Locked)
async def _le(_r, e):
    return JSONResponse({"error": str(e), "kind": "locked"}, status_code=423)


@app.exception_handler(IntervalError)
async def _ie(_r, e):
    return JSONResponse({"error": str(e), "kind": "validation"}, status_code=422)


@app.exception_handler(Conflict)
async def _ce(_r, e):
    return JSONResponse({"error": str(e), "kind": "conflict", "detail": e.detail}, status_code=409)


@app.exception_handler(NotFound)
async def _nf(_r, e):
    return JSONResponse({"error": f"not found: {e}", "kind": "not_found"}, status_code=404)


def _clean(o):
    """NaN-safe JSON."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
        return None
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# ------------------------------------------------------------------ config / status
@app.get("/api/config")
def config():
    try:
        import ultralytics  # noqa: F401
        ul = True
    except ImportError:
        ul = False
    w = per.weights_path()
    try:
        import xgboost  # noqa: F401
        xg = True
    except ImportError:
        xg = False
    return {**store.tax.to_dict(), "version": VERSION, "label_migration": store.label_migration,
            "admins": store.admins(), "team_mode": TEAM, "pose_backend": {
        "name": "Ultralytics YOLO11n-pose + ByteTrack", "installed": ul, "weights_present": os.path.exists(w),
        "weights_path": w, "keypoints": "COCO-17 (no toes/heels)"},
        "xgboost_installed": xg, "data_dir": store.data_dir}


# ------------------------------------------------------------------ videos
@app.get("/api/videos")
def list_videos():
    return _clean(store.list_videos())


@app.post("/api/videos")
async def upload_video(file: UploadFile = File(...), title: str = Form(None), capture_type: str = Form("unknown"),
                       weapon_type: str = Form("unknown"), left_name: str = Form(None), right_name: str = Form(None),
                       auto_analyze: bool = Form(True)):
    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=store.data_dir) as tmp:
        shutil.copyfileobj(file.file, tmp)
    try:
        v = store.add_video(tmp.name, title or file.filename or "video", move=True, capture_type=capture_type,
                            weapon_type=weapon_type)
    except Exception as e:  # noqa: BLE001
        if os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise HTTPException(422, f"could not read video: {e}") from e
    if auto_analyze:
        job = jobs.submit("auto_analyze", v["video_id"], left_name=left_name or None, right_name=right_name or None)
    else:
        job = jobs.submit("index_video", v["video_id"])
    return {"video": v, "job": job}


@app.post("/api/videos/{video_id}/analyze")
def analyze_video(video_id: str):
    """(Re)run the automatic analysis for a whole video: new light candidates + any touch without a run."""
    store.get_video(video_id)
    return jobs.submit("auto_analyze", video_id)


@app.post("/api/videos/from_path")
def add_local_video(body: dict = Body(...)):
    _need_admin("add a video from a path on the server")
    v = store.add_video(body["path"], body.get("title") or os.path.basename(body["path"]),
                        capture_type=body.get("capture_type", "unknown"), weapon_type=body.get("weapon_type", "unknown"))
    return {"video": v, "job": jobs.submit("index_video", v["video_id"])}


@app.post("/api/import/videos/folder")
def bulk_import_folder(body: dict = Body(...)):
    """v0.5.5: add every video file in a folder on the host PC (sub-folders too). Admins only."""
    _need_admin("bulk-import videos")
    folder = (body.get("folder") or "").strip().strip('"')
    if not folder or not os.path.isdir(os.path.expanduser(folder)):
        raise HTTPException(422, f"folder not found on the host PC: {folder or '(empty)'}")
    n = len(bulk.find_videos(os.path.expanduser(folder)))
    if n == 0:
        raise HTTPException(422, "no video files (.mp4, .mov, .m4v, .mkv, .webm, .avi) in that folder")
    job = jobs.submit("bulk_import_folder", None, folder=folder, analyze=bool(body.get("analyze", False)),
                      actor=who(body.get("actor")) or "unknown")
    return {"found": n, "job": job}


@app.post("/api/import/videos/links")
def bulk_import_links(body: dict = Body(...)):
    """v0.5.5: download a list of video links on the host PC and add them. Admins only.
    body.text: one link per line, "title, link" lines, or a CSV with a video_url / url / link column."""
    _need_admin("bulk-import videos")
    items = bulk.parse_links(body.get("text", ""))
    if not items:
        raise HTTPException(422, "no http(s) links found. Paste one link per line, or a CSV with a video_url column")
    job = jobs.submit("bulk_import_links", None, items=items, analyze=bool(body.get("analyze", False)),
                      actor=who(body.get("actor")) or "unknown")
    return {"found": len(items), "job": job}


@app.get("/api/import/videos/status")
def bulk_import_status():
    """The most recent bulk import jobs, newest first."""
    rows = store.db.q("SELECT * FROM jobs WHERE kind IN ('bulk_import_folder','bulk_import_links') "
                      "ORDER BY created_at DESC LIMIT 5")
    for r in rows:
        r["result"] = json.loads(r["result_json"]) if r.get("result_json") else None
        r.pop("params_json", None)
    return rows


@app.get("/api/videos/{video_id}")
def get_video(video_id: str):
    v = store.get_video(video_id)
    meta_p = os.path.join(store.video_dir(video_id), "video_meta.json")
    v["meta"] = json.load(open(meta_p)) if os.path.exists(meta_p) else None
    v["bouts"] = [store.get_bout(b["bout_id"]) for b in store.db.q("SELECT bout_id FROM bouts WHERE video_id=?", (video_id,))]
    v["jobs"] = jobs.list(video_id, 5)
    return _clean(v)


@app.patch("/api/videos/{video_id}")
def patch_video(video_id: str, body: dict = Body(...)):
    """title / capture_type / weapon_type, plus (v0.4.0) split: 'train' | 'test' | null and
    locked: true | false (both need 'actor', the name in the Annotator ID box)."""
    store.get_video(video_id)
    allowed = {k: body[k] for k in ("title", "capture_type", "weapon_type") if k in body}
    store.db.update("videos", "video_id", video_id, allowed)
    actor = (who(body.get("actor")) or "").strip()
    if "split" in body:
        store.set_split(video_id, body["split"], actor)
    if "locked" in body:
        store.set_lock(video_id, bool(body["locked"]), actor, admin=_is_admin())
    return _clean(store.get_video(video_id))


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str, actor: str = Query(...)):
    """Delete a wrong or duplicate video with its touches, labels and analysis files. The human
    labels are saved first as a re-importable file in data/trash/."""
    _need_admin("delete a video")
    return _clean(store.delete_video(video_id, (who(actor) or "").strip()))


@app.get("/api/dashboard")
def dashboard():
    """Numbers for a dashboard (this app's Videos page, or the Platform team's own front end)."""
    vids = store.list_videos()
    st = [v["review_status"] for v in vids]
    by_label = {}
    for r in store.db.q("SELECT label, COUNT(*) n FROM action_segments WHERE kind='annotation' AND deleted=0 GROUP BY label"):
        by_label[r["label"]] = r["n"]
    athletes = {}
    for r in store.db.q("SELECT f.athlete_id a, f.display_name d, COUNT(DISTINCT f.bout_id) n FROM fencers f "
                        "WHERE COALESCE(f.athlete_id, f.display_name) IS NOT NULL GROUP BY COALESCE(f.athlete_id, f.display_name)"):
        athletes[r["a"] or r["d"]] = r["n"]
    return _clean({
        "videos_total": len(vids), "videos_reviewed": st.count("reviewed"), "videos_in_progress": st.count("in_progress"),
        "videos_pending": st.count("pending"), "videos_without_touches": st.count("no_touches"),
        "videos_locked": sum(1 for v in vids if v.get("locked")),
        "videos_train": sum(1 for v in vids if v.get("split") == "train"),
        "videos_test": sum(1 for v in vids if v.get("split") == "test"),
        "touches_total": sum(v["n_touches"] for v in vids),
        "touches_labelled": sum(v["n_labelled_touches"] for v in vids),
        "labels_total": sum(v["n_labels"] for v in vids),
        "predictions_to_review": sum(v["pending_review"] for v in vids),
        "labels_by_name": by_label, "bouts_per_athlete": athletes,
        "weapons": sorted({v["weapon_type"] for v in vids if v.get("weapon_type")}),
        "label_set": store.tax.preset, "version": VERSION,
        "definitions": {"reviewed": "every touch in the video has at least one human label",
                        "in_progress": "some touches have human labels", "pending": "touches found, none labelled yet",
                        "predictions_to_review": "machine-made segments no person has confirmed or corrected yet"}})


@app.get("/api/videos/{video_id}/stream")
def stream(video_id: str):
    if not store.get_video(video_id)["linked"] and not os.path.exists(os.path.join(store.video_dir(video_id), "proxy.mp4")):
        raise HTTPException(404, "video file not linked on this computer (run link_videos.command)")
    p = store.playback_path(video_id)
    return FileResponse(p, media_type="video/mp4")


@app.get("/api/videos/{video_id}/frame_times")
def frame_times(video_id: str):
    idx = store.frame_index(video_id)
    t = idx.get("t_play", idx["t"])   # playback file timeline (differs only for reassembled sources)
    return {"t0": float(t[0]), "t": [round(float(x - t[0]), 6) for x in t],
            "note": "t[i] = presentation time of decoded frame i in the PLAYBACK file (matches <video>.currentTime); "
                    "analysis times use the original video's timestamps"}


@app.get("/api/videos/{video_id}/poster.jpg")
def poster(video_id: str):
    if not store.get_video(video_id)["linked"]:
        raise HTTPException(404, "video file not linked on this computer")
    tch = store.list_touches(video_id=video_id)
    for t in tch:
        p = os.path.join(store.frames_dir(t["touch_id"]), f"{t['light_frame']:06d}.jpg")
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg")
    pth = os.path.join(store.video_dir(video_id), "poster.jpg")
    if not os.path.exists(pth):
        import cv2
        from . import video as vid
        idx = store.frame_index(video_id)
        f = len(idx["t"]) // 3
        for _, img in vid.decode_range(store.get_video(video_id)["path"], idx, f, f + 1):
            cv2.imwrite(pth, img)
    return FileResponse(pth, media_type="image/jpeg")


@app.post("/api/videos/{video_id}/bouts")
def add_bout(video_id: str, body: dict = Body(...)):
    return store.add_bout(video_id, body.get("name") or "Bout", body.get("left"), body.get("right"))


@app.patch("/api/fencers/{fencer_id}")
def patch_fencer(fencer_id: str, body: dict = Body(...)):
    store.update_fencer(fencer_id, body)
    return store.db.one("SELECT * FROM fencers WHERE fencer_id=?", (fencer_id,))


# ------------------------------------------------------------------ touches
@app.post("/api/bouts/{bout_id}/touches")
def add_touch(bout_id: str, body: dict = Body(...)):
    t = store.add_touch(bout_id, body.get("light_frame"), body.get("lamp_left"), body.get("lamp_right"),
                        body.get("notes"), start_frame=body.get("start_frame"), end_frame=body.get("end_frame"))
    return {"touch": t, "job": jobs.submit("prepare_touch", t["touch_id"])}


@app.get("/api/touches")
def list_touches(video_id: str = None, bout_id: str = None):
    return _clean(store.list_touches(bout_id=bout_id, video_id=video_id))


def _manifest(tid):
    p = os.path.join(store.stage_dir(tid), "manifest.json")
    return json.load(open(p)) if os.path.exists(p) else {}


@app.get("/api/touches/{touch_id}")
def get_touch(touch_id: str):
    t = store.get_touch(touch_id)
    v = store.get_video(t["video_id"])
    b = store.get_bout(t["bout_id"])
    anns = store.annotations(touch_id)
    cov = {s: coverage_check([a for a in anns if a["side"] == s], t["start_frame"], t["end_frame"]) for s in tx.SIDES}
    run = store.latest_run(touch_id)
    return _clean({**t, "video": v, "bout": b, "times": store.touch_times(touch_id), "annotations": anns,
                   "coverage": cov, "expert_events": store.events(touch_id, sources=["expert"]),
                   "latest_run": ({**{k: run[k] for k in ("run_id", "method", "status_label", "model_version",
                                                          "calibrated", "created_at")},
                                   "stage_status": json.loads(run["stage_status_json"])} if run else None),
                   "manifest": _manifest(touch_id), "jobs": jobs.list(touch_id, 5)})


@app.patch("/api/touches/{touch_id}")
def patch_touch(touch_id: str, body: dict = Body(...)):
    return store.update_touch(touch_id, body)


@app.delete("/api/touches/{touch_id}")
def delete_touch(touch_id: str):
    store.delete_touch(touch_id)
    return {"deleted": touch_id}


@app.get("/api/touches/{touch_id}/frames/{frame}.jpg")
def frame_jpg(touch_id: str, frame: int):
    t = store.get_touch(touch_id)
    if not t["start_frame"] <= frame < t["end_frame"]:
        raise HTTPException(404, "exact frames are decoded for the touch window only")
    p = os.path.join(store.frames_dir(touch_id), f"{frame:06d}.jpg")
    if not os.path.exists(p):
        # lazy: decode the whole window once (keyframe seek, PTS-verified), e.g. after importing an analysis
        with _extract_lock(touch_id):
            if not os.path.exists(p):
                runner.prepare_touch(store, touch_id)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


_locks: dict = {}


def _extract_lock(touch_id):
    import threading
    return _locks.setdefault(touch_id, threading.Lock())


@app.get("/api/touches/{touch_id}/overlay")
def overlay(touch_id: str):
    sd = store.stage_dir(touch_id)
    tp, pp = os.path.join(sd, "tracks.parquet"), os.path.join(sd, "pose.npz")
    if not os.path.exists(tp):
        return {"available": False, "reason": "tracking has not run for this touch"}
    tr = per.add_track_flags(pd.read_parquet(tp))
    pose, frames, src = per.load_pose_npz(pp)
    out = {"available": True, "source": src, "frames": frames, "sides": {}}
    for side in tx.SIDES:
        g = tr[tr.side == side].sort_values("frame")
        out["sides"][side] = {
            "bbox": [[None if np.isnan(v) else round(float(v), 1) for v in (r.x1, r.y1, r.x2, r.y2)] if r.present else None
                     for r in g.itertuples()],
            "flags": [{"track_break": bool(r.track_break), "id_switch": bool(r.id_switch_suspect),
                       "pair_implausible": bool(r.pair_implausible),
                       "identity_uncertain": bool(r.identity_uncertain), "screen_position": r.screen_position}
                      for r in g.itertuples()],
            # raw keypoints with mask: [x, y, conf, visible]
            "kp": [[[round(float(p[0]), 1), round(float(p[1]), 1), round(float(p[2]), 2), int(m)]
                    if not np.isnan(p[0]) else None for p, m in zip(fr, mk)]
                   for fr, mk in zip(pose[side]["raw"], pose[side]["mask"])],
        }
    return _clean(out)


@app.get("/api/touches/{touch_id}/candidates")
def candidates(touch_id: str):
    """Every tracked person in the window (not only the chosen two), for checking / fixing identity."""
    sd = store.stage_dir(touch_id)
    dp = os.path.join(sd, "detections.parquet")
    if not os.path.exists(dp):
        return {"available": False, "reason": "tracking has not run for this touch"}
    det = pd.read_parquet(dp)
    man = _manifest(touch_id).get("s2_tracking", {}).get("info", {})
    sel = man.get("selection", {})
    by_frame = {}
    for r in det[det.track_id >= 0].itertuples():
        by_frame.setdefault(int(r.frame), []).append([int(r.track_id), round(r.x1, 1), round(r.y1, 1), round(r.x2, 1), round(r.y2, 1)])
    assign = sel.get("assignment") or {}
    cands = sel.get("candidates", [])
    # Display numbers that mean the same thing on every touch of a bout: 1 = the bout's LEFT fencer,
    # 2 = the RIGHT fencer, 3+ = everyone else from left to right. (Raw tracker ids restart on every
    # touch, which is why the numbers used to change from touch to touch.)
    display, nxt = {}, 3
    for side, num in (("left", 1), ("right", 2)):
        if assign.get(side) is not None:
            display[int(assign[side])] = num
            for c in cands:
                if c.get("track_id") == assign[side]:
                    for m in c.get("members", []):
                        display.setdefault(int(m), num)
    others = sorted({int(r.track_id) for r in det[det.track_id >= 0].itertuples()} - set(display),
                    key=lambda tid: float(det[det.track_id == tid].x1.median()))
    for tid in others:
        display[tid] = nxt
        nxt += 1
    return _clean({"available": True, "status": sel.get("status"), "assignment": sel.get("assignment"),
                   "display": {str(k): v for k, v in display.items()},
                   "candidates": sel.get("candidates", []), "rules": sel.get("rules"),
                   "identity_confirmed": runner.read_identity(store, touch_id), "boxes": by_frame})


@app.post("/api/touches/{touch_id}/identity")
def set_identity(touch_id: str, body: dict = Body(...)):
    store.get_touch(touch_id)
    try:
        d = runner.write_identity(store, touch_id, body.get("left"), body.get("right"), who(body.get("reviewer")))
    except (TypeError, ValueError) as e:
        raise IntervalError(str(e)) from e
    job = jobs.submit("analyze_touch", touch_id, pose_source="auto", method="rule_baseline")
    return {"identity": d, "job": job}


@app.delete("/api/touches/{touch_id}/identity")
def reset_identity(touch_id: str, reviewer: str = Query(...)):
    runner.clear_identity(store, touch_id, who(reviewer))
    return {"identity": None, "job": jobs.submit("analyze_touch", touch_id, pose_source="auto", method="rule_baseline")}


@app.get("/api/touches/{touch_id}/predictions")
def get_predictions(touch_id: str, annotator: str = Query(...), blind: bool = False):
    if blind:
        return {"hidden": True, "reason": "blind mode: predictions are not shown"}
    annotator = who(annotator)
    store.mark_predictions_viewed(touch_id, annotator)
    run = store.latest_run(touch_id)
    preds = store.predictions(touch_id)
    evs = store.events(touch_id, sources=["rule", "model"])
    return _clean({"hidden": False, "run": ({k: run[k] for k in ("run_id", "method", "status_label", "model_version",
                                                                 "calibrated", "created_at")} if run else None),
                   "predictions": preds, "events": [e for e in evs if run and e["run_id"] == run["run_id"]]})


@app.post("/api/touches/{touch_id}/annotations")
def create_annotation(touch_id: str, body: dict = Body(...)):
    return store.create_annotation(touch_id, body.get("side"), body.get("start_frame"), body.get("end_frame"),
                                   body.get("label"), who(body.get("annotator_id")), label_source=body.get("label_source"),
                                   ambiguity_flag=body.get("ambiguity_flag", False),
                                   replace_overlaps=body.get("replace_overlaps", False))


@app.patch("/api/annotations/{segment_id}")
def patch_annotation(segment_id: str, body: dict = Body(...)):
    return store.update_annotation(segment_id, body, who(body.get("annotator_id")))


@app.delete("/api/annotations/{segment_id}")
def delete_annotation(segment_id: str, annotator_id: str = Query(...)):
    store.delete_annotation(segment_id, who(annotator_id))
    return {"deleted": segment_id}


@app.get("/api/annotations/{segment_id}/history")
def annotation_history(segment_id: str):
    return [{**r, "snapshot": json.loads(r["snapshot_json"])}
            for r in store.db.q("SELECT * FROM annotation_log WHERE segment_id=? ORDER BY log_id", (segment_id,))]


@app.post("/api/predictions/{segment_id}/review")
def review(segment_id: str, body: dict = Body(...)):
    return store.review_prediction(segment_id, body.get("action"), who(body.get("reviewer")),
                                   corrected_label=body.get("corrected_label"),
                                   corrected_start=body.get("corrected_start_frame"),
                                   corrected_end=body.get("corrected_end_frame"),
                                   error_reason=body.get("error_reason"), comment=body.get("comment"),
                                   replace_overlaps=body.get("replace_overlaps", False))


@app.post("/api/touches/{touch_id}/events")
def add_event(touch_id: str, body: dict = Body(...)):
    return store.add_event(touch_id, body.get("side"), body.get("event_type"), body.get("frame"), "expert",
                           note=body.get("note"))


@app.delete("/api/events/{event_id}")
def delete_event(event_id: str):
    store.delete_event(event_id)
    return {"deleted": event_id}


@app.post("/api/touches/{touch_id}/analyze")
def analyze(touch_id: str, body: dict = Body(default={})):
    store.get_touch(touch_id)
    return jobs.submit("analyze_touch", touch_id, pose_source=body.get("pose_source", "auto"),
                       method=body.get("method", "rule_baseline"), model_id=body.get("model_id"),
                       force=bool(body.get("force", False)))


# ------------------------------------------------------------------ jobs / models / insights
@app.get("/api/jobs")
def list_jobs(target_id: str = None):
    return jobs.list(target_id)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/models")
def models():
    return _clean(ml.list_models(store))


@app.post("/api/models/train")
def train(body: dict = Body(default={})):
    method = body.get("method", "xgboost")
    # test_bouts omitted -> the bouts of every video marked "Test" on the Videos page
    tb = body.get("test_bouts")
    if method == "tcn":
        return jobs.submit("train_tcn", None, test_bouts=tb, seed=int(body.get("seed", 0)))
    if method != "xgboost":
        raise HTTPException(400, f"unknown method {method!r} (use 'xgboost' or 'tcn')")
    return jobs.submit("train_xgboost", None, W=int(body.get("W", 15)), test_bouts=tb, seed=int(body.get("seed", 0)))


@app.get("/api/models/readiness")
def readiness():
    return _clean(ml.readiness(store))


@app.get("/api/models/compare")
def compare_models(a: str, b: str):
    try:
        return _clean(ml.compare(store, a, b))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/bouts")
def list_bouts():
    return store.db.q("SELECT b.bout_id, b.video_id, b.name, v.split, v.title video_title FROM bouts b "
                      "JOIN videos v ON v.video_id=b.video_id ORDER BY b.created_at, b.rowid")


@app.get("/api/insights")
def insights(video_id: str = None, source: str = "annotations"):
    return _clean(stats.insights(store, video_id, source))


# ------------------------------------------------------------------ exports / imports
def _touch_ids(video_id):
    return [t["touch_id"] for t in store.list_touches(video_id=video_id)] if video_id else None


@app.get("/api/export/{what}.{fmt}")
def export(what: str, fmt: str, video_id: str = None, request: Request = None):
    tids = _touch_ids(video_id)
    fields = exports.RECORD_FIELDS + exports.EXTRA_FIELDS
    if what == "predictions":
        rows = exports.segment_records(store, tids, kind="prediction")
    elif what == "annotations":
        rows = exports.segment_records(store, tids, kind="annotation")
    elif what == "reviews":
        rows = [r for r in store.reviews() if not tids or r["touch_id"] in tids]
        fields = list(rows[0].keys()) if rows else ["review_id"]
    elif what == "review":
        base = str(request.base_url).rstrip("/") if request else "http://localhost:8000"
        rows = exports.review_csv_rows(store, tids, base)
        fields = exports.REVIEW_CSV_FIELDS
    elif what == "runs":
        rows = exports.run_manifests(store, tids)
        fields = None
    elif what == "bundle":
        return JSONResponse(_clean(exports.export_bundle(store, video_id)),
                            headers={"Content-Disposition": "attachment; filename=engarde_bundle.json"})
    elif what == "events":
        rows = [e for t in (tids or [x["touch_id"] for x in store.list_touches()]) for e in store.events(t)]
        fields = list(rows[0].keys()) if rows else ["event_id"]
    else:
        raise HTTPException(404, f"unknown export {what}")
    name = f"engarde_{what}.{fmt}"
    if fmt == "json":
        return JSONResponse(_clean(rows), headers={"Content-Disposition": f"attachment; filename={name}"})
    if fmt == "csv" and fields:
        # utf-8 with a byte-order mark, so Excel shows "Advance → lunge" correctly
        return Response(("\ufeff" + exports.to_csv(rows, fields)).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})
    raise HTTPException(400, "unsupported format")


@app.get("/api/airs/{touch_id}")
def airs(touch_id: str, arm: str = "automated", fmt: str = "json"):
    a = exports.airs_timeline(store, touch_id, arm)
    if fmt == "txt":
        return PlainTextResponse(a["timeline_txt"] + "\n")
    return a


@app.post("/api/import/bundle")
async def import_bundle(file: UploadFile = File(...), target_video_id: str = Form(None)):
    return exports.import_bundle(store, json.loads(await file.read()), target_video_id)


@app.post("/api/import/labels")
async def import_labels(file: UploadFile = File(...), target_video_id: str = Form(None), actor: str = Form("import")):
    """Shared labels: accepts a teammate's Full bundle (.json), Training labels (.json) or Training
    labels (.csv). Videos are matched by file fingerprint unless target_video_id is given."""
    try:
        vids = exports.parse_labels_file(await file.read(), file.filename or "")
        return _clean(exports.import_labels(store, vids, target_video_id or None, (who(actor) or "import").strip()))
    except (ValueError, KeyError) as e:
        raise HTTPException(422, f"could not read the labels file: {e}") from e


@app.get("/api/audit")
def audit(limit: int = 100):
    _need_admin("read the activity log")
    return [{**r, "detail": json.loads(r["detail_json"]) if r["detail_json"] else None}
            for r in store.db.q("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,))]


@app.post("/api/import/annotations_csv")
async def import_ann_csv(file: UploadFile = File(...)):
    return exports.import_annotations_csv(store, (await file.read()).decode("utf-8-sig"))


@app.post("/api/import/review_csv")
async def import_review_csv(file: UploadFile = File(...)):
    return exports.import_review_csv(store, (await file.read()).decode("utf-8-sig"))


# ------------------------------------------------------------------ sign-in / team (v0.5.0)
def _is_https(request: Request) -> bool:
    """True behind Tailscale Serve (which always serves https and adds its identity header) or any
    proxy that says so; the session cookie is then marked Secure."""
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https" \
        or "tailscale-user-login" in request.headers


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = auth.user_for_token(store.db, request.cookies.get(auth.COOKIE)) if TEAM else None
    return {"team_mode": TEAM, "user": user, "version": VERSION,
            "needs_first_admin": TEAM and not auth.has_admin(store.db)}


@app.post("/api/auth/login")
def auth_login(request: Request, body: dict = Body(...)):
    _need_team()
    name = (body.get("name") or "").strip()
    wait = _throttle.blocked(name.lower())
    if wait:
        raise HTTPException(429, f"Too many wrong passwords for this name. Try again in {wait} seconds.")
    user = auth.verify(store.db, name, body.get("password") or "")
    if not user:
        _throttle.fail(name.lower())
        store.audit(name or "unknown", "login_failed", "auth")
        raise HTTPException(401, "Wrong name or password.")
    _throttle.ok(name.lower())
    store.audit(user["name"], "login", "auth", {"tailscale_account": request.headers.get("tailscale-user-login")})
    token = auth.new_session(store.db, user["user_id"])
    https = _is_https(request)
    resp = JSONResponse({"user": auth._public(store.db.one("SELECT * FROM users WHERE user_id=?", (user["user_id"],)))})
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.end_session(store.db, request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"signed_out": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.post("/api/auth/password")
def auth_password(request: Request, body: dict = Body(...)):
    _need_team()
    u = _current.get()
    if not auth.verify(store.db, u["name"], body.get("old") or ""):
        raise HTTPException(403, "The current password is wrong.")
    auth.update_user(store.db, u["user_id"], password=body.get("new"))
    token = auth.new_session(store.db, u["user_id"])            # other devices are signed out, this one stays in
    https = _is_https(request)
    resp = JSONResponse({"changed": True})
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")
    return resp


@app.get("/api/team/users")
def team_users():
    _need_team()
    _need_admin("see the accounts")
    return auth.list_users(store.db)


@app.post("/api/team/users")
def team_add_user(body: dict = Body(...)):
    _need_team()
    _need_admin("add people")
    u = auth.create_user(store.db, body.get("name"), body.get("password"), body.get("role") or "member")
    store.audit(who(), "user_added", u["name"], {"role": u["role"]})
    return u


@app.patch("/api/team/users/{user_id}")
def team_patch_user(user_id: str, body: dict = Body(...)):
    _need_team()
    _need_admin("change accounts")
    u = auth.update_user(store.db, user_id, role=body.get("role"), active=body.get("active"), password=body.get("password"))
    store.audit(who(), "user_changed", u["name"], {k: body[k] for k in ("role", "active") if k in body}
                | ({"password": "reset"} if body.get("password") else {}))
    return u


@app.post("/api/videos/{video_id}/unlock_request")
def unlock_request(video_id: str, body: dict = Body(default={})):
    _need_team()
    return store.request_unlock(video_id, who(), body.get("reason"))


@app.get("/api/team/unlock_requests")
def unlock_requests():
    _need_team()
    return store.unlock_requests("pending", None if _is_admin() else who())


@app.post("/api/team/unlock_requests/{request_id}")
def decide_unlock(request_id: str, body: dict = Body(...)):
    _need_team()
    _need_admin("approve or refuse an unlock request")
    return store.decide_unlock(request_id, bool(body.get("approve")), who())


@app.post("/api/team/backup")
def backup_now():
    _need_admin("start a backup")
    return backup.run_backup(store)


def _changelog(n_sections=2) -> str:
    p = os.path.join(os.path.dirname(__file__), "..", "..", "CHANGELOG.md")
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        parts = f.read().split("\n## ")
    secs = parts[1:1 + n_sections]
    return ("## " + "\n## ".join(secs)) if secs else ""


@app.get("/api/about")
def about():
    """Home page: which version is running, what changed, when the last backup was."""
    return {"version": VERSION, "team_mode": TEAM, "started_at": STARTED_AT, "changelog": _changelog(),
            "backup": backup.status() if TEAM else None, "backup_dir": backup.backup_dir_setting(store) if TEAM else None,
            "label_set": store.tax.preset}


# ------------------------------------------------------------------ frontend
_dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
