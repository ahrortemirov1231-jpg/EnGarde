"""Bulk video import (v0.5.5): bring many bout videos into the workspace in one go, without tags.

Two sources, both run as one background job so the server stays usable:
  * a folder on the host PC (every video file in it, sub-folders included)
  * a list of links (one per line, "title, link" lines, or a CSV file such as a table export with a
    video_url / url / link column). The host PC downloads each link itself.
Each video is copied into the data folder, indexed (frame times, browser copy if needed) and checked
against the videos already here by file fingerprint (sha256), so running an import twice adds nothing.
Analysis is off by default; it can be queued per video afterwards, or for all of them with analyze=True.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request

from . import video as vid

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
URL_COLS = ("video_url", "url", "link", "video", "clip", "src")
CHUNK = 1 << 20


def find_videos(folder: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith("."):
                out.append(os.path.join(root, f))
    return sorted(out)


def _title_from_row(row: dict) -> str | None:
    """Build a readable title from common table columns (fencer_name / opponent_name / round / title)."""
    low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    if low.get("title"):
        return low["title"]
    a = low.get("fencer_name") or low.get("fencer") or low.get("left")
    b = low.get("opponent_name") or low.get("opponent") or low.get("right")
    if a or b:
        t = f"{a or '?'} vs {b or '?'}"
        for extra in ("round", "event", "tags"):
            if low.get(extra) and len(low[extra]) < 80:
                t += f" · {low[extra]}"
                break
        return t
    return None


def parse_links(text: str) -> list[dict]:
    """Accept: one link per line; "title, link" or "title<TAB>link" lines; or a CSV with a header row
    that has a link column (video_url, url, link, ...). Returns [{"url", "title"}], duplicates removed."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    items: list[dict] = []
    first = text.split("\n", 1)[0].lower()
    if any(c in [h.strip().strip('"') for h in re.split(r"[,\t;]", first)] for c in URL_COLS):
        dialect = "excel-tab" if "\t" in first else "excel"
        for row in csv.DictReader(io.StringIO(text), dialect=dialect):
            low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            url = next((low[c] for c in URL_COLS if low.get(c, "").startswith(("http://", "https://"))), None)
            if url:
                items.append({"url": url, "title": _title_from_row(row)})
    else:
        for line in text.split("\n"):
            m = re.search(r"https?://\S+", line)
            if not m:
                continue
            url = m.group(0).rstrip('",;')
            title = line[:m.start()].strip().strip('",;\t ') or None
            items.append({"url": url, "title": title})
    seen, out = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


def _name_from_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    base = os.path.basename(urllib.parse.unquote(p.path)) or "video"
    return os.path.splitext(base)[0][:120] or "video"


def download(url: str, dst_dir: str, timeout: int = 60) -> str:
    """Stream one link to a temp file in dst_dir. Raises on HTTP errors or a non-video reply."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http(s) links are supported")
    req = urllib.request.Request(url, headers={"User-Agent": "EngardeWorkspace/0.5.5"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype.startswith(("text/html", "application/json")):
            raise ValueError(f"the link returned a web page ({ctype.split(';')[0]}), not a video file")
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in VIDEO_EXT:
            ext = ".mov" if "quicktime" in ctype else ".webm" if "webm" in ctype else ".mp4"
        fd, tmp = tempfile.mkstemp(suffix=ext, dir=dst_dir)
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(r, f, CHUNK)
    if os.path.getsize(tmp) < 1024:
        os.remove(tmp)
        raise ValueError("the download was empty")
    return tmp


def _known_hashes(store) -> dict:
    return {r["sha256"]: r["video_id"] for r in store.db.q("SELECT video_id, sha256 FROM videos WHERE sha256 IS NOT NULL")}


def _add_one(store, path: str, title: str, *, move: bool, known: dict, analyze: bool, jobs=None) -> dict:
    h = vid.sha256_file(path)
    if h in known:
        if move and os.path.exists(path):
            os.remove(path)
        return {"status": "already_here", "video_id": known[h]}
    v = store.add_video(path, title, move=move, weapon_type="sabre")
    store.index_video(v["video_id"])
    known[h] = v["video_id"]
    if analyze and jobs is not None:
        jobs.submit("auto_analyze", v["video_id"])
    return {"status": "added", "video_id": v["video_id"]}


def import_folder(store, folder: str, report=None, *, analyze: bool = False, jobs=None, actor: str = "system") -> dict:
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise ValueError(f"folder not found on the host PC: {folder}")
    files = find_videos(folder)
    known, res = _known_hashes(store), {"source": folder, "found": len(files), "added": 0, "already_here": 0,
                                        "failed": [], "video_ids": []}
    for i, p in enumerate(files):
        if report:
            report(i / max(1, len(files)), "importing", f"{i + 1} of {len(files)}: {os.path.basename(p)}")
        try:
            title = os.path.splitext(os.path.basename(p))[0]
            r = _add_one(store, p, title, move=False, known=known, analyze=analyze, jobs=jobs)
            res[r["status"]] += 1
            res["video_ids"].append(r["video_id"])
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
            res["failed"].append({"item": os.path.basename(p), "error": str(e)[:300]})
    store.audit(actor, "bulk_import_folder", folder, {k: v for k, v in res.items() if k != "video_ids"})
    return res


def import_links(store, items: list[dict], report=None, *, analyze: bool = False, jobs=None, actor: str = "system") -> dict:
    tmpdir = os.path.join(store.data_dir, "incoming")
    os.makedirs(tmpdir, exist_ok=True)
    known, res = _known_hashes(store), {"source": "links", "found": len(items), "added": 0, "already_here": 0,
                                        "failed": [], "video_ids": []}
    for i, it in enumerate(items):
        title = it.get("title") or _name_from_url(it["url"])
        if report:
            report(i / max(1, len(items)), "downloading", f"{i + 1} of {len(items)}: {title}")
        tmp = None
        try:
            tmp = download(it["url"], tmpdir)
            r = _add_one(store, tmp, title, move=True, known=known, analyze=analyze, jobs=jobs)
            res[r["status"]] += 1
            res["video_ids"].append(r["video_id"])
        except Exception as e:  # noqa: BLE001
            res["failed"].append({"item": title, "error": str(e)[:300]})
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
    store.audit(actor, "bulk_import_links", f"{len(items)} links", {k: v for k, v in res.items() if k != "video_ids"})
    return res
