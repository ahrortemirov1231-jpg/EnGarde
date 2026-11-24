# Status — first runnable increment

_Numbers in the real-video sections come from the runs recorded in `data/reports/`._

## Done (runs end to end)

| Area | What exists |
|---|---|
| Video import | upload/CLI import; ffprobe metadata; **decoded-frame index with real timestamps** (VFR-safe); browser proxy (H.264, ≤ 1920 px) for playback only; content-rectangle detection for display (letterbox) |
| Touches | manual light frame + lamp sides; **light candidate detector** (red/green chroma onset, lockout merge ≤ 0.2 s); window [−3.0, +0.5] s by timestamp, clipped at ends; manual range override; verify / delete candidate in UI |
| Exact frames | keyframe-seek decode with per-frame PTS verification against the index; window frames cached as JPEG; the UI shows the decoded frame when paused and labels playback as approximate |
| Identity | fixed side label, separate athlete_id, per-frame screen_position; crossings, suspected ID switches, track breaks and post-cut frames flagged `identity_uncertain`; never swapped silently |
| Pipeline S1–S6 | on-disk stage outputs, cached by config hash, stage status in UI; failures recorded (e.g. missing `lap`, missing weights) |
| Rule baseline | Advance / Retreat / Lunge / Other/Still; labelled **"Rule baseline / unvalidated"**; no probabilities; median-5 + merge < 4 frames; rule events (lunge_start, lunge_land, arm_extension_full) |
| Quality gate | pose_missing (core and lower-body evidence), track_unstable, camera_cut, low_confidence (calibrated models only). No-evidence spans carry no label |
| Review workbench | Videos / Review / Insights; touch list; player with exact stepping, 0.1–1× speed, skeleton/box/quality toggles; per-fencer prediction + human lanes; drag to create, drag handles; Confirm / Correct / Unknown / Unobservable / Reject; expert events; coverage check; AIRS export links |
| Provenance | append-only reviews; immutable predictions (DB trigger); annotation history log; blind/model_assisted firewall |
| Export / import | predictions, annotations (training labels), reviews, review.csv (re-importable), events, run manifests with versions, full bundle (re-importable, idempotent, time-checked) |
| AIRS | `context.json {lights, timeline}`; physical actions only; real-frame times; source per line; "not determinable" for no_call; expert arm separate |
| XGBoost (E1) | training/inference entry: LOBO CV, athlete-leakage check, 1/√freq weights, temperature scaling with ≥ 3 bouts, firewall for test bouts (blind/adjudicated only), Macro-F1 (all frames) + coverage + abstention. Verified on synthetic data only |
| Insights | counts, durations; unavailable states for metres, recovery, hand-vs-foot, etc. |
| TCN (E2) | Bidirectional seq2seq TCN (PRD §7.4: 6 non-causal dilated blocks, 127-frame receptive field, weighted CE + MS-TCN++ T-MSE, AdamW + warm-up/cosine, early stopping on an inner bout, random sub-sequences). Same protocol as E1. Verified on synthetic data only |
| One protocol for E1 and E2 | `ml.run_protocol`: identical folds, firewall, temperature scaling and scoring; out-of-fold predictions saved per model |
| Evaluation (PRD §9.2, §10.2, §10.3) | Calibration report (4 bins, ECE, Brier, risk-coverage, over-confidence alarm, monotonicity rule), 95% intervals resampled by bout, pooled confusion matrix, paired E1-vs-E2 comparison with the pre-registered adoption rule |
| Windows | `setup.bat`, `start.bat`, `link_videos.bat` (+ cross-platform `scripts/link_videos.py`) |
| Tests | 39 automated tests (see below) |

## Not done

* **TCN ablations** (skeleton-only inputs TCN-A/B/C, causal vs bidirectional, keypoint sets K1–K3) are not run; E2 uses the kinematic features (the "TCN-D" variant). The E1-vs-E2 comparison exists but has only been run on synthetic data.
* No real labels exist, so **no model has been trained on real data** and there is **no real-video accuracy number**. Rule thresholds are not grid-searched.
* Piste calibration (homography clicks) is not implemented, so no metres or strip position.
* Event head, refinement stages (E4), ST-GCN — not implemented.
* Light detection is a candidate detector for this broadcast style only. It does not recognise **replays**, so replays produce extra candidates to delete.
* Pose model comparison (RTMPose / HRNet with foot keypoints) — blocked by weight hosts in this environment. COCO-17 has no toes/heels.
* AIRS three-arm experiment — needs the AIRS bouts' raw video, an API budget and consent; not run.

## Real-video verification

### Szabo vs Heathcock (FencingTV, 8:20.8, 14,964 frames, VFR ≈ 29.9 fps)

Imported whole, so frame numbers and times are the original's. Reports: `data/reports/szabo_*.json|csv|jpg`.

**30 s validation segment (30–60 s)**
* Light candidates: 2, both confirmed against the frames: f1156 (38.88 s, red + green, 150 ms apart) and f1437 (48.32 s, green).
* Two fencers in both touches. Left = SZABO, right = HEATHCOCK. The same track ids throughout; background official (track 3) ranked third and excluded.
* Every touch ends in a broadcast cut or fast pan about +100 ms after the light. Detected as camera_cut; later frames are identity_uncertain → no_call.

**Full video**
* 39 light candidates, all 39 analysed without failures. Two fencers selected in 39/39.
* Fencer presence (median) left 1.00 / right 0.98. Visible core keypoints (median) 1.00 / 0.97. 0 suspected ID switches, 0 crossings. 74 short track breaks (interpolated or flagged).
* Visual identity check (one overlay frame per touch): wrong-person boxes in #8, 19, 24, 25, 29, 30 (background people, referee), plus empty strips in #17 and #38. After adding the pair-plausibility check, all six are flagged `pair_implausible` and become no_call.
* Visual check of the light candidates (my reading of the frames, not ground truth): ~24 live touch actions, ~14 non-live (fencers apart, empty strip, replay or official framing: #6, 8, 10, 11, 17, 19, 24, 25, 29, 30, 31, 32, 36, 38), 2 unclear (#18, #35). Detector precision is roughly 60%. Recall not measured.
* Share of 8,224 fencer-frames: no_call 40.4% (track_unstable 35.6%, camera_cut 13.9%, pose_missing 12.5%; reasons overlap), Other/Still 31.2%, Advance 13.8%, Lunge 7.8%, Retreat 6.8%.
* Labels are **rule baseline / unvalidated**: there are no human labels yet, so no accuracy is claimed. A known visible miss: touch 1, the left fencer's final lunge into the light (f1151–1158) is labelled Advance; the earlier part (1143–1151) is Lunge.

**Defects found on real footage and fixed**
1. Camera-motion estimate locked onto static broadcast overlays (logo, score bar) → scene band only, plus zoom and cut detection.
2. Screen-recording timing (10–100 ms frame gaps, ~20% repeated frames) made speed spikes → regression derivatives on true Δt; repeated frames re-interpolated.
3. Pose model swaps anatomical left/right ankles in side view → per-frame front/back legs (PRD §6.1).
4. Lower body invisible still counted as evidence → lower-body visibility required.
5. Decoding from frame 0 for every touch (quadratic) → keyframe seek with per-frame PTS check.
6. **The source changes resolution mid-stream** (3420×1450 for the first 5 s, then 3420×2214). OpenCV and the first decoder squashed later frames vertically by 0.655. All frames are now fitted, undistorted, into one canvas, and every touch was re-extracted and re-analysed (older runs kept as history).
7. Background people and officials could be picked as a fencer → pair-plausibility flag.

### USA vs Poland, men's team sabre (FencingTV, 30:02.7, 54,008 frames, VFR ≈ 30.0 fps)

* Too large to transfer whole (1.02 GB > 400 MB). Split on the source machine with `ffmpeg -c copy` into 5 keyframe-aligned parts and re-joined here. **Verified packet by packet** against a fingerprint of the original video stream: 54,008 packets, identical sizes and MD5s, same order. All analysis times are the original PTS. The browser proxy was re-stamped to the original timeline (max deviation 4.03 ms).
* The source also changes resolution (3456×1694 → 3456×2234); frames are fitted into one canvas.
* 166 light candidates → 152 touches after de-duplication; **152/152 analysed, 0 failures**; two fencers selected in 152/152.
* Presence median 1.00 / 1.00, visible core keypoints median 1.00 / 1.00, 0 suspected ID switches, 127 short track breaks.
* Shares of 32,132 fencer-frames: Other/Still 31.9%, no_call 29.5% (track_unstable 26.3%, pose_missing 12.7%, camera_cut 2.9%), Advance 17.2%, Retreat 14.0%, Lunge 7.4%. Labels are rule baseline / unvalidated.
* Identity check (one overlay frame per touch, all of #1–46 and every second touch after): the new failure mode here is **foreground audience heads**. These, plus referees and background people, are flagged pair_implausible in 46 touches. Remaining unflagged error seen: #49 (referee at fencer size on the strip taken as RIGHT). Unclear: #18, #20.
* 166 candidates is far more than the lit touches in a 45-touch team match: replays and between-leg sequences produce many false candidates, so manual verification in the UI is required. Precision was not measured for this video.
* Team relay: athletes change every leg. The bout uses team-level sides (athlete_id empty), so athlete isolation for training cannot be verified until per-leg athletes are entered.

### Runtime (2 CPU cores, no GPU)
* Frame index: about 1/3 real time. Light candidates: about real time. Per touch (S1–S6, YOLO at 960 px on ~106 frames of 3.4K video): 35–60 s. Proxy: about real time.
* Szabo (8.3 min, 39 touches) about 45 min end to end. USA (30 min, 152 touches) about 3.5 h end to end.

### Tests
`cd backend && python -m pytest -q` → **39 passed** (synthetic fixture + generated VP9 clips; nothing measures real-video recognition). New: `tests/test_models.py` (E1/E2 protocol, comparison, calibration, bootstrap, vectorised features).

## Changes in v0.2.0 (Oct 25, 2026)

* **E2 bidirectional TCN** (`engarde/tcn.py`) and **one shared training protocol** for E1 and E2 (`engarde/ml.py`).
* **Evaluation helpers** (`engarde/evaluation.py`): calibration report, bout-level bootstrap intervals, paired model comparison with the PRD §10.3 rule. Shown on **Insights → Models**, which also gained train buttons and a frozen-test-bout picker.
* **Speed**: training-set building checked frame evidence once per frame for every frame (quadratic per touch); now once per sequence. Window features are vectorised (identical output, ~15x faster on a 2,000-frame sequence).
* **Safety**: analysing a touch with a model of the wrong kind (e.g. an XGBoost id passed as TCN) is refused.
* **Windows launchers** and a cross-platform relink script.
