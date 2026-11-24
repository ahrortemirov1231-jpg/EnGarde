"""v0.5.0 team mode: sign-in, roles, the signed-in name on every action, unlock requests, backups.
SYNTHETIC fixture only."""
import importlib
import json
import os
import shutil

import pytest


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    d = tmp_path_factory.mktemp("teamdata")
    old = {k: os.environ.get(k) for k in ("ENGARDE_DATA", "ENGARDE_TEAM")}
    os.environ["ENGARDE_DATA"], os.environ["ENGARDE_TEAM"] = str(d), "1"
    from fastapi.testclient import TestClient
    import engarde.api as api
    importlib.reload(api)
    from engarde import auth
    from engarde.fixture import make_fixture
    from engarde.pipeline import runner
    auth.create_user(api.store.db, "Ahror", "correct horse 1", "admin")
    auth.create_user(api.store.db, "Mrunal", "correct horse 2", "member")
    auth.create_user(api.store.db, "Guest", "correct horse 3", "viewer")
    fx = make_fixture(str(d / "fx"))
    s = api.store
    v = s.add_video(fx["video"], "SYNTHETIC", is_synthetic=True)
    s.index_video(v["video_id"])
    shutil.copy(fx["skeleton"], os.path.join(s.video_dir(v["video_id"]), "imported_skeleton.json"))
    b = s.add_bout(v["video_id"], "bout")
    t = s.add_touch(b["bout_id"], 150, 1, 0)
    runner.analyze_touch(s, t["touch_id"])

    def client(name=None, pw=None):
        c = TestClient(api.app)
        if name:
            r = c.post("/api/auth/login", json={"name": name, "password": pw})
            assert r.status_code == 200, r.text
        return c
    yield {"api": api, "client": client, "video": v, "touch": t, "dir": d}
    for k, val in old.items():
        if val is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = val
    importlib.reload(api)


def test_nothing_without_signing_in(team):
    c = team["client"]()
    assert c.get("/api/auth/me").json() == {"team_mode": True, "user": None, "version": team["api"].VERSION,
                                             "needs_first_admin": False}
    for path in ("/api/videos", "/api/touches", "/api/dashboard", "/api/export/bundle.json",
                 f"/api/videos/{team['video']['video_id']}/stream", "/api/team/users", "/api/about"):
        assert c.get(path).status_code == 401, path
    assert c.post(f"/api/touches/{team['touch']['touch_id']}/annotations", json={}).status_code == 401
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert c.post("/api/auth/login", json={"name": "Ahror", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"name": "nobody", "password": "x"}).status_code == 401


def test_sign_in_and_out(team):
    c = team["client"]("ahror", "correct horse 1")              # the name is not case sensitive
    me = c.get("/api/auth/me").json()["user"]
    assert me["name"] == "Ahror" and me["role"] == "admin" and "pw_hash" not in me and "salt" not in me
    assert c.get("/api/videos").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/videos").status_code == 401


def test_the_signed_in_name_is_used_whatever_the_page_sends(team):
    c = team["client"]("Mrunal", "correct horse 2")
    t = team["touch"]
    r = c.post(f"/api/touches/{t['touch_id']}/annotations",
               json={"side": "left", "start_frame": 60, "end_frame": 80, "label": "Lunge", "annotator_id": "Tim Morehouse",
                     "label_source": "blind"})
    assert r.status_code == 200 and r.json()["annotator_id"] == "Mrunal" and r.json()["label_source"] == "blind"
    # the blind-label firewall follows the account: once Mrunal has seen predictions her labels are model_assisted
    assert c.get(f"/api/touches/{t['touch_id']}/predictions?annotator=someone_else").json()["hidden"] is False
    r = c.post(f"/api/touches/{t['touch_id']}/annotations",
               json={"side": "right", "start_frame": 60, "end_frame": 80, "label": "Lunge", "annotator_id": "x", "label_source": "blind"})
    assert r.json()["annotator_id"] == "Mrunal" and r.json()["label_source"] == "model_assisted"


def test_roles(team):
    vid = team["video"]["video_id"]
    guest, member, admin = team["client"]("Guest", "correct horse 3"), team["client"]("Mrunal", "correct horse 2"), \
        team["client"]("Ahror", "correct horse 1")
    assert guest.get("/api/videos").status_code == 200
    assert guest.patch(f"/api/videos/{vid}", json={"split": "test"}).status_code == 403          # viewers only look
    assert member.patch(f"/api/videos/{vid}", json={"split": "train"}).status_code == 200
    assert member.delete(f"/api/videos/{vid}?actor=x").status_code == 403                        # only admins delete
    assert member.get("/api/team/users").status_code == 403 and member.get("/api/audit").status_code == 403
    assert member.post("/api/team/users", json={"name": "Eve", "password": "long enough 1", "role": "admin"}).status_code == 403
    assert member.post("/api/videos/from_path", json={"path": "C:/Windows/win.ini"}).status_code == 403
    assert [u["name"] for u in admin.get("/api/team/users").json()] == ["Ahror", "Mrunal", "Guest"]
    r = admin.post("/api/team/users", json={"name": "Alaia", "password": "short"})
    assert r.status_code == 422 and "at least" in r.json()["error"]
    r = admin.post("/api/team/users", json={"name": "Alaia", "password": "long enough 1"})
    assert r.status_code == 200 and r.json()["role"] == "member"
    assert admin.post("/api/team/users", json={"name": "alaia", "password": "long enough 1"}).status_code == 422   # duplicate
    me = [u for u in admin.get("/api/team/users").json() if u["name"] == "Ahror"][0]
    assert admin.patch(f"/api/team/users/{me['user_id']}", json={"role": "member"}).status_code == 422   # last admin stays
    assert admin.patch(f"/api/team/users/{me['user_id']}", json={"active": False}).status_code == 422
    # switching an account off signs that person out at once
    al = team["client"]("Alaia", "long enough 1")
    aid = [u for u in admin.get("/api/team/users").json() if u["name"] == "Alaia"][0]["user_id"]
    assert admin.patch(f"/api/team/users/{aid}", json={"active": False}).status_code == 200
    assert al.get("/api/videos").status_code == 401
    assert al.post("/api/auth/login", json={"name": "Alaia", "password": "long enough 1"}).status_code == 401


def test_lock_and_ask_to_unlock(team):
    vid = team["video"]["video_id"]
    member, admin = team["client"]("Mrunal", "correct horse 2"), team["client"]("Ahror", "correct horse 1")
    other = admin.post("/api/team/users", json={"name": "Jayant", "password": "long enough 2"}).json()
    jay = team["client"]("Jayant", "long enough 2")
    assert member.patch(f"/api/videos/{vid}", json={"locked": True, "actor": "spoof"}).json()["locked_by"] == "Mrunal"
    r = jay.patch(f"/api/videos/{vid}", json={"locked": False})
    assert r.status_code == 423 and "Ask to unlock" in r.json()["error"]
    assert jay.post(f"/api/touches/{team['touch']['touch_id']}/annotations",
                    json={"side": "left", "start_frame": 90, "end_frame": 95, "label": "Lunge"}).status_code == 423
    req = jay.post(f"/api/videos/{vid}/unlock_request", json={"reason": "touch 1 has the wrong light frame"}).json()
    assert req["status"] == "pending" and req["requested_by"] == "Jayant"
    assert jay.post(f"/api/videos/{vid}/unlock_request", json={}).json()["request_id"] == req["request_id"]   # asked once
    assert jay.post(f"/api/team/unlock_requests/{req['request_id']}", json={"approve": True}).status_code == 403
    pend = admin.get("/api/team/unlock_requests").json()
    assert len(pend) == 1 and pend[0]["video_title"] == "SYNTHETIC" and pend[0]["reason"].startswith("touch 1")
    assert member.get("/api/team/unlock_requests").json() == []                     # members see only their own
    assert admin.post(f"/api/team/unlock_requests/{req['request_id']}", json={"approve": True}).json()["status"] == "approved"
    assert admin.get(f"/api/videos/{vid}").json()["locked"] == 0 and admin.get("/api/team/unlock_requests").json() == []
    acts = [a["action"] for a in admin.get("/api/audit").json()]
    assert {"lock", "unlock_request", "unlock", "unlock_approved", "user_added"} <= set(acts)
    assert other["name"] == "Jayant"


def test_change_password_and_throttle(team):
    c = team["client"]("Mrunal", "correct horse 2")
    second_device = team["client"]("Mrunal", "correct horse 2")
    assert c.post("/api/auth/password", json={"old": "nope", "new": "a new long one"}).status_code == 403
    assert c.post("/api/auth/password", json={"old": "correct horse 2", "new": "a new long one"}).status_code == 200
    assert c.get("/api/videos").status_code == 200 and second_device.get("/api/videos").status_code == 401
    x = team["client"]()
    codes = [x.post("/api/auth/login", json={"name": "Guest", "password": "bad"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert x.post("/api/auth/login", json={"name": "Guest", "password": "correct horse 3"}).status_code == 429


def test_backup_files(team):
    admin = team["client"]("Ahror", "correct horse 1")
    extra = team["dir"] / "drive"
    with open(team["dir"] / "team.json", "w") as f:
        json.dump({"backup_dir": str(extra)}, f)
    r = admin.post("/api/team/backup").json()
    assert os.path.getsize(r["database"]) > 0 and r["copied_to"] == str(extra) and r["copy_error"] is None
    assert json.load(open(r["labels"]))["format"] == "engarde-bundle/1"
    assert sorted(os.listdir(extra)) == sorted(os.path.basename(r[k]) for k in ("database", "labels"))
    about = admin.get("/api/about").json()
    assert about["version"] == team["api"].VERSION and about["backup"]["last"]["at"] and "## v" in about["changelog"]


def test_single_computer_mode_has_no_sign_in(tmp_path):
    old = {k: os.environ.get(k) for k in ("ENGARDE_DATA", "ENGARDE_TEAM")}
    os.environ["ENGARDE_DATA"] = str(tmp_path / "solo")
    os.environ.pop("ENGARDE_TEAM", None)
    from fastapi.testclient import TestClient
    import engarde.api as api
    importlib.reload(api)
    try:
        c = TestClient(api.app)
        assert c.get("/api/auth/me").json()["team_mode"] is False
        assert c.get("/api/videos").status_code == 200 and c.get("/api/about").status_code == 200
        assert c.post("/api/auth/login", json={"name": "a", "password": "b"}).status_code == 404
        assert c.get("/api/team/users").status_code == 404
    finally:
        for k, val in old.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
