#!/usr/bin/env python3
"""Pack a finished analysis WITHOUT videos or cached frame images, so it can be opened on another
machine that has the original video files (see relink_video.py).

  python scripts/export_analysis.py --data data --out engarde-analysis.zip

Contents: engarde.sqlite (checkpointed), per video frame_index.npz / probe / content_rect / meta /
provenance, every touch's stages/ (tracks, pose, features, predictions, review.csv, AIRS, manifests),
reports/. Frame JPEGs are regenerated on demand from the original video.
For re-joined sources the index is rewritten so the ORIGINAL file is the decode source
(t_play = t, no t_joined): the original file's own timestamps are the analysis timestamps.
"""
import argparse, io, json, os, sqlite3, sys, zipfile
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--names", nargs="*", default=[], help="video_id=original_file_name (where the user keeps it)")
ap.add_argument("--max-mb", type=float, default=0, help="split into parts of at most this size (0 = one file)")
a = ap.parse_args()
d = os.path.abspath(a.data)
con = sqlite3.connect(os.path.join(d, "engarde.sqlite"))
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
videos = con.execute("SELECT video_id, title, path, n_frames, sha256 FROM videos").fetchall()
con.close()
manifest = {"format": "engarde-analysis/1", "videos": []}
names = dict(x.split("=", 1) for x in a.names)
import shutil, tempfile
tmpdb = os.path.join(tempfile.mkdtemp(), "engarde.sqlite")
shutil.copy(os.path.join(d, "engarde.sqlite"), tmpdb)
c2 = sqlite3.connect(tmpdb)
c2.execute("UPDATE videos SET path='(not linked: run link_videos.command)'")   # machine-specific paths
c2.execute("UPDATE jobs SET status='completed' WHERE status IN ('queued','running')")
c2.commit(); c2.close()
with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(tmpdb, "engarde.sqlite")
    for vid_id, title, path, n_frames, sha in videos:
        vd = os.path.join(d, "videos", vid_id)
        for f in ("probe.json", "content_rect.json", "video_meta.json", "provenance.json"):
            if os.path.exists(os.path.join(vd, f)):
                z.write(os.path.join(vd, f), f"videos/{vid_id}/{f}")
        idx = dict(np.load(os.path.join(vd, "frame_index.npz")))
        rejoined = "t_joined" in idx
        out = {"t": idx["t"], "key": idx["key"], "t_play": idx["t"].copy()}
        buf = io.BytesIO(); np.savez(buf, **out)
        z.writestr(f"videos/{vid_id}/frame_index.npz", buf.getvalue())
        prov = json.load(open(os.path.join(vd, "provenance.json"))) if os.path.exists(os.path.join(vd, "provenance.json")) else {}
        manifest["videos"].append({"video_id": vid_id, "title": title, "n_frames": n_frames,
                                   "original_file": names.get(vid_id) or prov.get("original_file") or os.path.basename(path),
                                   "rejoined_here": rejoined})
    for root, dirs, files in os.walk(os.path.join(d, "touches")):
        dirs[:] = [x for x in dirs if x != "frames"]
        for f in files:
            if f.endswith(".tmp.jpg"):
                continue
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, d))
    rd = os.path.join(d, "reports")
    for f in sorted(os.listdir(rd)) if os.path.isdir(rd) else []:
        if f.endswith((".json", ".csv", ".jpg")):
            z.write(os.path.join(rd, f), f"reports/{f}")
    z.writestr("ANALYSIS_MANIFEST.json", json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=1)); print(a.out, round(os.path.getsize(a.out) / 1e6, 1), "MB")

if a.max_mb:
    # split into self-contained parts (each unzips into the same data/ folder)
    src = zipfile.ZipFile(a.out)
    items = sorted(src.infolist(), key=lambda i: (not i.filename.startswith(("engarde.sqlite", "ANALYSIS", "videos/")), i.filename))
    parts, cur, size = [], [], 0
    for it in items:
        if cur and size + it.compress_size > a.max_mb * 1e6:
            parts.append(cur); cur, size = [], 0
        cur.append(it); size += it.compress_size
    if cur:
        parts.append(cur)
    base = a.out[:-4]
    for k, chunk in enumerate(parts, 1):
        name = f"{base}-part{k}of{len(parts)}.zip"
        with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
            for it in chunk:
                z.writestr(it, src.read(it.filename))
        print(name, round(os.path.getsize(name) / 1e6, 1), "MB")
    src.close()
    os.remove(a.out)
