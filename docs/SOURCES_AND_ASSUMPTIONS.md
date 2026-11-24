# Sources reviewed, PRD differences, assumptions

## Inputs actually checked

| Source | Status |
|---|---|
| `EngardeAnalytics-PRD-v1.1.docx` | Read in full. |
| `Engarde Analytics PRD v1.2.docx` | Read in full. **Its title says "v1.12"** (dated Oct 20, 2026). Treated as the newer spec. |
| AIRS repo (github.com/andmer/AIRS) | Cloned and read: SPEC.md, `frames/context.example.json`, `clips/touches.csv`, `prompts/01_perception.md`. Verified: `context.json = {lights:{left,right}, timeline:"<text>"}`, times relative to the light (negative before), left/right = screen position, perception treats the timeline as primary evidence. |
| engardeanalytics.netlify.app | **Not verified.** Only a Google / e-mail login wall is visible. No sign-in was attempted and no account was created, so its bout/touch/label workflow is unknown. No credentials or source from it are used. |
| "Old repo" | **Not available.** No Engarde repository was provided or reachable; the only repo given was AIRS. Data structures follow PRD v1.12 §4.2 instead. |
| Dribbble references (visual brief) | **Not readable** (pages render client-side and come back empty). The six uploaded Rondesignlab screenshots were used as the visual reference. |
| Real videos | `Szabo-vs-Heathcock-sabre.webm` (FencingTV, 8:20.8, 294 MB) and `USA-vs-Poland-mens-team-sabre.webm` (FencingTV, 30:02.7, 1.02 GB), read from the user's computer. Processed locally only. |

## PRD v1.1 vs v1.12: differences that change the implementation

| Topic | v1.1 | v1.12 (implemented) |
|---|---|---|
| Footwork classes | Advance, Retreat, Lunge, Gathering, Holding (+Hop/Recovery later) | **Advance, Retreat, Lunge, Other/Still**, one label per frame; Hop/Recovery only once defined |
| Unknown vs Unobservable | not distinguished | **separate** (plus system `no_call`) |
| Window | "agreed reference time" | **[-3.0 s, +0.5 s] around the light** |
| Data contract | field list only | **6 stages with on-disk files** (video_meta.json, touches.csv, tracks.parquet, pose.npz, features.parquet, pred.parquet, review.csv, airs_timeline.txt) + §4.2 record fields |
| Review UI | "support coaches viewing …" | "no full web UI; review.csv". **Overridden by this round's brief:** light web workbench and review.csv both exist. |
| TCN | 4 blocks, pooling, clip classification | seq2seq per-frame, 6 blocks, dilation 32, T-MSE. **Not built this round.** |
| XGBoost | params only | + W in {9,15,21} centred window, 1/sqrt(freq) weights on training folds only, early stopping |
| Quality gate | concept | low_confidence < 0.5, pose_missing > 30 %, track_unstable, camera_cut |
| Post-processing | concept | median 5 frames, merge < 4 frames |
| Events | incl. foot_lift | foot_lift dropped; rule event baseline defined |
| Splits | LOSO / LOMO | dev = leave-one-bout-out; frozen test = unseen bouts **and** athletes |
| Tracking | ByteTrack params | + tau_low 0.1, fencer-selection rules (piste filter, top-2 by frames x height, left = smaller median x, merge <= 15-frame gaps) |

## Scope decisions this round

* **Preparation step (准备步)** is Layer D (tactical) in PRD v1.12 and is not trained. The system does not output it. A coach can mark such spans `Unknown` until it is defined physically.
* **Hop, Recovery** exist in the taxonomy but are disabled (`data/taxonomy.json` → `{"enabled_extra": ["Hop"]}` enables one). The rule baseline labels a backward recovery after a lunge as `Retreat`.
* **No refereeing.** Nothing computes hits, scores, priority or right-of-way. The light detector produces *candidate touch anchors* only. AIRS export refuses tactical/referee words.
* **Pose model:** MediaPipe, RTMPose and HRNet weights are on hosts blocked from this environment, so only Ultralytics YOLO11n-pose (GitHub release) is wired. It is COCO-17: **no toe/heel keypoints**, so foot_contact cannot be measured.

## Implementation assumptions

1. **Frame numbers** = 0-based decoded-frame index. **Times** = decoder best_effort_timestamp, never frame x nominal fps (both real videos are VFR screen recordings: the container says 60/120 fps, the content is ~30 fps with 10–100 ms frame gaps).
2. **Intervals** are `[start_frame, end_frame)` everywhere (DB, API, UI, CSV/JSON, scoring). `t_end_ms` is the time of frame `end_frame`.
3. **left/right** is the fixed side label = screen side at bout start (FIE: red lamp = left, green = right). Athlete identity is a separate field. Per-frame screen position and crossings are stored per frame; identities are never swapped silently.
4. **Light frames** come from a chroma candidate detector (`light_source = detector_candidate`) and need human verification in the UI. Broadcast **replays** re-show the lamps and produce extra candidates, which the reviewer deletes.
5. **Long videos** are processed in time chunks of the original file. A file too big to transfer was split losslessly (`-c copy`), re-joined, and verified packet-by-packet (count, sizes, MD5) against a fingerprint made on the source machine. Analysis timestamps are the original PTS.
6. **No calibration** is estimated. Distances are in body-scale units. Metric distances, strip position and recovery duration show `unavailable`.
7. Rule thresholds are hand-set starting points (body-scale units/s), **not** grid-searched, because no labelled data exists yet.
