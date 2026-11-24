"""Team mode (v0.5.0): the app runs on one computer and the team signs in to it from anywhere.

Switched on with the environment variable ENGARDE_TEAM=1 (serve_team.bat sets it). Without it the app
behaves exactly as before: no sign-in, one person, one computer.

Roles
    admin   everything, plus: add / remove people, delete videos, unlock anything, approve unlock requests
    member  upload, analyse, label, review, lock, train
    viewer  look only
Passwords are stored as salted PBKDF2-SHA256 hashes; session tokens are stored hashed too.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from .db import new_id, now_iso
from .intervals import IntervalError

ROLES = ["admin", "member", "viewer"]
COOKIE = "engarde_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 310_000
MIN_PASSWORD = 8


def team_mode() -> bool:
    return os.environ.get("ENGARDE_TEAM", "").strip().lower() not in ("", "0", "false", "no")


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS).hex()


def _public(u: dict | None) -> dict | None:
    return None if not u else {k: u[k] for k in ("user_id", "name", "role", "active", "created_at", "last_login_at")}


def _check_name(name) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 40:
        raise IntervalError("a name of 1 to 40 characters is required")
    return name


def _check_password(password) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise IntervalError(f"the password needs at least {MIN_PASSWORD} characters")
    return password


def list_users(db) -> list[dict]:
    return [_public(u) for u in db.q("SELECT * FROM users ORDER BY created_at, rowid")]


def has_admin(db) -> bool:
    return db.one("SELECT 1 x FROM users WHERE role='admin' AND active=1") is not None


def create_user(db, name, password, role="member") -> dict:
    name, password = _check_name(name), _check_password(password)
    if role not in ROLES:
        raise IntervalError(f"role must be one of {ROLES}")
    if db.one("SELECT 1 x FROM users WHERE name=? COLLATE NOCASE", (name,)):
        raise IntervalError(f"there is already an account called {name}")
    salt = secrets.token_hex(16)
    uid = new_id("usr")
    db.insert("users", {"user_id": uid, "name": name, "role": role, "salt": salt, "pw_hash": _hash(password, salt),
                        "active": 1, "created_at": now_iso()})
    return _public(db.one("SELECT * FROM users WHERE user_id=?", (uid,)))


def _admins_left_without(db, user_id) -> int:
    return db.one("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND user_id != ?", (user_id,))["n"]


def update_user(db, user_id, role=None, active=None, password=None) -> dict:
    u = db.one("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not u:
        raise IntervalError("no such account")
    ch = {}
    if role is not None:
        if role not in ROLES:
            raise IntervalError(f"role must be one of {ROLES}")
        ch["role"] = role
    if active is not None:
        ch["active"] = int(bool(active))
    losing_admin = u["role"] == "admin" and u["active"] and (ch.get("role", "admin") != "admin" or ch.get("active", 1) == 0)
    if losing_admin and _admins_left_without(db, user_id) == 0:
        raise IntervalError("this is the only admin: make someone else an admin first")
    if password is not None:
        salt = secrets.token_hex(16)
        ch.update(salt=salt, pw_hash=_hash(_check_password(password), salt))
    db.update("users", "user_id", user_id, ch)
    if password is not None or ch.get("active") == 0:
        db.x("DELETE FROM sessions WHERE user_id=?", (user_id,))      # signed out everywhere
    return _public(db.one("SELECT * FROM users WHERE user_id=?", (user_id,)))


def verify(db, name, password) -> dict | None:
    u = db.one("SELECT * FROM users WHERE name=? COLLATE NOCASE", ((name or "").strip(),))
    # hash even when the name is unknown, so the reply takes the same time either way
    good = hmac.compare_digest(_hash(password or "", u["salt"] if u else "00" * 16), u["pw_hash"] if u else "")
    return u if (u and good and u["active"]) else None


def new_session(db, user_id) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    db.insert("sessions", {"token_hash": hashlib.sha256(token.encode()).hexdigest(), "user_id": user_id,
                           "created_at": now_iso(), "expires_at": exp})
    db.update("users", "user_id", user_id, {"last_login_at": now_iso()})
    db.x("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    return token


def user_for_token(db, token) -> dict | None:
    if not token:
        return None
    r = db.one("SELECT u.* FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE s.token_hash=? AND s.expires_at > ? "
               "AND u.active=1", (hashlib.sha256(token.encode()).hexdigest(), now_iso()))
    return _public(r)


def end_session(db, token):
    if token:
        db.x("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


class Throttle:
    """Five wrong passwords for a name within ten minutes -> that name waits one minute."""

    def __init__(self, tries=5, window_s=600, wait_s=60):
        self.tries, self.window_s, self.wait_s = tries, window_s, wait_s
        self.fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> int:
        now = time.time()
        f = [t for t in self.fails.get(key, []) if now - t < self.window_s]
        self.fails[key] = f
        if len(f) >= self.tries and now - f[-1] < self.wait_s:
            return int(self.wait_s - (now - f[-1])) + 1
        return 0

    def fail(self, key: str):
        self.fails.setdefault(key, []).append(time.time())

    def ok(self, key: str):
        self.fails.pop(key, None)
