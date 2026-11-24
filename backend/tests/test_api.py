"""HTTP-level checks: upload -> background jobs (queued/running/completed) -> touch -> analyze ->
predictions (blind mode hides them) -> review -> exports. SYNTHETIC fixture only."""
import importlib
import os
import shutil
import time

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("apidata")
    os.environ["ENGARDE_DATA"] = str(d)
    from fastapi.testclient import TestClient
    import engarde.api as api
    importlib.reload(api)
    return TestClient(api.app), api


def _wait(c, job_id, timeout=120):
    t0 = time.time()
    seen = set()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        seen.add(j["status"])
        if j["status"] in ("completed", "failed"):
            return j, seen
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_end_to_end_over_http(client, tmp_path):
    c, api = client
    from engarde.fixture import make_fixture
    fx = make_fixture(str(tmp_path / "fx"))
    with open(fx["video"], "rb") as f:
        r = c.post("/api/videos", files={"file": ("fixture.mp4", f, "video/mp4")}, data={"title": "SYNTHETIC"})
    assert r.status_code == 200
    vid = r.json()["video"]["video_id"]
    j, seen = _wait(c, r.json()["job"]["job_id"])
    assert j["status"] == "completed", j
    shutil.copy(fx["skeleton"], os.path.join(api.store.video_dir(vid), "imported_skeleton.json"))
    b = c.post(f"/api/videos/{vid}/bouts", json={"name": "b", "left": {"athlete_id": "L"}, "right": {"athlete_id": "R"}}).json()
    r = c.post(f"/api/bouts/{b['bout_id']}/touches", json={"light_frame": 150, "lamp_left": True, "lamp_right": False})
    tid = r.json()["touch"]["touch_id"]
    _wait(c, r.json()["job"]["job_id"])
    assert c.get(f"/api/touches/{tid}/frames/150.jpg").status_code == 200
    assert c.get(f"/api/touches/{tid}/frames/10.jpg").status_code == 404      # outside window: no fake exact frame
    j, _ = _wait(c, c.post(f"/api/touches/{tid}/analyze", json={}).json()["job_id"])
    assert j["status"] == "completed", j.get("message")
    # blind mode hides predictions entirely and does not mark them viewed
    hidden = c.get(f"/api/touches/{tid}/predictions", params={"annotator": "blindA", "blind": True}).json()
    assert hidden["hidden"] and "predictions" not in hidden
    a = c.post(f"/api/touches/{tid}/annotations", json={"side": "left", "start_frame": 60, "end_frame": 90,
                                                        "label": "Other/Still", "annotator_id": "blindA", "label_source": "blind"}).json()
    assert a["label_source"] == "blind"
    vis = c.get(f"/api/touches/{tid}/predictions", params={"annotator": "blindA"}).json()
    assert vis["run"]["status_label"] == "Rule baseline / unvalidated" and vis["predictions"]
    a2 = c.post(f"/api/touches/{tid}/annotations", json={"side": "left", "start_frame": 90, "end_frame": 100,
                                                         "label": "Advance", "annotator_id": "blindA", "label_source": "blind"}).json()
    assert a2["label_source"] == "model_assisted"
    # validation errors are 422 / 409, not 500
    assert c.post(f"/api/touches/{tid}/annotations", json={"side": "left", "start_frame": 95, "end_frame": 99,
                                                           "label": "Lunge", "annotator_id": "x"}).status_code == 409
    assert c.post(f"/api/touches/{tid}/annotations", json={"side": "left", "start_frame": 30, "end_frame": 40,
                                                           "label": "Lunge", "annotator_id": "x"}).status_code == 422
    p = [x for x in vis["predictions"] if not x["no_call"]][0]
    rv = c.post(f"/api/predictions/{p['segment_id']}/review", json={"action": "unobservable", "reviewer": "coach",
                                                                    "replace_overlaps": True}).json()
    assert rv["action"] == "unobservable" and rv["corrected_label"] == "Unobservable"
    for what in ("predictions", "annotations", "reviews", "review", "events"):
        r = c.get(f"/api/export/{what}.csv", params={"video_id": vid})
        assert r.status_code == 200 and r.text.count("\n") >= 2, what
    assert c.get("/api/export/runs.json").json()[0]["versions"]["python"]
    airs = c.get(f"/api/airs/{tid}", params={"fmt": "txt"}).text
    assert "not determinable" in airs and "attack" not in airs.lower()
    ins = c.get("/api/insights").json()
    assert ins["metrics"]["launch_distance_m"]["available"] is False


def test_upload_runs_full_analysis_automatically(client, tmp_path):
    c, api = client
    from engarde.fixture import make_fixture
    fx = make_fixture(str(tmp_path / "fx2"))
    with open(fx["video"], "rb") as f:
        r = c.post("/api/videos", files={"file": ("fixture.mp4", f, "video/mp4")},
                   data={"title": "SYNTHETIC auto", "left_name": "Left A", "right_name": "Right B"})
    assert r.status_code == 200 and r.json()["job"]["kind"] == "auto_analyze"
    vid = r.json()["video"]["video_id"]
    j, seen = _wait(c, r.json()["job"]["job_id"], timeout=600)
    assert j["status"] == "completed", j.get("message")
    res = j["result"]
    touches = c.get("/api/touches", params={"video_id": vid}).json()
    # the fixture lamp lights at frame 150 (and at 232, only 8 frames before the end: shorter than the
    # 0.3 s a lamp must stay lit, so it is correctly not a candidate)
    assert len(touches) >= 1 and res["analysed"] == len(touches) and not res["failed"]
    assert all(t["light_source"] == "detector_candidate" and t["latest_run"] for t in touches)
    assert any(abs(t["light_frame"] - 150) <= 2 for t in touches)
    v = [x for x in c.get("/api/videos").json() if x["video_id"] == vid][0]
    assert v["bouts"][0]["fencers"][0]["display_name"] in ("Left A", "Right B")
    assert v["job"]["status"] == "completed"
