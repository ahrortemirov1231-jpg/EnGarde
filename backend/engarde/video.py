"""Video metadata, exact frame index, and exact frame extraction.

Frame numbers are DECODED-frame indices (0-based) in decode/presentation order, as produced by
ffprobe -show_frames and by sequential OpenCV reads. Frame timestamps come from the decoder's
best_effort_timestamp (so variable-frame-rate video keeps its real times). We never derive a frame
number by multiplying time x nominal fps.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading

import cv2
import numpy as np


def sha256_file(path: str, limit_mb: int = 2048) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        read = 0
        while chunk := f.read(1 << 20):
            h.update(chunk)
            read += len(chunk)
            if read > limit_mb << 20:
                break
    return h.hexdigest()


def _frac(s: str | None) -> float | None:
    if not s or s in ("0/0", "N/A"):
        return None
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b) if float(b) else None
    return float(s)


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration,time_base:format=duration,format_name",
         "-of", "json", path], capture_output=True, text=True, check=True)
    info = json.loads(out.stdout)
    if not info.get("streams"):
        raise ValueError("no video stream found")
    st = info["streams"][0]
    fmt = info.get("format", {})
    return {
        "codec": st.get("codec_name"),
        "width": int(st["width"]), "height": int(st["height"]),
        "fps_nominal": _frac(st.get("r_frame_rate")),
        "fps_avg": _frac(st.get("avg_frame_rate")),
        "container_duration_s": float(fmt.get("duration") or st.get("duration") or 0) or None,
        "time_base": st.get("time_base"),
        "format_name": fmt.get("format_name"),
    }


def build_frame_index(path: str) -> dict:
    """Decode every frame once (ffprobe) and record its timestamp and keyframe flag."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "frame=best_effort_timestamp_time,key_frame", "-of", "compact=p=0:nk=0", path],
        capture_output=True, text=True, check=True)
    ts, key = [], []
    for line in out.stdout.splitlines():
        kv = dict(p.split("=", 1) for p in line.strip().split("|") if "=" in p)
        t = kv.get("best_effort_timestamp_time")
        if t is None:
            continue
        if t == "N/A":  # skipping it would desync frame numbers from sequential decoding
            raise ValueError("a decoded frame has no timestamp; re-mux the video before import")
        key.append(int(kv.get("key_frame", 0)))
        ts.append(float(t))
    ts_arr = np.asarray(ts, dtype=np.float64)
    if len(ts_arr) == 0:
        raise ValueError("could not decode any frames")
    if np.any(np.diff(ts_arr) <= 0):
        # Out-of-order timestamps would make frame<->time mapping ambiguous: refuse loudly.
        raise ValueError("non-monotonic frame timestamps; re-mux the video before import")
    return {"t": ts_arr, "key": np.asarray(key, dtype=np.int8)}


def index_summary(idx: dict) -> dict:
    t = idx["t"]
    n = len(t)
    d = np.diff(t) if n > 1 else np.array([0.0])
    fps_measured = (n - 1) / (t[-1] - t[0]) if n > 1 and t[-1] > t[0] else None
    med = float(np.median(d)) if n > 1 else None
    vfr = bool(n > 2 and med and np.max(np.abs(d - med)) > 0.01 * med + 1e-4)
    return {
        "n_frames": int(n),
        "t_first": float(t[0]),
        "t_last": float(t[-1]),
        "duration_s": float(t[-1] - t[0] + (med or 0)),
        "fps_measured": fps_measured,
        "vfr": vfr,
    }


def save_index(idx: dict, path: str):
    np.savez(path, **{k: v for k, v in idx.items() if isinstance(v, np.ndarray)})


def load_index(path: str) -> dict:
    z = np.load(path)
    return {k: z[k] for k in z.files}


def window_frames(idx: dict, light_frame: int, pre_s: float, post_s: float) -> tuple[int, int, bool]:
    """Return the analysis window [start, end) by REAL timestamps around the light frame,
    clipped to the video. Third value = whether clipping happened."""
    t = idx["t"]
    n = len(t)
    if not 0 <= light_frame < n:
        raise ValueError(f"light_frame {light_frame} outside video (0..{n - 1})")
    tl = t[light_frame]
    want_start, want_end = tl - pre_s, tl + post_s
    start = int(np.searchsorted(t, want_start - 1e-6, side="left"))
    end = int(np.searchsorted(t, want_end + 1e-6, side="right"))  # include frame at exactly +post
    clipped = bool(want_start < t[0] - 1e-6 or want_end > t[-1] + 1e-6)
    return max(0, start), min(n, end), clipped


def extract_frames(path: str, frames: list[int], out_dir: str, jpeg_quality: int = 92,
                   expected_total: int | None = None) -> dict:
    """DEPRECATED — OpenCV silently rescales frames to the first frame's size when a stream changes
    resolution. Use extract_frames_fast (canvas-fitted, PTS-verified)."""
    raise RuntimeError("extract_frames is deprecated; use extract_frames_fast")
    os.makedirs(out_dir, exist_ok=True)
    wanted = sorted(set(int(f) for f in frames))
    todo = [f for f in wanted if not os.path.exists(os.path.join(out_dir, f"{f:06d}.jpg"))]
    if not todo:
        return {"written": 0, "cached": len(wanted)}
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video")
    todo_set = set(todo)
    last = todo[-1]
    i, written = 0, 0
    while i <= last:
        if i in todo_set:
            ok, img = cap.read()
            if not ok:
                break
            cv2.imwrite(os.path.join(out_dir, f"{i:06d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            written += 1
        else:
            if not cap.grab():
                break
        i += 1
    cap.release()
    if written != len(todo):
        raise RuntimeError(f"decoded only {written}/{len(todo)} requested frames (stopped at {i})")
    return {"written": written, "cached": len(wanted) - written}


def read_frames(out_dir: str, frames: list[int]):
    for f in frames:
        img = cv2.imread(os.path.join(out_dir, f"{f:06d}.jpg"))
        yield f, img


def needs_browser_proxy(meta: dict, path: str) -> bool:
    return not (meta.get("codec") == "h264" and path.lower().endswith((".mp4", ".m4v", ".mov")))


def make_browser_proxy(path: str, out_path: str):
    """H.264 copy for in-browser playback only, on the same canvas as analysis frames (fixed size even
    if the source changes resolution). Exact frames are always decoded from the original."""
    cv = canvas_size(path)
    w = min(1920, cv["width"]) // 2 * 2
    h = int(round(cv["height"] * w / cv["width"])) // 2 * 2
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-map", "0:v:0", "-vf", vf, "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                    "-movflags", "+faststart", out_path], check=True)


def content_rect(path: str, idx: dict | None = None, samples: int = 7) -> list[int] | None:
    """Bounding box [x, y, w, h] of non-black content on the analysis canvas (letterbox bars baked into
    screen recordings). DISPLAY only; analysis uses full canvas frames and coordinates."""
    acc = None
    if idx is None:
        return None
    n = len(idx["t"])
    for k in range(samples):
        f = int((k + 0.5) * n / samples)
        for _, img in decode_range(path, idx, f, f + 1):
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
            acc = g if acc is None else np.maximum(acc, g)
    if acc is None:
        return None
    rows = np.where(acc.mean(axis=1) > 12)[0]
    cols = np.where(acc.mean(axis=0) > 12)[0]
    if not len(rows) or not len(cols):
        return None
    x0, x1, y0, y1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    H, W = acc.shape
    if (x1 - x0) * (y1 - y0) > 0.97 * W * H:
        return [0, 0, W, H]
    return [x0, y0, x1 - x0, y1 - y0]


_canvas_cache: dict = {}


def canvas_size(path: str, samples: int = 12) -> dict:
    """Largest frame size in the file (screen recordings can change resolution mid-stream). All
    decoded frames are fitted into this canvas (aspect kept, padded), so coordinates are comparable."""
    if path in _canvas_cache:
        return _canvas_cache[path]
    meta = probe(path)
    dur = meta.get("container_duration_s") or 0
    sizes = {(meta["width"], meta["height"])}
    for k in range(samples):
        t = dur * (k + 0.5) / samples if dur else 0
        out = subprocess.run(["ffprobe", "-v", "error", "-read_intervals", f"{t:.3f}%+#1", "-select_streams", "v:0",
                              "-show_entries", "frame=width,height", "-of", "csv=p=0", path],
                             capture_output=True, text=True).stdout.strip().splitlines()
        if out:
            w, h = (int(x) for x in out[0].split(",")[:2])
            sizes.add((w, h))
    W = max(w for w, _ in sizes)
    H = max(h for _, h in sizes)
    res = {"width": W, "height": H, "sizes_seen": sorted(sizes), "variable": len(sizes) > 1}
    _canvas_cache[path] = res
    return res


def decode_range(path: str, idx: dict, start: int, end: int, out_w: int | None = None):
    """Yield (frame_no, BGR image) for frames [start, end) WITHOUT decoding from the beginning:
    ffmpeg input-seeks to the last keyframe <= start (taken from the frame index), decodes forward,
    and every emitted frame's PTS is checked against the index (raises on any mismatch).
    out_w: optional output width (aspect kept) for cheap scans such as lamp detection."""
    import re
    import threading
    # timestamps as stored in the DECODED file: t_joined for re-joined sources, else the file's own times
    t_file = idx["t_joined"] if "t_joined" in idx else idx.get("t_play", idx["t"])
    key = idx["key"]
    ks = [k for k in range(start, -1, -1) if key[k]]
    k0 = ks[0] if ks else 0
    cv = canvas_size(path)
    W0, H0 = cv["width"], cv["height"]
    if out_w:
        w = int(out_w) // 2 * 2
        h = int(round(H0 * w / W0)) // 2 * 2
    else:
        w, h = W0, H0
    ss = max(0.0, float(t_file[k0]) - 0.0005)
    cmd = ["ffmpeg", "-v", "info", "-nostats", "-threads", "0", "-ss", f"{ss:.6f}", "-i", path, "-map", "0:v:0",
           "-copyts", "-fps_mode", "passthrough", "-frames:v", str(end - k0), "-vf",
           f"showinfo,scale={w}:{h}:force_original_aspect_ratio=decrease:flags=area,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    p = __import__("subprocess").Popen(cmd, stdout=__import__("subprocess").PIPE, stderr=__import__("subprocess").PIPE)
    pts = []
    tb = _frac(probe(path).get("time_base")) or 0.001
    def _err():
        # integer pts x time_base: showinfo's pts_time is printed with ~6 significant digits only
        for line in p.stderr:
            if b"showinfo" not in line.split(b"]")[0] + b"]":
                continue
            m = re.search(rb"\bpts:\s*(-?\d+)", line)
            if m:
                pts.append(int(m.group(1)) * tb)
    th = threading.Thread(target=_err, daemon=True)
    th.start()
    n = w * h * 3
    f = k0
    try:
        while f < end:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            if f >= start:
                yield f, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            f += 1
    finally:
        p.stdout.close()
        p.wait()
        th.join(timeout=5)
    if f < end:
        raise RuntimeError(f"decode_range: got frames up to {f}, expected {end}")
    got = np.asarray(pts[: end - k0])
    exp = np.asarray(t_file[k0:end])
    if len(got) != len(exp) or np.max(np.abs(got - exp)) > 0.002:
        raise RuntimeError("decode_range: decoded PTS do not match the frame index (seek not frame-exact)")


_extract_locks: dict = {}
_extract_guard = threading.Lock()


def extract_frames_fast(path: str, idx: dict, frames: list[int], out_dir: str, jpeg_quality: int = 92) -> dict:
    """One extraction per folder at a time. A new touch is extracted by a background job AND, if the
    page asks for a frame first, by that request; before v0.4.0 the two could collide and the job was
    reported as failed even though the frames were fine."""
    with _extract_guard:
        lock = _extract_locks.setdefault(os.path.abspath(out_dir), threading.Lock())
    with lock:
        return _extract_frames_fast(path, idx, frames, out_dir, jpeg_quality)


def _extract_frames_fast(path: str, idx: dict, frames: list[int], out_dir: str, jpeg_quality: int = 92) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    wanted = sorted(set(int(x) for x in frames))
    todo = [x for x in wanted if not os.path.exists(os.path.join(out_dir, f"{x:06d}.jpg"))]
    if not todo:
        return {"written": 0, "cached": len(wanted), "method": "cached"}
    written = 0
    todo_set = set(todo)
    for f, img in decode_range(path, idx, todo[0], todo[-1] + 1):
        if f in todo_set:
            final = os.path.join(out_dir, f"{f:06d}.jpg")
            tmp = final + ".tmp.jpg"
            cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            os.replace(tmp, final)          # atomic: an interrupted run never leaves a truncated frame
            written += 1
    if written != len(todo):
        raise RuntimeError(f"wrote {written}/{len(todo)} frames")
    return {"written": written, "cached": len(wanted) - written, "method": "keyframe-seek + PTS-verified"}
