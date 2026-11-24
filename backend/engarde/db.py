"""SQLite storage. One file, created on first use. Predictions and review records are append-only
(enforced by triggers, not just by convention)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    path TEXT NOT NULL,            -- absolute path of the stored original
    sha256 TEXT,
    width INTEGER, height INTEGER,
    n_frames INTEGER,              -- decoded frame count (from the frame index)
    duration_s REAL,
    fps_nominal REAL,              -- container r_frame_rate
    fps_measured REAL,             -- (n_frames-1)/(t_last-t_first)
    vfr INTEGER DEFAULT 0,         -- 1 when frame intervals vary > 1%
    codec TEXT,
    capture_type TEXT DEFAULT 'unknown',   -- fixed | broadcast_panning | unknown
    weapon_type TEXT DEFAULT 'unknown',
    is_synthetic INTEGER DEFAULT 0,
    calibration_json TEXT,         -- piste homography; NULL = uncalibrated (no metric distances)
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS bouts (
    bout_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    name TEXT,
    created_at TEXT
);

-- Athlete identity is separate from the fixed side label and from per-frame screen position.
CREATE TABLE IF NOT EXISTS fencers (
    fencer_id TEXT PRIMARY KEY,
    bout_id TEXT NOT NULL REFERENCES bouts(bout_id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('left','right')),  -- fixed label: screen side at bout start
    athlete_id TEXT,               -- stable athlete identity (name / federation id); may be NULL
    display_name TEXT,
    UNIQUE (bout_id, side)
);

CREATE TABLE IF NOT EXISTS touches (
    touch_id TEXT PRIMARY KEY,
    bout_id TEXT NOT NULL REFERENCES bouts(bout_id) ON DELETE CASCADE,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    seq INTEGER,
    light_frame INTEGER NOT NULL,
    light_time_s REAL NOT NULL,
    lamp_left INTEGER, lamp_right INTEGER,      -- NULL = unknown
    light_source TEXT DEFAULT 'manual',         -- manual | detector
    start_frame INTEGER NOT NULL,               -- analysis window [start_frame, end_frame)
    end_frame INTEGER NOT NULL,
    window_clipped INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    run_id TEXT PRIMARY KEY,
    touch_id TEXT NOT NULL REFERENCES touches(touch_id) ON DELETE CASCADE,
    method TEXT NOT NULL,          -- rule_baseline | xgboost
    status_label TEXT,             -- e.g. 'Rule baseline / unvalidated'
    model_version TEXT,
    dataset_version TEXT,
    config_json TEXT,
    versions_json TEXT,
    stage_status_json TEXT,
    calibrated INTEGER DEFAULT 0,
    created_at TEXT
);

-- kind='prediction' rows are immutable; kind='annotation' rows are the current human labels.
CREATE TABLE IF NOT EXISTS action_segments (
    segment_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('prediction','annotation')),
    touch_id TEXT NOT NULL REFERENCES touches(touch_id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('left','right')),
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    label TEXT NOT NULL,
    -- prediction fields
    run_id TEXT REFERENCES prediction_runs(run_id) ON DELETE CASCADE,
    class_probs_json TEXT,         -- NULL for rules (no probabilities are invented)
    calibrated_prob REAL,          -- NULL unless a calibration was fitted
    top2_label TEXT,
    no_call INTEGER DEFAULT 0,
    no_call_reason TEXT,
    track_quality TEXT,
    pose_quality REAL,
    camera_motion_flag INTEGER,
    -- annotation fields
    label_source TEXT,
    annotator_id TEXT,
    ambiguity_flag INTEGER DEFAULT 0,
    label_version TEXT,
    derived_from_segment_id TEXT,  -- prediction this annotation was accepted/corrected from
    deleted INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    CHECK (end_frame > start_frame)
);
CREATE INDEX IF NOT EXISTS ix_seg_touch ON action_segments(touch_id, kind, side);

CREATE TRIGGER IF NOT EXISTS predictions_immutable_upd BEFORE UPDATE ON action_segments
WHEN OLD.kind = 'prediction'
BEGIN SELECT RAISE(ABORT, 'prediction segments are immutable'); END;

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    touch_id TEXT NOT NULL REFERENCES touches(touch_id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('left','right')),
    event_type TEXT NOT NULL,
    event_frame INTEGER,           -- NULL = not determinable
    event_time_ms REAL,            -- relative to the light; NULL when not determinable
    event_prob REAL,               -- NULL for rule / expert
    source TEXT NOT NULL CHECK (source IN ('model','rule','expert')),
    run_id TEXT,
    determinable INTEGER DEFAULT 1,
    note TEXT,
    created_at TEXT
);

-- Append-only coach review of predictions.
CREATE TABLE IF NOT EXISTS review_records (
    review_id TEXT PRIMARY KEY,
    prediction_segment_id TEXT NOT NULL REFERENCES action_segments(segment_id),
    touch_id TEXT NOT NULL,
    side TEXT NOT NULL,
    action TEXT NOT NULL,          -- confirm | correct | unknown | unobservable | reject
    corrected_label TEXT,
    corrected_start_frame INTEGER,
    corrected_end_frame INTEGER,
    error_reason TEXT,
    comment TEXT,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    label_source TEXT NOT NULL,    -- always model_assisted: reviewer saw the prediction
    resulting_annotation_id TEXT
);
CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON review_records
BEGIN SELECT RAISE(ABORT, 'review records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON review_records
BEGIN SELECT RAISE(ABORT, 'review records are append-only'); END;

-- Every annotation change is also appended here (full history, nothing lost).
CREATE TABLE IF NOT EXISTS annotation_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id TEXT NOT NULL,
    op TEXT NOT NULL,              -- create | update | delete | import
    snapshot_json TEXT NOT NULL,
    actor TEXT,
    at TEXT NOT NULL
);

-- Blind-labeling firewall: once an annotator has seen predictions for a touch,
-- everything they label there is model_assisted.
CREATE TABLE IF NOT EXISTS prediction_views (
    touch_id TEXT NOT NULL,
    annotator_id TEXT NOT NULL,
    first_viewed_at TEXT NOT NULL,
    PRIMARY KEY (touch_id, annotator_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    progress REAL DEFAULT 0,
    stage TEXT,
    message TEXT,
    params_json TEXT,
    result_json TEXT,
    created_at TEXT, started_at TEXT, finished_at TEXT
);

CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    classes_json TEXT,
    metrics_json TEXT,
    config_json TEXT,
    dataset_version TEXT,
    created_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class DB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Small, idempotent upgrades for data folders made by an older version."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(videos)").fetchall()}
        for name, ddl in (("split", "TEXT"),            # 'train' | 'test' | NULL (not decided)
                          ("locked", "INTEGER DEFAULT 0"), ("locked_by", "TEXT"), ("locked_at", "TEXT"),
                          ("sha256", "TEXT")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {ddl}")
        self.conn.execute("CREATE TABLE IF NOT EXISTS app_flags (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                role TEXT NOT NULL CHECK (role IN ('admin','member','viewer')),
                salt TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER DEFAULT 1,
                created_at TEXT, last_login_at TEXT);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT, expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS unlock_requests (
                request_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, requested_by TEXT NOT NULL, reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied')),
                decided_by TEXT, created_at TEXT, decided_at TEXT);
        """)
        self.conn.execute("CREATE TABLE IF NOT EXISTS audit_log (log_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, target TEXT, detail_json TEXT)")
        # v0.4.0: review records stay append-only, except when a whole video is deleted on purpose
        # (Store.delete_video sets the flag inside its transaction and clears it again).
        self.conn.executescript("""
            DROP TRIGGER IF EXISTS reviews_no_delete;
            CREATE TRIGGER reviews_no_delete BEFORE DELETE ON review_records
            WHEN COALESCE((SELECT value FROM app_flags WHERE key='deleting_video'), '0') != '1'
            BEGIN SELECT RAISE(ABORT, 'review records are append-only'); END;
            DROP TRIGGER IF EXISTS predictions_immutable_upd;
            CREATE TRIGGER predictions_immutable_upd BEFORE UPDATE ON action_segments
            WHEN OLD.kind = 'prediction'
            BEGIN SELECT RAISE(ABORT, 'prediction segments are immutable'); END;
        """)
        # v0.5.6: indexes for the lookups the Videos page makes. Without them the page slowed down
        # roughly with the square of the data, and at about 100 videos it took ~5 s per load.
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS ix_touches_video ON touches(video_id);
            CREATE INDEX IF NOT EXISTS ix_touches_bout ON touches(bout_id);
            CREATE INDEX IF NOT EXISTS ix_bouts_video ON bouts(video_id);
            CREATE INDEX IF NOT EXISTS ix_fencers_bout ON fencers(bout_id);
            CREATE INDEX IF NOT EXISTS ix_runs_touch ON prediction_runs(touch_id, created_at);
            CREATE INDEX IF NOT EXISTS ix_seg_run ON action_segments(run_id);
            CREATE INDEX IF NOT EXISTS ix_reviews_pred ON review_records(prediction_segment_id);
            CREATE INDEX IF NOT EXISTS ix_jobs_target ON jobs(target_id, created_at);
        """)

    def q(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql: str, params=()):
        with self._lock:
            return self.conn.execute(sql, params)

    def insert(self, table: str, row: dict):
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()]
        self.x(f"INSERT INTO {table} ({cols}) VALUES ({qs})", vals)

    def update(self, table: str, key: str, key_val, changes: dict):
        if not changes:
            return
        sets = ",".join(f"{k}=?" for k in changes)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in changes.values()]
        self.x(f"UPDATE {table} SET {sets} WHERE {key}=?", vals + [key_val])

    def transaction(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: DB):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db.conn.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
        return False
