"""v0.5.5 bulk video import: folder and links, duplicates skipped, bad items reported."""
import functools
import http.server
import os
import shutil
import subprocess
import threading

import pytest

from engarde import bulk
from engarde.store import Store


def _clip(path, color, seconds=1):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=30:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    os.makedirs(d / "sub")
    _clip(str(d / "a.mp4"), "red")
    _clip(str(d / "sub" / "b.mov"), "blue")
    shutil.copy(d / "a.mp4", d / "a_copy.mp4")          # same file twice -> added once
    (d / "notes.txt").write_text("not a video")
    return d


def test_parse_links_formats():
    plain = bulk.parse_links("https://x.org/1.mp4\n\nhttps://x.org/2.mp4\nhttps://x.org/1.mp4")
    assert [i["url"] for i in plain] == ["https://x.org/1.mp4", "https://x.org/2.mp4"]
    titled = bulk.parse_links("LUKASHENKO vs NITU, https://x.org/3.mp4")
    assert titled == [{"url": "https://x.org/3.mp4", "title": "LUKASHENKO vs NITU"}]
    table = bulk.parse_links("id,fencer_name,opponent_name,round,video_url\n7,Darii LUKASHENKO,NITU Radu,Quarterfinal,https://x.org/4.mp4\n8,A,B,,")
    assert table == [{"url": "https://x.org/4.mp4", "title": "Darii LUKASHENKO vs NITU Radu · Quarterfinal"}]
    assert bulk.parse_links("no links here") == []


def test_import_folder_skips_duplicates_and_non_videos(clips, tmp_path):
    st = Store(str(tmp_path / "data"))
    r = bulk.import_folder(st, str(clips))
    assert r["found"] == 3 and r["added"] == 2 and r["already_here"] == 1 and not r["failed"]
    assert len(st.list_videos()) == 2
    again = bulk.import_folder(st, str(clips))
    assert again["added"] == 0 and again["already_here"] == 3
    assert all(v["n_frames"] for v in st.list_videos())        # indexed, ready to open
    assert os.path.exists(clips / "a.mp4")                     # folder import copies, never moves


def test_import_links_downloads_and_reports_failures(clips, tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(clips))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st = Store(str(tmp_path / "data"))
        items = bulk.parse_links(f"Bout A, {base}/a.mp4\n{base}/sub/b.mov\n{base}/missing.mp4\n{base}/notes.txt")
        r = bulk.import_links(st, items)
        assert r["added"] == 2 and len(r["failed"]) == 2
        titles = sorted(v["title"] for v in st.list_videos())
        assert titles == ["Bout A", "b"]
        assert not os.listdir(os.path.join(st.data_dir, "incoming"))   # no temp files left
    finally:
        srv.shutdown()


def test_list_videos_fast_matches_old(clips, tmp_path):
    """v0.5.6: the grouped-query video list gives exactly what the old per-video version gave."""
    import json
    from engarde.fixture import make_fixture
    st = Store(str(tmp_path / "data"))
    bulk.import_folder(st, str(clips))
    fx = make_fixture(str(tmp_path / "fx"))
    v = st.add_video(fx["video"], "fixture")
    st.index_video(v["video_id"])
    b = st.add_bout(v["video_id"], "bout", {"display_name": "L"}, {"display_name": "R"})
    st.add_touch(b["bout_id"], 40)
    norm = lambda x: json.dumps(x, sort_keys=True, default=str)
    assert norm(st.list_videos()) == norm(st.list_videos_slow())
    names = {r["name"] for r in st.db.q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_touches_video", "ix_runs_touch", "ix_reviews_pred"} <= names
