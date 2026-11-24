# Engarde Analytics — offline footwork pre-labelling & review MVP

Research and coaching tool: **video → touch windows → tracking + pose → features → footwork pre-labels →
human confirm/correct → save/export**. It recognises *movements* (Advance, Retreat, Lunge, Other/Still).
It is **not a referee**: nothing infers hits, points, priority or right-of-way.

Everything runs locally. Videos are never sent to a third-party API.

**Version 0.2.0** (Oct 25, 2026). See `CHANGELOG.md` for what changed since Alaia's v0.1.0.

## Quick start on a Mac (no terminal)

1. Once: install Python 3.11+ and ffmpeg (`brew install python@3.12 ffmpeg`), then **double-click `setup.command`**.
2. Every time: **double-click `start.command`**. The browser opens http://127.0.0.1:8000. Keep that window open.
3. **Videos → Upload video**, optionally with the left/right athlete names. The video is then analysed
   automatically: exact frame index → scoring-light candidates → both fencers tracked → footwork pre-labels.
   Progress shows on the video card. Open **Review** to check it.
4. In Review, each touch has a **Fencer identity** section. If the system picked the wrong people, turn on
   **All people**, read the track numbers on the video, and choose the left/right fencer; only that touch is
   re-analysed. Light frames are detector candidates: verify them or delete non-touches.

To open an analysis that was already computed elsewhere: unzip `engarde-analysis.zip` into `data/`,
double-click `link_videos.command` (it finds the original videos in ~/Downloads, checks they are the same
videos frame for frame, and links them), then `start.command`.

### How the two fencers are chosen
Per touch window, every tracked person is a candidate. A person is eligible only when their clothing is
mostly white (fencing whites; referees and the audience are not). The system then picks the **most plausible
pair**: similar height, feet on the same strip line, visible together for ≥ 30 % of the window, separated
left/right, and not much smaller than the largest white person in view. If no pair passes, nothing is guessed:
the whole touch is marked identity-uncertain (no_call) until a reviewer assigns the fencers. A reviewer's
assignment always wins and is logged (`touches/<id>/identity_log.jsonl`).

## Quick start on Windows (no terminal)

1. Once: install Python 3.12 from python.org (tick **Add python.exe to PATH**) and ffmpeg
   (`winget install Gyan.FFmpeg` in PowerShell). Copy the `engarde-mvp` folder **out of OneDrive**
   (e.g. to `C:\engarde-mvp`), then **double-click `setup.bat`**.
2. Every time: **double-click `start.bat`**. The browser opens http://127.0.0.1:8000. Keep that window open.
3. To open an analysis computed elsewhere: copy its files into `data\`, then double-click `link_videos.bat`.
4. To test without real video: double-click **`demo.bat`** (5 synthetic bouts with labels, in a separate
   `demo_data` folder, opened at http://127.0.0.1:8001) and try Review and Insights → Models. **`run_tests.bat`** runs the automated tests.
   On a Mac the same files end in `.command`.

The `.bat` files mirror the Mac `.command` files; they were written without a Windows machine to test on,
so report anything that fails.

## New in v0.5.0: one PC for the whole team

`serve_team.bat` runs the app with sign-in, and Tailscale gives the team a private https link to it. Teammates
install nothing but Tailscale, always see the latest version, and share the same videos and labels live.
Step by step: `docs/TEAM_SERVER.md`. What to send teammates: `docs/TEAMMATE_QUICKSTART.md`.

## New in v0.4.0

Tim's label names, every video reviewable, overlays while the video plays, touches in time order, delete video,
shared labels (Export labels / Import labels), Training vs Test videos, lock, and GitHub as the place for the code.
See `CHANGELOG.md`, `docs/SHARED_LABELS.md` and `docs/GITHUB.md`.

**Updating from v0.3.0 without GitHub:** close the app, then copy everything from this folder over your old
`engarde-mvp-v0.3.0` folder and choose Replace (the same works for v0.5.0). Your `data`, `data_tim`, `weights` and `.venv` folders stay as they
are, so there is nothing to set up again. On first start the app copies your database
(`data/engarde.sqlite.before-v0.4.0`) and renames old labels to Tim's names.

## Tim's tagged touches (new in v0.3.0)

Tim's Model comparison page has 164 tagged sabre touches with gold footwork labels and his models'
predictions. To train and compare on them: **gpu_setup.bat** (once, NVIDIA GPUs), **tim_data.bat**, then **start_tim.bat** (train in
Insights > Models with no frozen test bout), then **compare_tim.bat**. Details: `docs/TIM_DATA.md`.

## Models: E1 (XGBoost) vs E2 (bidirectional TCN)

Both are trained under one protocol (`engarde/ml.py: run_protocol`): leave-one-bout-out CV, frozen test
bouts behind the firewall (blind/adjudicated labels only, scored once), temperature scaling on a held-out
calibration bout, and Macro-F1 over all labelled frames with no_call counted as an error.

* Train from **Insights → Models** (tick the frozen test bouts, then *Train XGBoost (E1)* or *Train TCN (E2)*),
  or over HTTP: `POST /api/models/train {"method": "xgboost" | "tcn", "test_bouts": [...]}`.
* Each run reports a **95% interval resampled by bout** (marked unreliable below 5 bouts, PRD §10.2), a
  pooled out-of-fold confusion matrix, and a **calibration report** (4 confidence bins, ECE, Brier,
  risk-coverage, over-confidence alarm, PRD §9.2).
* `GET /api/models/compare?a=<E1 id>&b=<E2 id>` compares the two on the same out-of-fold frames with a paired
  bootstrap and applies the pre-registered rule (PRD §10.3): E2 is adopted only if it beats E1 by
  ≥ 3 points and the interval excludes 0. Insights shows the verdict.
* Analyse a touch with a trained model: `POST /api/touches/<id>/analyze {"method": "tcn", "model_id": "..."}`.

## Install

Requirements: Python ≥ 3.11 (tested 3.13.17), ffmpeg/ffprobe ≥ 6 on PATH, Node ≥ 20 (tested 22.23) for the UI build.

```bash
cd engarde-mvp
python3 -m venv .venv && source .venv/bin/activate
# CPU-only torch (avoids multi-GB CUDA wheels on Linux; on macOS plain `pip install torch==2.14.1`)
pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# pose weights (Ultralytics YOLO11n-pose, COCO-17, AGPL-3.0)
mkdir -p weights
curl -L -o weights/yolo11n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt
# sha256 869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0

cd frontend && npm ci || npm install && npm run build && cd ..
```

## Run

```bash
ENGARDE_DATA=$PWD/data uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000  (Videos / Review / Insights)
```
UI development with hot reload: `cd frontend && npm run dev` (proxies `/api` to :8000).

### Command-line pipeline

```bash
# 1. import + index a local video (exact frame index, browser proxy)
python3 - <<'EOF'
import sys; sys.path.insert(0, "backend")
from engarde.store import Store
s = Store("data"); v = s.add_video("/path/bout.webm", "My bout", capture_type="broadcast_panning", weapon_type="sabre")
s.index_video(v["video_id"])
s.add_bout(v["video_id"], "A vs B", left={"athlete_id": "A"}, right={"athlete_id": "B"})
print(v["video_id"])
EOF
# 2. light candidates -> touches -> YOLO pipeline, on a time range of the ORIGINAL video
python3 scripts/process_video.py --data data --video vid_xxx --from 30 --to 60 --report data/reports/r.json
python3 scripts/process_video.py --data data --video vid_xxx --from 0            # whole video, chunked
# 3. visual QA of one touch (boxes, sides, skeleton, flags, predictions on exact frames)
python3 scripts/qa_render.py --data data --touch tch_xxx --every 4 --out qa.jpg
# files too large to move in one piece: lossless parts + packet-fingerprint verification
python3 scripts/import_reassembled.py --data data --parts p0.webm p1.webm ... --framemd5 orig.framemd5 \
        --title "..." --original-name orig.webm
# synthetic fixture end-to-end (data-flow check only — not real footage)
python3 scripts/demo_fixture.py --data /tmp/fx --with-labels
# tests
cd backend && python3 -m pytest -q
```

## Pipeline and files (PRD v1.12 §4; every stage writes to `data/touches/<id>/stages/`)

| Stage | Output | Notes |
|---|---|---|
| S1 video & lights | `data/videos/<id>/frame_index.npz`, `video_meta.json`, `s1_window.json`, `frames/*.jpg` | frame numbers = decoded index; times = decoder timestamps (VFR-safe). Window [−3.0 s, +0.5 s] by timestamp, clipped at the ends. Light **candidates** from a chroma detector (red lamp = left, green = right), to be verified in the UI. |
| S2 detection+tracking | `detections.parquet` (all tracked people kept), `tracks.parquet` | YOLO11n-pose + Ultralytics ByteTrack (τ_high 0.6, τ_low 0.1, IoU 0.2, buffer ≈ 1 s). Fencer selection: optional piste polygon → offline fragment merge (≤ 15 frames) → top-2 by frames × height → left = smaller median x. Flags: track_break, id_switch_suspect, identity_uncertain (crossings, after shot cuts), per-frame screen_position. |
| S3 pose | `pose.npz` | raw keypoints + mask (conf < 0.3 → 0) + interpolation flag (gaps ≤ 3). Raw values kept. |
| S4 features | `features.parquet`, `camera.parquet` | mirroring, per-frame front/back legs, first-frame normalisation, regression derivatives on true Δt, repeated-frame handling, camera motion (ORB+RANSAC on the scene band), zoom and cut detection. Units: body-scale, **no metres** without calibration. |
| S5 recognition | `pred_<run>.parquet`, DB rows | **Rule baseline / unvalidated** (no probabilities). XGBoost when trained. |
| S6 quality gate | `review.csv`, `airs_timeline.txt`, `context.json`, `run_<run>.json` | no_call: pose_missing, track_unstable, camera_cut, low_confidence (only with calibrated probabilities). |

Stages are cached by config hash and inputs: re-running after a rule change only redoes S4–S6.

## Data model

SQLite `data/engarde.sqlite`: `videos, bouts, fencers, touches, prediction_runs, action_segments
(kind = prediction | annotation), events, review_records, annotation_log, prediction_views, jobs, models`.

* Intervals are **`[start_frame, end_frame)`** everywhere.
* Predictions are immutable (DB trigger). Review records are append-only (trigger). Each review links the
  original prediction, reviewer, time, corrected label/bounds and reason.
* `label_source ∈ {blind, model_assisted, adjudicated, unknown}`. **Firewall:** once an annotator has
  viewed predictions for a touch (logged in `prediction_views`), anything they label or edit there becomes
  `model_assisted`. Blind mode hides predictions, confidence and candidates.
* Human states `Unknown` (visible, undecidable) and `Unobservable` (not visible) are labels. The system's
  `no_call` is a prediction flag with a reason. A no-evidence prediction carries the label `none`, never Other/Still.

## Exports / imports (all re-importable)

`/api/export/{predictions,annotations,reviews,review,events,runs,bundle}.{csv|json}?video_id=…`,
`/api/airs/<touch>?arm=automated|expert&fmt=txt|json`. Imports: `/api/import/bundle`,
`/api/import/annotations_csv`, `/api/import/review_csv` (rows with `review_action` filled become review records).

## XGBoost (E1)

`POST /api/models/train {"W": 15, "test_bouts": [...]}`: per-frame windowed statistics, sample weights
∝ 1/√freq on training folds, **leave-one-bout-out CV**, athlete-overlap check between dev and test bouts,
temperature scaling on a held-out calibration bout (only with ≥ 3 dev bouts), and test bouts scored once,
on blind/adjudicated labels only. Reported: **Macro-F1 over all labelled frames (no_call counts as an
error), coverage, abstention**, and Macro-F1 on covered frames as a secondary number. No model has been
trained on real labels yet, so **no real-video model performance exists**.

## Status

See `docs/STATUS.md` (done / not done, verification results, known limits) and
`docs/SOURCES_AND_ASSUMPTIONS.md` (what was checked, PRD differences, assumptions).
**TCN (E2) is built** and compared with E1 under one protocol (deliverable T2), but only on synthetic data: no real labels exist yet.
