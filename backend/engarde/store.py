"""Business rules on top of the DB: videos, bouts, fencers, touches, annotations, reviews, events."""
from __future__ import annotations

import json
import os
import shutil

from . import taxonomy as tx
from . import video as vid
from .db import DB, new_id, now_iso
from .intervals import IntervalError, find_overlap, validate_interval


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    def __init__(self, msg, detail=None):
        super().__init__(msg)
        self.detail = detail


class Locked(PermissionError):
    """The video is locked: labels, reviews and touches cannot change until it is unlocked."""


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        db_path = os.path.join(self.data_dir, "engarde.sqlite")
        existed = os.path.exists(db_path)
        self.tax = tx.load_taxonomy(self.data_dir)
        if existed:
            self._backup_before_upgrade(db_path)
        self.db = DB(db_path)
        self.label_migration = self._migrate_labels()

    # ------------------------------------------------------------------ upgrades
    def _backup_before_upgrade(self, db_path):
        """One copy of the database as it was before v0.4.0 touched it (label names change)."""
        bak = db_path + ".before-v0.4.0"
        if os.path.exists(bak):
            return
        try:
            import sqlite3
            src = sqlite3.connect(db_path)
            has_flags = src.execute("SELECT 1 FROM sqlite_master WHERE name='app_flags'").fetchone()
            if not has_flags:                      # only databases made by an older version
                dst = sqlite3.connect(bak)
                src.backup(dst)
                dst.close()
            src.close()
        except Exception:  # noqa: BLE001 - a failed backup must not stop the app from starting
            pass

    def _migrate_labels(self) -> dict:
        """Rename human labels saved under an older label set to the current one (for example
        Advance -> Step forward). Each change is appended to annotation_log; the original label
        stays readable in the history, and predictions (immutable) are only renamed on display."""
        if not self.tax.legacy:
            return {"renamed": 0}
        rows = self.db.q("SELECT segment_id, label FROM action_segments WHERE kind='annotation'")
        n, by = 0, {}
        with self.db.transaction():
            for r in rows:
                new = self.tax.canon(r["label"])
                if new != r["label"] and new in self.tax.annotation_labels:
                    self.db.update("action_segments", "segment_id", r["segment_id"],
                                   {"label": new, "label_version": f"{self.tax.preset} (renamed from {r['label']})"})
                    self._log(r["segment_id"], "relabel", "system:v0.4.0")
                    by[f"{r['label']} -> {new}"] = by.get(f"{r['label']} -> {new}", 0) + 1
                    n += 1
        if n:
            self.audit("system:v0.4.0", "relabel", "all", by)
        return {"renamed": n, "by": by}

    def audit(self, actor, action, target, detail=None):
        self.db.insert("audit_log", {"at": now_iso(), "actor": actor or "unknown", "action": action,
                                     "target": target, "detail_json": json.dumps(detail, default=str) if detail is not None else None})

    def admins(self) -> list[str]:
        """Names allowed to unlock anything and to delete videos: data/team.json {"admins": ["Name"]}.
        The app has no logins, so this is an honour system until it runs on a shared server."""
        p = os.path.join(self.data_dir, "team.json")
        if not os.path.exists(p):
            return []
        try:
            with open(p, encoding="utf-8") as f:
                return [str(a).strip().lower() for a in json.load(f).get("admins", [])]
        except Exception:  # noqa: BLE001
            return []

    def is_admin(self, actor) -> bool:
        adm = self.admins()
        return not adm or (actor or "").strip().lower() in adm

    # ------------------------------------------------------------------ lock / split
    def ensure_unlocked(self, video_id):
        v = self.db.one("SELECT locked, locked_by, title FROM videos WHERE video_id=?", (video_id,))
        if v and v["locked"]:
            raise Locked(f"\"{v['title']}\" is locked by {v['locked_by'] or 'someone'}. "
                         f"Ask them or an admin to unlock it on the Videos page before changing labels.")

    def set_lock(self, video_id, locked: bool, actor: str, admin: bool | None = None):
        """admin=None: single-computer mode (data/team.json decides who may unlock).
        admin=True/False: team mode, the signed-in person's role decides."""
        v = self.get_video(video_id)
        if not actor:
            raise IntervalError("your name (Annotator ID) is required to lock or unlock a video")
        if locked:
            if not v["locked"]:
                self.db.update("videos", "video_id", video_id, {"locked": 1, "locked_by": actor, "locked_at": now_iso()})
                self.audit(actor, "lock", video_id)
        elif v["locked"]:
            same = (v["locked_by"] or "").strip().lower() == actor.strip().lower()
            if admin is None:
                adm = self.admins()    # no admin list configured -> anyone may unlock (name is recorded)
                allowed = same or not adm or actor.strip().lower() in adm
            else:
                allowed = same or admin
            if not allowed:
                raise Locked(f"only {v['locked_by']} or an admin can unlock this video. Use \"Ask to unlock\".")
            self.db.update("videos", "video_id", video_id, {"locked": 0, "locked_by": None, "locked_at": None})
            self.db.x("UPDATE unlock_requests SET status='approved', decided_by=?, decided_at=? "
                      "WHERE video_id=? AND status='pending'", (actor, now_iso(), video_id))
            self.audit(actor, "unlock", video_id, {"was_locked_by": v["locked_by"]})
        return self.get_video(video_id)

    def request_unlock(self, video_id, actor, reason=None) -> dict:
        v = self.get_video(video_id)
        if not v["locked"]:
            raise Conflict("this video is not locked")
        old = self.db.one("SELECT * FROM unlock_requests WHERE video_id=? AND requested_by=? AND status='pending'",
                          (video_id, actor))
        if old:
            return old
        rid = new_id("ulr")
        self.db.insert("unlock_requests", {"request_id": rid, "video_id": video_id, "requested_by": actor,
                                           "reason": (reason or "").strip()[:300] or None, "created_at": now_iso()})
        self.audit(actor, "unlock_request", video_id, {"reason": reason})
        return self.db.one("SELECT * FROM unlock_requests WHERE request_id=?", (rid,))

    def unlock_requests(self, status="pending", requested_by=None) -> list[dict]:
        sql = ("SELECT r.*, v.title video_title, v.locked_by FROM unlock_requests r LEFT JOIN videos v ON v.video_id=r.video_id "
               "WHERE r.status=?")
        params = [status]
        if requested_by:
            sql += " AND r.requested_by=?"
            params.append(requested_by)
        return self.db.q(sql + " ORDER BY r.created_at", params)

    def decide_unlock(self, request_id, approve: bool, admin_name) -> dict:
        r = self.db.one("SELECT * FROM unlock_requests WHERE request_id=?", (request_id,))
        if not r or r["status"] != "pending":
            raise NotFound(f"pending unlock request {request_id}")
        if approve and self.db.one("SELECT locked FROM videos WHERE video_id=?", (r["video_id"],)):
            self.set_lock(r["video_id"], False, admin_name, admin=True)
        self.db.update("unlock_requests", "request_id", request_id,
                       {"status": "approved" if approve else "denied", "decided_by": admin_name, "decided_at": now_iso()})
        self.audit(admin_name, "unlock_approved" if approve else "unlock_denied", r["video_id"],
                   {"requested_by": r["requested_by"]})
        return self.db.one("SELECT * FROM unlock_requests WHERE request_id=?", (request_id,))

    def set_split(self, video_id, split, actor=None):
        if split in ("", "none"):
            split = None
        if split not in (None, "train", "test"):
            raise IntervalError("split must be 'train', 'test' or empty")
        self.get_video(video_id)
        self.db.update("videos", "video_id", video_id, {"split": split})
        self.audit(actor, "split", video_id, {"split": split})
        return self.get_video(video_id)

    def test_bout_ids(self) -> list[str]:
        return [r["bout_id"] for r in self.db.q(
            "SELECT b.bout_id FROM bouts b JOIN videos v ON v.video_id=b.video_id WHERE v.split='test'")]

    # ------------------------------------------------------------------ delete video
    def delete_video(self, video_id, actor) -> dict:
        """Removes a video and everything made from it. The human work (touches, labels, reviews,
        expert events) is first saved as a re-importable bundle in data/trash/."""
        v = self.get_video(video_id)
        if not actor:
            raise IntervalError("your name (Annotator ID) is required to delete a video")
        if v["locked"]:
            raise Locked(f"\"{v['title']}\" is locked by {v['locked_by']}; unlock it before deleting")
        tids = [r["touch_id"] for r in self.db.q("SELECT touch_id FROM touches WHERE video_id=?", (video_id,))]
        busy = self.db.q("SELECT job_id FROM jobs WHERE status IN ('queued','running') AND target_id IN (%s)"
                         % ",".join("?" * (len(tids) + 1)), [video_id] + tids)
        if busy:
            raise Conflict("analysis is still running for this video; wait until it finishes, then delete")
        n_ann = self.db.one("SELECT COUNT(*) n FROM action_segments s JOIN touches t ON t.touch_id=s.touch_id "
                            "WHERE t.video_id=? AND s.kind='annotation' AND s.deleted=0", (video_id,))["n"]
        n_rev = self.db.one("SELECT COUNT(*) n FROM review_records r JOIN touches t ON t.touch_id=r.touch_id "
                            "WHERE t.video_id=?", (video_id,))["n"]
        backup = None
        if tids and v["indexed"]:
            from . import exports
            trash = os.path.join(self.data_dir, "trash")
            os.makedirs(trash, exist_ok=True)
            backup = os.path.join(trash, f"{video_id}_{now_iso().replace(':', '').replace('+', 'Z')[:17]}.json")
            with open(backup, "w", encoding="utf-8") as f:
                json.dump(exports.export_bundle(self, video_id), f, default=str)
        with self.db.transaction() as db:
            db.x("INSERT OR REPLACE INTO app_flags (key, value) VALUES ('deleting_video', '1')")
            try:
                for tid in tids:
                    db.x("DELETE FROM review_records WHERE touch_id=?", (tid,))
                    db.x("DELETE FROM events WHERE touch_id=?", (tid,))
                    db.x("DELETE FROM action_segments WHERE touch_id=?", (tid,))
                    db.x("DELETE FROM prediction_runs WHERE touch_id=?", (tid,))
                    db.x("DELETE FROM prediction_views WHERE touch_id=?", (tid,))
                    db.x("DELETE FROM jobs WHERE target_id=?", (tid,))
                db.x("DELETE FROM touches WHERE video_id=?", (video_id,))
                db.x("DELETE FROM fencers WHERE bout_id IN (SELECT bout_id FROM bouts WHERE video_id=?)", (video_id,))
                db.x("DELETE FROM bouts WHERE video_id=?", (video_id,))
                db.x("DELETE FROM jobs WHERE target_id=?", (video_id,))
                db.x("DELETE FROM unlock_requests WHERE video_id=?", (video_id,))
                db.x("DELETE FROM videos WHERE video_id=?", (video_id,))
            finally:
                db.x("INSERT OR REPLACE INTO app_flags (key, value) VALUES ('deleting_video', '0')")
        leftovers = []
        for d in [self.touch_dir(t) for t in tids] + [self.video_dir(video_id)]:
            shutil.rmtree(d, ignore_errors=True)
            if os.path.exists(d):
                leftovers.append(d)      # Windows keeps a file that is still open in the browser
        out = {"deleted": video_id, "title": v["title"], "touches": len(tids), "labels": n_ann, "reviews": n_rev,
               "backup": backup, "leftover_folders": leftovers}
        self.audit(actor, "delete_video", video_id, out)
        return out

    # ------------------------------------------------------------------ paths
    def video_dir(self, video_id):
        return os.path.join(self.data_dir, "videos", video_id)

    def touch_dir(self, touch_id):
        return os.path.join(self.data_dir, "touches", touch_id)

    def frames_dir(self, touch_id):
        return os.path.join(self.touch_dir(touch_id), "frames")

    def stage_dir(self, touch_id):
        return os.path.join(self.touch_dir(touch_id), "stages")

    # ------------------------------------------------------------------ videos
    def add_video(self, src_path: str, title: str, *, move=False, capture_type="unknown",
                  weapon_type="unknown", is_synthetic=False) -> dict:
        video_id = new_id("vid")
        d = self.video_dir(video_id)
        os.makedirs(d, exist_ok=True)
        ext = os.path.splitext(src_path)[1].lower() or ".mp4"
        dst = os.path.join(d, "original" + ext)
        (shutil.move if move else shutil.copy2)(src_path, dst)
        meta = vid.probe(dst)
        cv = vid.canvas_size(dst)
        meta["canvas"] = cv
        row = {
            "video_id": video_id, "title": title, "path": dst, "width": cv["width"], "height": cv["height"],
            "fps_nominal": meta["fps_nominal"], "codec": meta["codec"], "capture_type": capture_type,
            "weapon_type": weapon_type, "is_synthetic": int(is_synthetic), "created_at": now_iso(),
        }
        self.db.insert("videos", row)
        with open(os.path.join(d, "probe.json"), "w") as f:
            json.dump(meta, f, indent=2)
        return self.get_video(video_id)

    def index_video(self, video_id: str, make_proxy: bool = True) -> dict:
        v = self.get_video(video_id)
        idx = vid.build_frame_index(v["path"])
        vid.save_index(idx, os.path.join(self.video_dir(video_id), "frame_index.npz"))
        s = vid.index_summary(idx)
        meta = json.load(open(os.path.join(self.video_dir(video_id), "probe.json")))
        if make_proxy and vid.needs_browser_proxy(meta, v["path"]):
            vid.make_browser_proxy(v["path"], os.path.join(self.video_dir(video_id), "proxy.mp4"))
        self.db.update("videos", "video_id", video_id, {
            "n_frames": s["n_frames"], "duration_s": s["duration_s"], "fps_measured": s["fps_measured"],
            "vfr": int(s["vfr"]), "sha256": vid.sha256_file(v["path"])})
        video_meta = {**self.get_video(video_id), "probe": meta, "index": s,
                      "frame_numbering": "0-based decoded frame index; timestamps = best_effort_timestamp_time"}
        with open(os.path.join(self.video_dir(video_id), "video_meta.json"), "w") as f:
            json.dump(video_meta, f, indent=2)
        return video_meta

    def frame_index(self, video_id: str) -> dict:
        p = os.path.join(self.video_dir(video_id), "frame_index.npz")
        if not os.path.exists(p):
            raise Conflict("video is not indexed yet")
        return vid.load_index(p)

    def playback_path(self, video_id):
        v = self.get_video(video_id)
        proxy = os.path.join(self.video_dir(video_id), "proxy.mp4")
        return proxy if os.path.exists(proxy) else v["path"]

    def get_video(self, video_id) -> dict:
        v = self.db.one("SELECT * FROM videos WHERE video_id=?", (video_id,))
        if not v:
            raise NotFound(f"video {video_id}")
        self._heal_path(v)
        v["indexed"] = v["n_frames"] is not None
        v["calibrated"] = v["calibration_json"] is not None
        v["linked"] = os.path.exists(v["path"])
        v["content_rect"] = self._content_rect(video_id, v) if v["linked"] or os.path.exists(
            os.path.join(self.video_dir(video_id), "content_rect.json")) else None
        return v

    def _heal_path(self, v):
        """The database stores each video's full path. When the app folder is renamed or moved, that
        path is stale although the file is still in this data folder: find it there and fix the row."""
        p = v.get("path")
        if p and not os.path.exists(p):
            cand = os.path.join(self.video_dir(v["video_id"]), os.path.basename(p.replace("\\", "/")))
            if os.path.exists(cand):
                self.db.update("videos", "video_id", v["video_id"], {"path": cand})
                v["path"] = cand
        return v

    def _content_rect(self, video_id, v):
        p = os.path.join(self.video_dir(video_id), "content_rect.json")
        if os.path.exists(p):
            return json.load(open(p))
        if not v.get("n_frames"):
            return None
        r = vid.content_rect(v["path"], self.frame_index(video_id))
        with open(p, "w") as f:
            json.dump(r, f)
        return r

    def list_videos(self) -> list[dict]:
        """Every video with its counts. v0.5.6: built from a few grouped queries (one per kind of count)
        instead of about eight queries per video, so the Videos page stays fast with hundreds of videos."""
        vids = self.db.q("SELECT * FROM videos ORDER BY created_at DESC")
        bouts_by, fencers_by = {}, {}
        for f in self.db.q("SELECT * FROM fencers ORDER BY side"):
            fencers_by.setdefault(f["bout_id"], []).append(f)
        for b in self.db.q("SELECT * FROM bouts ORDER BY rowid"):
            b["fencers"] = fencers_by.get(b["bout_id"], [])
            bouts_by.setdefault(b["video_id"], []).append(b)
        n_t = {r["video_id"]: r["n"] for r in self.db.q("SELECT video_id, COUNT(*) n FROM touches GROUP BY video_id")}
        lab = {r["video_id"]: r for r in self.db.q(
            "SELECT t.video_id, COUNT(DISTINCT s.touch_id) nt, COUNT(*) n FROM action_segments s "
            "JOIN touches t ON t.touch_id=s.touch_id WHERE s.kind='annotation' AND s.deleted=0 GROUP BY t.video_id")}
        pend = {r["video_id"]: r["n"] for r in self.db.q(
            """WITH latest AS (SELECT touch_id, run_id FROM (SELECT touch_id, run_id, ROW_NUMBER() OVER
                   (PARTITION BY touch_id ORDER BY created_at DESC, rowid DESC) rn FROM prediction_runs) WHERE rn=1)
               SELECT t.video_id, COUNT(*) n FROM action_segments s
               JOIN latest l ON l.touch_id=s.touch_id AND l.run_id=s.run_id
               JOIN touches t ON t.touch_id=s.touch_id
               WHERE s.kind='prediction'
                 AND NOT EXISTS (SELECT 1 FROM review_records rr WHERE rr.prediction_segment_id=s.segment_id)
               GROUP BY t.video_id""")}
        jobs = {r["target_id"]: {k: r[k] for k in ("job_id", "kind", "status", "stage", "progress", "message", "created_at")}
                for r in self.db.q(
            """SELECT * FROM (SELECT job_id, kind, status, stage, progress, message, created_at, target_id,
                   ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY created_at DESC, rowid DESC) rn
                   FROM jobs WHERE target_id IN (SELECT video_id FROM videos)) WHERE rn=1""")}
        out = []
        for v in vids:
            self._heal_path(v)
            v["linked"] = os.path.exists(v["path"])
            v["indexed"] = v["n_frames"] is not None
            v["calibrated"] = v["calibration_json"] is not None
            vid_ = v["video_id"]
            v["bouts"] = bouts_by.get(vid_, [])
            v["n_touches"] = n_t.get(vid_, 0)
            v["pending_review"] = pend.get(vid_, 0)
            v["n_labelled_touches"] = lab[vid_]["nt"] if vid_ in lab else 0
            v["n_labels"] = lab[vid_]["n"] if vid_ in lab else 0
            v["review_status"] = self.review_status(v["n_touches"], v["n_labelled_touches"])
            v["job"] = jobs.get(vid_)
            out.append(v)
        return out

    def list_videos_slow(self) -> list[dict]:
        """The pre-v0.5.6 version, kept only so a test can check the fast one gives the same answer."""
        out = []
        for v in self.db.q("SELECT * FROM videos ORDER BY created_at DESC"):
            self._heal_path(v)
            v["linked"] = os.path.exists(v["path"])
            v["indexed"] = v["n_frames"] is not None
            v["calibrated"] = v["calibration_json"] is not None
            v["bouts"] = self.db.q("SELECT * FROM bouts WHERE video_id=?", (v["video_id"],))
            for b in v["bouts"]:
                b["fencers"] = self.fencers(b["bout_id"])
            v["n_touches"] = self.db.one("SELECT COUNT(*) n FROM touches WHERE video_id=?", (v["video_id"],))["n"]
            v["pending_review"] = self.pending_review_count(video_id=v["video_id"])
            v["n_labelled_touches"] = self.db.one(
                "SELECT COUNT(DISTINCT s.touch_id) n FROM action_segments s JOIN touches t ON t.touch_id=s.touch_id "
                "WHERE t.video_id=? AND s.kind='annotation' AND s.deleted=0", (v["video_id"],))["n"]
            v["n_labels"] = self.db.one(
                "SELECT COUNT(*) n FROM action_segments s JOIN touches t ON t.touch_id=s.touch_id "
                "WHERE t.video_id=? AND s.kind='annotation' AND s.deleted=0", (v["video_id"],))["n"]
            v["review_status"] = self.review_status(v["n_touches"], v["n_labelled_touches"])
            j = self.db.one("SELECT job_id, kind, status, stage, progress, message, created_at FROM jobs "
                            "WHERE target_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (v["video_id"],))
            v["job"] = j
            out.append(v)
        return out

    @staticmethod
    def review_status(n_touches, n_labelled) -> str:
        """Plain status for a video: no_touches | pending | in_progress | reviewed.
        'reviewed' = every touch has at least one human label."""
        if not n_touches:
            return "no_touches"
        if n_labelled >= n_touches:
            return "reviewed"
        return "in_progress" if n_labelled else "pending"

    # ------------------------------------------------------------------ bouts / fencers
    def add_bout(self, video_id, name, left=None, right=None) -> dict:
        self.get_video(video_id)
        bout_id = new_id("bout")
        self.db.insert("bouts", {"bout_id": bout_id, "video_id": video_id, "name": name, "created_at": now_iso()})
        for side, info in (("left", left or {}), ("right", right or {})):
            self.db.insert("fencers", {"fencer_id": new_id("fnc"), "bout_id": bout_id, "side": side,
                                       "athlete_id": info.get("athlete_id"),
                                       "display_name": info.get("display_name")})
        return self.get_bout(bout_id)

    def get_bout(self, bout_id):
        b = self.db.one("SELECT * FROM bouts WHERE bout_id=?", (bout_id,))
        if not b:
            raise NotFound(f"bout {bout_id}")
        b["fencers"] = self.fencers(bout_id)
        return b

    def fencers(self, bout_id):
        return self.db.q("SELECT * FROM fencers WHERE bout_id=? ORDER BY side", (bout_id,))

    def update_fencer(self, fencer_id, changes):
        allowed = {k: v for k, v in changes.items() if k in ("athlete_id", "display_name")}
        self.db.update("fencers", "fencer_id", fencer_id, allowed)

    # ------------------------------------------------------------------ touches
    def add_touch(self, bout_id, light_frame: int, lamp_left=None, lamp_right=None, notes=None,
                  light_source="manual", start_frame=None, end_frame=None) -> dict:
        b = self.get_bout(bout_id)
        v = self.get_video(b["video_id"])
        self.ensure_unlocked(v["video_id"])
        idx = self.frame_index(v["video_id"])
        n = len(idx["t"])
        if not isinstance(light_frame, int) or not 0 <= light_frame < n:
            raise IntervalError(f"light_frame must be an integer in [0, {n})")
        ws, we, clipped = vid.window_frames(idx, light_frame, tx.WINDOW_PRE_S, tx.WINDOW_POST_S)
        if start_frame is not None or end_frame is not None:  # manual touch range override
            s = ws if start_frame is None else start_frame
            e = we if end_frame is None else end_frame
            validate_interval(s, e, 0, n, "touch range")
            if not s <= light_frame < e:
                raise IntervalError("touch range must contain the light frame")
            ws, we = s, e
        seq = (self.db.one("SELECT MAX(seq) m FROM touches WHERE bout_id=?", (bout_id,))["m"] or 0) + 1
        touch_id = new_id("tch")
        self.db.insert("touches", {
            "touch_id": touch_id, "bout_id": bout_id, "video_id": v["video_id"], "seq": seq,
            "light_frame": light_frame, "light_time_s": float(idx["t"][light_frame]),
            "lamp_left": None if lamp_left is None else int(bool(lamp_left)),
            "lamp_right": None if lamp_right is None else int(bool(lamp_right)),
            "light_source": light_source, "start_frame": ws, "end_frame": we, "window_clipped": int(clipped),
            "notes": notes, "created_at": now_iso()})
        return self.get_touch(touch_id)

    def update_touch(self, touch_id, changes):
        t = self.get_touch(touch_id)
        self.ensure_unlocked(t["video_id"])
        allowed = {k: changes[k] for k in ("lamp_left", "lamp_right", "notes") if k in changes}
        if "light_source" in changes:
            if changes["light_source"] not in ("manual", "manual_verified", "detector_candidate"):
                raise IntervalError("light_source must be manual | manual_verified | detector_candidate")
            allowed["light_source"] = changes["light_source"]
        if "light_frame" in changes and changes["light_frame"] != t["light_frame"]:
            has = self.db.one("SELECT COUNT(*) n FROM action_segments WHERE touch_id=? AND deleted=0", (touch_id,))
            if has["n"]:
                raise Conflict("light_frame cannot change once segments or predictions exist; "
                               "create a new touch at the right frame and delete this one")
            idx = self.frame_index(t["video_id"])
            lf = changes["light_frame"]
            ws, we, clipped = vid.window_frames(idx, lf, tx.WINDOW_PRE_S, tx.WINDOW_POST_S)
            allowed.update(light_frame=lf, light_time_s=float(idx["t"][lf]), start_frame=ws, end_frame=we,
                           window_clipped=int(clipped))
        self.db.update("touches", "touch_id", touch_id, allowed)
        return self.get_touch(touch_id)

    def delete_touch(self, touch_id):
        """Only touches without human work can be deleted (predictions are regenerable; labels and
        reviews are not)."""
        self.ensure_unlocked(self.get_touch(touch_id)["video_id"])
        n_ann = self.db.one("SELECT COUNT(*) n FROM action_segments WHERE touch_id=? AND kind='annotation'", (touch_id,))["n"]
        n_rev = self.db.one("SELECT COUNT(*) n FROM review_records WHERE touch_id=?", (touch_id,))["n"]
        n_ev = self.db.one("SELECT COUNT(*) n FROM events WHERE touch_id=? AND source='expert'", (touch_id,))["n"]
        if n_ann or n_rev or n_ev:
            raise Conflict("touch has human labels, reviews or expert events; it cannot be deleted")
        with self.db.transaction() as db:
            db.x("DELETE FROM events WHERE touch_id=?", (touch_id,))
            db.x("DELETE FROM action_segments WHERE touch_id=?", (touch_id,))
            db.x("DELETE FROM prediction_runs WHERE touch_id=?", (touch_id,))
            db.x("DELETE FROM prediction_views WHERE touch_id=?", (touch_id,))
            db.x("DELETE FROM touches WHERE touch_id=?", (touch_id,))
        shutil.rmtree(self.touch_dir(touch_id), ignore_errors=True)

    def get_touch(self, touch_id) -> dict:
        t = self.db.one("SELECT * FROM touches WHERE touch_id=?", (touch_id,))
        if not t:
            raise NotFound(f"touch {touch_id}")
        # the touch number is its position in time within the bout, not the order it was added in
        t["seq"] = self.db.one("SELECT COUNT(*) n FROM touches WHERE bout_id=? AND (light_frame < ? OR "
                               "(light_frame = ? AND rowid < (SELECT rowid FROM touches WHERE touch_id=?)))",
                               (t["bout_id"], t["light_frame"], t["light_frame"], touch_id))["n"] + 1
        return t

    def touch_times(self, touch_id) -> dict:
        """Per-frame timestamps inside the window: abs seconds and ms relative to the light."""
        t = self.get_touch(touch_id)
        idx = self.frame_index(t["video_id"])
        ts = idx["t"]
        frames = list(range(t["start_frame"], t["end_frame"]))
        tl = ts[t["light_frame"]]
        return {"frames": frames, "t_s": [float(ts[f]) for f in frames],
                "rel_ms": [round(float((ts[f] - tl) * 1000), 3) for f in frames], "t0_video_s": float(ts[0])}

    def rel_ms(self, touch: dict, frame: int, idx=None) -> float:
        idx = idx or self.frame_index(touch["video_id"])
        return round(float((idx["t"][frame] - idx["t"][touch["light_frame"]]) * 1000), 3)

    def list_touches(self, bout_id=None, video_id=None):
        # always in time order: a touch added by hand later still lands in its place in the bout
        if bout_id:
            rows = self.db.q("SELECT * FROM touches WHERE bout_id=? ORDER BY light_frame, rowid", (bout_id,))
        elif video_id:
            rows = self.db.q("SELECT * FROM touches WHERE video_id=? ORDER BY light_frame, rowid", (video_id,))
        else:
            rows = self.db.q("SELECT * FROM touches ORDER BY video_id, bout_id, light_frame, rowid")
        counter: dict = {}
        for r in rows:
            counter[r["bout_id"]] = counter.get(r["bout_id"], 0) + 1
            r["seq"] = counter[r["bout_id"]]
            r["n_annotations"] = self.db.one(
                "SELECT COUNT(*) n FROM action_segments WHERE touch_id=? AND kind='annotation' AND deleted=0",
                (r["touch_id"],))["n"]
            r["pending_review"] = self.pending_review_count(touch_id=r["touch_id"])
            r["frames_ready"] = os.path.isdir(self.frames_dir(r["touch_id"])) and \
                len(os.listdir(self.frames_dir(r["touch_id"]))) >= r["end_frame"] - r["start_frame"]
            run = self.latest_run(r["touch_id"])
            r["latest_run"] = {k: run[k] for k in ("run_id", "method", "status_label", "created_at")} if run else None
        return rows

    # ------------------------------------------------------------------ predictions
    def latest_run(self, touch_id, method=None):
        if method:
            return self.db.one("SELECT * FROM prediction_runs WHERE touch_id=? AND method=? "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1", (touch_id, method))
        return self.db.one("SELECT * FROM prediction_runs WHERE touch_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                           (touch_id,))

    def predictions(self, touch_id, run_id=None) -> list[dict]:
        run = self.db.one("SELECT * FROM prediction_runs WHERE run_id=?", (run_id,)) if run_id else self.latest_run(touch_id)
        if not run:
            return []
        rows = self.db.q("SELECT * FROM action_segments WHERE kind='prediction' AND run_id=? ORDER BY side, start_frame",
                         (run["run_id"],))
        for r in rows:
            r["class_probs"] = json.loads(r.pop("class_probs_json")) if r.get("class_probs_json") else None
            # stored predictions never change; an older run's label names are translated for display
            if r["label"] != "none":
                r["label"] = self.tax.canon(r["label"])
            r["top2_label"] = self.tax.canon(r["top2_label"]) if r.get("top2_label") else r.get("top2_label")
            r["reviews"] = self.db.q("SELECT * FROM review_records WHERE prediction_segment_id=? ORDER BY reviewed_at",
                                     (r["segment_id"],))
        return rows

    def mark_predictions_viewed(self, touch_id, annotator_id):
        if not annotator_id:
            raise IntervalError("annotator_id is required to view predictions (blind-label firewall)")
        self.db.x("INSERT OR IGNORE INTO prediction_views (touch_id, annotator_id, first_viewed_at) VALUES (?,?,?)",
                  (touch_id, annotator_id, now_iso()))

    def has_viewed(self, touch_id, annotator_id) -> bool:
        return self.db.one("SELECT 1 x FROM prediction_views WHERE touch_id=? AND annotator_id=?",
                           (touch_id, annotator_id)) is not None

    def pending_review_count(self, touch_id=None, video_id=None) -> int:
        cond, params = "", ()
        if touch_id:
            cond, params = "AND s.touch_id=?", (touch_id,)
        elif video_id:
            cond, params = "AND t.video_id=?", (video_id,)
        # only the latest run per touch counts
        sql = f"""SELECT COUNT(*) n FROM action_segments s JOIN touches t ON t.touch_id=s.touch_id
                  WHERE s.kind='prediction' {cond}
                  AND s.run_id = (SELECT r.run_id FROM prediction_runs r WHERE r.touch_id=s.touch_id
                                  ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1)
                  AND NOT EXISTS (SELECT 1 FROM review_records rr WHERE rr.prediction_segment_id=s.segment_id)"""
        return self.db.one(sql, params)["n"]

    # ------------------------------------------------------------------ annotations
    def _resolve_label_source(self, touch_id, annotator_id, requested: str | None) -> str:
        if self.has_viewed(touch_id, annotator_id):
            return "model_assisted"   # firewall: seen predictions => never blind/adjudicated
        if requested in ("blind", "adjudicated"):
            return requested
        if requested == "model_assisted":
            return "model_assisted"
        return "blind"

    def annotations(self, touch_id, include_deleted=False) -> list[dict]:
        sql = "SELECT * FROM action_segments WHERE kind='annotation' AND touch_id=?"
        if not include_deleted:
            sql += " AND deleted=0"
        return self.db.q(sql + " ORDER BY side, start_frame", (touch_id,))

    def _check_annotation(self, touch, side, start, end, label, exclude_id=None):
        if side not in tx.SIDES:
            raise IntervalError("side must be 'left' or 'right'")
        if label not in self.tax.annotation_labels:
            raise IntervalError(f"label '{label}' is not enabled; allowed: {self.tax.annotation_labels}")
        validate_interval(start, end, touch["start_frame"], touch["end_frame"])
        same = [a for a in self.annotations(touch["touch_id"]) if a["side"] == side and a["segment_id"] != exclude_id]
        o = find_overlap(start, end, same)
        if o:
            raise Conflict(f"overlaps existing {side} segment {o['label']} [{o['start_frame']}, {o['end_frame']})",
                           {"overlapping_segment_id": o["segment_id"]})

    def _log(self, segment_id, op, actor):
        snap = self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (segment_id,))
        self.db.insert("annotation_log", {"segment_id": segment_id, "op": op, "snapshot_json": json.dumps(snap),
                                          "actor": actor, "at": now_iso()})

    def create_annotation(self, touch_id, side, start_frame, end_frame, label, annotator_id,
                          label_source=None, ambiguity_flag=False, derived_from=None, segment_id=None,
                          label_version="v1", replace_overlaps=False, _op="create") -> dict:
        if not annotator_id:
            raise IntervalError("annotator_id is required")
        touch = self.get_touch(touch_id)
        self.ensure_unlocked(touch["video_id"])
        label = self.tax.canon(label)
        with self.db.transaction():
            if replace_overlaps:
                for a in self.annotations(touch_id):
                    if a["side"] == side and start_frame < a["end_frame"] and a["start_frame"] < end_frame:
                        self.db.update("action_segments", "segment_id", a["segment_id"],
                                       {"deleted": 1, "updated_at": now_iso()})
                        self._log(a["segment_id"], "delete", annotator_id)
            self._check_annotation(touch, side, start_frame, end_frame, label)
            src = label_source if _op == "import" else self._resolve_label_source(touch_id, annotator_id, label_source)
            sid = segment_id or new_id("seg")
            self.db.insert("action_segments", {
                "segment_id": sid, "kind": "annotation", "touch_id": touch_id, "side": side,
                "start_frame": start_frame, "end_frame": end_frame, "label": label, "label_source": src,
                "annotator_id": annotator_id, "ambiguity_flag": int(bool(ambiguity_flag)),
                "label_version": label_version if label_version != "v1" else self.label_version(),
                "derived_from_segment_id": derived_from,
                "created_at": now_iso(), "updated_at": now_iso()})
            self._log(sid, _op, annotator_id)
        return self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (sid,))

    def label_version(self) -> str:
        return "v1" if self.tax.preset == "mvp4" else self.tax.preset

    def update_annotation(self, segment_id, changes: dict, annotator_id) -> dict:
        a = self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (segment_id,))
        if not a or a["kind"] != "annotation" or a["deleted"]:
            raise NotFound(f"annotation {segment_id}")
        touch = self.get_touch(a["touch_id"])
        self.ensure_unlocked(touch["video_id"])
        new = {k: changes.get(k, a[k]) for k in ("side", "start_frame", "end_frame", "label")}
        new["label"] = self.tax.canon(new["label"])
        with self.db.transaction():
            self._check_annotation(touch, new["side"], new["start_frame"], new["end_frame"], new["label"],
                                   exclude_id=segment_id)
            upd = dict(new)
            if "ambiguity_flag" in changes:
                upd["ambiguity_flag"] = int(bool(changes["ambiguity_flag"]))
            # editing after seeing predictions downgrades provenance
            if a["label_source"] in ("blind", "adjudicated") and self.has_viewed(a["touch_id"], annotator_id):
                upd["label_source"] = "model_assisted"
            upd["updated_at"] = now_iso()
            upd["annotator_id"] = annotator_id
            self.db.update("action_segments", "segment_id", segment_id, upd)
            self._log(segment_id, "update", annotator_id)
        return self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (segment_id,))

    def delete_annotation(self, segment_id, annotator_id):
        a = self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (segment_id,))
        if not a or a["kind"] != "annotation":
            raise NotFound(f"annotation {segment_id}")
        self.ensure_unlocked(self.get_touch(a["touch_id"])["video_id"])
        self.db.update("action_segments", "segment_id", segment_id, {"deleted": 1, "updated_at": now_iso()})
        self._log(segment_id, "delete", annotator_id)

    # ------------------------------------------------------------------ reviews
    def review_prediction(self, prediction_segment_id, action, reviewer, corrected_label=None,
                          corrected_start=None, corrected_end=None, error_reason=None, comment=None,
                          replace_overlaps=False) -> dict:
        p = self.db.one("SELECT * FROM action_segments WHERE segment_id=?", (prediction_segment_id,))
        if not p or p["kind"] != "prediction":
            raise NotFound(f"prediction {prediction_segment_id}")
        if action not in tx.REVIEW_ACTIONS:
            raise IntervalError(f"action must be one of {tx.REVIEW_ACTIONS}")
        if not reviewer:
            raise IntervalError("reviewer is required")
        if error_reason and error_reason not in tx.ERROR_REASONS:
            raise IntervalError(f"error_reason must be one of {tx.ERROR_REASONS}")
        if action == "confirm" and p["no_call"]:
            raise IntervalError("a no_call prediction has no label to confirm; use correct / unknown / unobservable")
        # reviewing means the reviewer has seen the prediction
        self.mark_predictions_viewed(p["touch_id"], reviewer)
        start = p["start_frame"] if corrected_start is None else corrected_start
        end = p["end_frame"] if corrected_end is None else corrected_end
        self.ensure_unlocked(self.get_touch(p["touch_id"])["video_id"])
        label = {"confirm": self.tax.canon(p["label"]), "correct": corrected_label, "unknown": tx.UNKNOWN,
                 "unobservable": tx.UNOBSERVABLE, "reject": None}[action]
        if action == "correct" and not corrected_label:
            raise IntervalError("correct requires corrected_label")
        ann = None
        if label is not None:
            ann = self.create_annotation(p["touch_id"], p["side"], start, end, label, reviewer,
                                         label_source="model_assisted", derived_from=prediction_segment_id,
                                         replace_overlaps=replace_overlaps)
        rid = new_id("rev")
        self.db.insert("review_records", {
            "review_id": rid, "prediction_segment_id": prediction_segment_id, "touch_id": p["touch_id"],
            "side": p["side"], "action": action, "corrected_label": label if action != "confirm" else None,
            "corrected_start_frame": start if (action != "confirm" and label) else None,
            "corrected_end_frame": end if (action != "confirm" and label) else None,
            "error_reason": error_reason, "comment": comment, "reviewer": reviewer, "reviewed_at": now_iso(),
            "label_source": "model_assisted", "resulting_annotation_id": ann["segment_id"] if ann else None})
        return self.db.one("SELECT * FROM review_records WHERE review_id=?", (rid,))

    def reviews(self, touch_id=None):
        if touch_id:
            return self.db.q("SELECT * FROM review_records WHERE touch_id=? ORDER BY reviewed_at", (touch_id,))
        return self.db.q("SELECT * FROM review_records ORDER BY reviewed_at")

    # ------------------------------------------------------------------ events
    def add_event(self, touch_id, side, event_type, frame, source, prob=None, run_id=None, note=None):
        t = self.get_touch(touch_id)
        if event_type not in tx.EVENT_TYPES:
            raise IntervalError(f"event_type must be one of {tx.EVENT_TYPES}")
        if source not in tx.EVENT_SOURCES:
            raise IntervalError(f"source must be one of {tx.EVENT_SOURCES}")
        if source != "model" and prob is not None:
            raise IntervalError("only model events may carry a probability")
        determinable = frame is not None
        if determinable and not t["start_frame"] <= frame < t["end_frame"]:
            raise IntervalError("event frame outside touch window")
        eid = new_id("evt")
        self.db.insert("events", {
            "event_id": eid, "touch_id": touch_id, "side": side, "event_type": event_type,
            "event_frame": frame, "event_time_ms": self.rel_ms(t, frame) if determinable else None,
            "event_prob": prob, "source": source, "run_id": run_id, "determinable": int(determinable),
            "note": note, "created_at": now_iso()})
        return self.db.one("SELECT * FROM events WHERE event_id=?", (eid,))

    def events(self, touch_id, run_id=None, sources=None):
        rows = self.db.q("SELECT * FROM events WHERE touch_id=? ORDER BY event_frame", (touch_id,))
        if sources:
            rows = [r for r in rows if r["source"] in sources]
        if run_id:
            rows = [r for r in rows if r["run_id"] in (run_id, None)]
        return rows

    def delete_event(self, event_id):
        self.db.x("DELETE FROM events WHERE event_id=? AND source='expert'", (event_id,))
