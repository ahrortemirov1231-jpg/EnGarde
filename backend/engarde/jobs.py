"""Minimal background job runner: one worker thread, state persisted in the jobs table
(queued -> running -> completed | failed). Jobs interrupted by a server restart are marked failed."""
from __future__ import annotations

import json
import queue
import threading
import traceback

from .db import new_id, now_iso


LONG_JOBS = {"auto_analyze", "train_xgboost", "train_tcn", "bulk_import_folder", "bulk_import_links"}


class JobRunner:
    """Two lanes: long jobs (whole-video analysis, training) never block short ones (one touch)."""

    def __init__(self, store):
        self.store = store
        self.db = store.db
        self.handlers = {}
        self.q: queue.Queue[str] = queue.Queue()
        self.q_long: queue.Queue[str] = queue.Queue()
        self.db.x("UPDATE jobs SET status='failed', message='interrupted by server restart', finished_at=? "
                  "WHERE status IN ('queued','running')", (now_iso(),))
        self._t = threading.Thread(target=self._loop, args=(self.q,), daemon=True)
        self._t.start()
        self._t2 = threading.Thread(target=self._loop, args=(self.q_long,), daemon=True)
        self._t2.start()

    def register(self, kind, fn):
        self.handlers[kind] = fn

    def submit(self, kind, target_id=None, **params) -> dict:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind {kind}")
        jid = new_id("job")
        self.db.insert("jobs", {"job_id": jid, "kind": kind, "target_id": target_id, "status": "queued",
                                "params_json": json.dumps(params), "created_at": now_iso()})
        (self.q_long if kind in LONG_JOBS else self.q).put(jid)
        return self.get(jid)

    def get(self, job_id):
        j = self.db.one("SELECT * FROM jobs WHERE job_id=?", (job_id,))
        if j and j.get("result_json"):
            j["result"] = json.loads(j["result_json"])
        return j

    def list(self, target_id=None, limit=50):
        if target_id:
            return self.db.q("SELECT * FROM jobs WHERE target_id=? ORDER BY created_at DESC LIMIT ?", (target_id, limit))
        return self.db.q("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))

    def wait_idle(self, timeout=None):
        """For CLI/tests: block until the queue is drained."""
        self.q.join()
        self.q_long.join()

    def _progress(self, jid):
        def report(progress: float, stage: str = None, message: str = None):
            ch = {"progress": round(float(progress), 3)}
            if stage is not None:
                ch["stage"] = stage
            if message is not None:
                ch["message"] = message
            self.db.update("jobs", "job_id", jid, ch)
        return report

    def _loop(self, q):
        while True:
            jid = q.get()
            try:
                j = self.get(jid)
                self.db.update("jobs", "job_id", jid, {"status": "running", "started_at": now_iso()})
                params = json.loads(j["params_json"] or "{}")
                result = self.handlers[j["kind"]](j["target_id"], report=self._progress(jid), **params)
                self.db.update("jobs", "job_id", jid, {"status": "completed", "progress": 1.0,
                                                       "result_json": json.dumps(result, default=str),
                                                       "finished_at": now_iso()})
            except Exception as e:  # noqa: BLE001 — failure is recorded, never hidden
                self.db.update("jobs", "job_id", jid, {
                    "status": "failed", "message": f"{type(e).__name__}: {e}",
                    "result_json": json.dumps({"traceback": traceback.format_exc()[-4000:]}),
                    "finished_at": now_iso()})
            finally:
                q.task_done()
