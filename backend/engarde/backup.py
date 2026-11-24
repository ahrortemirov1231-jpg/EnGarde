"""Backups for the team server (v0.5.0). Everything the team makes lives on one computer, so the
database and a re-importable labels file are copied on a schedule:

    data/backups/engarde_YYYY-MM-DD.sqlite    whole database (labels, touches, reviews, accounts)
    data/backups/labels_YYYY-MM-DD.json       labels bundle that Import labels can read

The newest 14 of each are kept. If data/team.json has {"backup_dir": "<folder>"} (for example a folder
that Google Drive for desktop syncs to the NYU Drive), both files are copied there as well, so a dead
disk does not take the team's work with it. Videos are not backed up: they are large and re-uploadable."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime

KEEP = 14
_state = {"last": None, "error": None}


def backup_dir_setting(store) -> str | None:
    p = os.path.join(store.data_dir, "team.json")
    env = os.environ.get("ENGARDE_BACKUP_DIR")
    if env:
        return env
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("backup_dir") or None
        except Exception:  # noqa: BLE001
            return None
    return None


def _prune(folder, prefix, suffix):
    files = sorted(f for f in os.listdir(folder) if f.startswith(prefix) and f.endswith(suffix))
    for f in files[:-KEEP]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass


def run_backup(store) -> dict:
    from . import exports
    d = os.path.join(store.data_dir, "backups")
    os.makedirs(d, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    db_dst = os.path.join(d, f"engarde_{day}.sqlite")
    tmp = db_dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    with store.db._lock:                         # a consistent copy even while people are labelling
        dst = sqlite3.connect(tmp)
        store.db.conn.backup(dst)
        dst.close()
    os.replace(tmp, db_dst)
    lab_dst = os.path.join(d, f"labels_{day}.json")
    bundle = exports.export_bundle(store)
    with open(lab_dst + ".tmp", "w", encoding="utf-8") as f:
        json.dump(bundle, f, default=str)
    os.replace(lab_dst + ".tmp", lab_dst)
    _prune(d, "engarde_", ".sqlite")
    _prune(d, "labels_", ".json")
    out = {"at": datetime.now().isoformat(timespec="seconds"), "database": db_dst, "labels": lab_dst,
           "videos_in_labels_file": len(bundle["videos"]), "copied_to": None, "copy_error": None}
    extra = backup_dir_setting(store)
    if extra:
        try:
            os.makedirs(extra, exist_ok=True)
            for p in (db_dst, lab_dst):
                shutil.copy2(p, os.path.join(extra, os.path.basename(p)))
            _prune(extra, "engarde_", ".sqlite")
            _prune(extra, "labels_", ".json")
            out["copied_to"] = extra
        except Exception as e:  # noqa: BLE001 - the local backup still counts
            out["copy_error"] = f"{type(e).__name__}: {e}"
    _state.update(last=out, error=None)
    return out


def status() -> dict:
    return dict(_state)


def start_schedule(store, every_hours=6.0, first_after_s=60.0):
    def loop():
        time.sleep(first_after_s)
        while True:
            try:
                run_backup(store)
            except Exception as e:  # noqa: BLE001
                _state["error"] = f"{type(e).__name__}: {e}"
            time.sleep(every_hours * 3600)
    t = threading.Thread(target=loop, daemon=True, name="engarde-backup")
    t.start()
    return t


def keep_awake():
    """Windows: stop the PC from going to sleep while the team server is running (the screen may still
    turn off). Ends by itself when the app stops; no Windows setting is changed."""
    if os.name != "nt":
        return False

    def hold():
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        while True:
            time.sleep(3600)
    threading.Thread(target=hold, daemon=True, name="engarde-keep-awake").start()
    return True
