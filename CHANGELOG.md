# Changelog

## v0.5.6 (Nov 23, 2026)

### Fixed
- **The site froze after the bulk import (107 videos).** Building the video list took about 5 seconds with
  that many videos, and the Videos page asked for it twice every 5 seconds, so requests piled up until even
  sign-in stopped answering. Added database indexes and rebuilt the list from a few grouped queries: it now
  takes about 0.01 seconds on the same data. The sign-in check no longer waits on the server's main loop,
  and pages stop refreshing while their tab is hidden and never start a refresh before the last one ends.

## v0.5.5 (Nov 20, 2026)

### Added
- **Bulk import** on the Videos page (admins only). Adds many bout videos at once, without labels:
  - **Folder on the host PC:** copy video files into `C:\EnGarde\incoming` (or any folder) and start the import.
  - **List of links:** paste links, one per line, or load a CSV with a `video_url` column (for example a table
    export from Tim's site). The host PC downloads each link itself.
  - Videos already in the workspace are skipped, so running an import twice is safe. One bad file or link is
    listed and the rest carry on. Analysis is off by default.
- **`download_usafencing.bat`**: downloads USA Fencing's sabre bout videos from their YouTube channel
  (approved for this project) into `incoming\usafencing`, up to 200 per run at 720p. Run it again to get more;
  videos already downloaded are skipped. Then use Bulk import on that folder.

## v0.5.4 (Nov 20, 2026)

### Added
- **Two more of Tim's labels: "Steps in place" and "Step back".** They show in the label list with the others.
  Labels made before this are not changed.

## v0.5.3 (Nov 17, 2026)

### Fixed
- **Skeleton and boxes were drawn beside the fencers, not on them,** after pausing a video. The paused
  picture was placed in the corner of the player instead of its proper place, while the skeleton and boxes
  were drawn in the proper place. It showed on wide screens and on videos with black bars around the picture
  (Tim's clips have them). The analysis itself was never wrong: only where the still picture was shown.

## v0.5.2 (Nov 17, 2026)

### Changed
- **The app folder can be renamed or moved** (for example to `C:\EnGarde`) without setting anything up again:
  videos and trained models are found inside the data folder even though the database remembers their old
  full path, and the launchers always use the Python inside the folder they are in.

## v0.5.1 (Nov 17, 2026)

### Added
- **Tim's 5 tagged bouts in the team workspace** (`team_add_tim_bouts.bat`): copies the 5 videos, their 164
  touches and the finished analysis from the benchmark folder (`data_tim`) into the team's `data` folder, and
  writes Tim's tags as labels under his ten label names. Nothing is analysed again; the benchmark folder is
  only read, so `compare_tim.bat` keeps working.
- Each label keeps the exact tag from Andre's file, for example `Tim tag: HALF_JUMP` (shown as the label
  version, and in every export), so the conversion below can be changed later without losing anything.

### How Tim's 24 tags map to his ten label names (601 labels, 567.8 s)
- **En garde** (71.5% of the time): EN_GARDE, HALF_JUMP, HALF_JUMP_DRIBBLE, APPEL, BACK_FOOT_HALF_JUMP
- **Advance → lunge** (11.7%): ADVANCE_LUNGE, DOUBLE_ADVANCE_LUNGE, ADVANCE_FLUNGE
- **Lunge** (8.6%): LUNGE, FLYING_LUNGE
- **Pull/retreat** (5.2%): PULL_ATTEMPT, PULL_PARRY, PULL_PARRY_RIPOSTE, JUMP_BACK, RECOVERING
- **Step forward** (2.4%): ADVANCE, ADVANCES, ADVANCE_APPEL, JUMP_FORWARD, STEP_FINISH
- **Reprise attack** (0.6%): REPRISE_LONG_ATTACK, REPRISE_PARRY
- No label (blade only, 2 tags in the file): ATTACK_OFF_LINE_PARRY, PARRY_ATTEMPT

### Assumptions to confirm with Tim
- Small in-place footwork (half jumps, appels: 25 tags) is filed under En garde, and RECOVERING (33 tags) under
  Pull/retreat. The table is `GOLD_TO_TEN` in `backend/engarde/tim_data.py`.

## v0.5.0 (Nov 17, 2026)

The app can now run on one PC for the whole team: everyone opens a link and signs in. See `docs/TEAM_SERVER.md`.

### Added
- **Team server** (`serve_team.bat`): the same app with sign-in switched on. `start.bat` still runs it the old
  way, for one person on one computer, with no sign-in.
- **Accounts and roles**: admin (everything, plus accounts, deleting videos, unlocking), member (upload, label,
  review, lock, train), viewer (look only). The **Team** page adds people, sets roles, switches sign-in off and
  sets new passwords.
- **Your name is your account.** Labels, reviews, locks and deletes are saved under the signed-in name whatever
  the page sends, so blind and model-assisted labels are tracked per person.
- **Ask to unlock**: a locked video can only be unlocked by the person who locked it or an admin. Anyone else
  sends a request with a reason; admins approve or refuse it on the Team page.
- **Home page**: version, what is new, where the work stands, how to work, last backup.
- **Automatic backups** of the database and a labels file every 6 hours (`data/backups`, newest 14 kept), with
  an optional second copy to another folder such as the NYU Drive.
- The PC does not go to sleep while the team server is running.
- **Launchers**: `team_link_on.bat` / `team_link_off.bat` (the https address through Tailscale),
  `team_autostart_on.bat` / `team_autostart_off.bat`, `team_accounts.bat` (accounts from the PC if nobody can
  sign in).
- 8 new tests (`backend/tests/test_team.py`); 67 in total, all passing.

### Changed
- In team mode only admins can delete videos, add a video from a path on the server, or read the activity log.

### Not in this version
- Upload from Google Drive or a URL; skeleton between touches; Tim's 164 touches in the team's training set.
- The team server is for a small trusted team behind a private link. It is not hardened for the open internet.

## v0.4.0 (Nov 17, 2026)

From the Nov team meeting list and the "Changes to Review" note written while labelling in blind mode.

### Fixed
- **Only the first video could be reviewed.** The Review button on a video card went back to the Videos page,
  and the Review link at the top always opened the first video in the list. Cause: the address
  `#/review?video=...` was read with the `?video=...` part as the page name. Every card now opens its own
  video, Review has a video picker, and the top link reopens the video you were last on.
- **Skeleton, lines and boxes only seemed to show when paused.** They were only loaded for the selected touch,
  and pressing play on a touch started 0.5 s before the end of its window and stopped. Now: play on a touch
  replays the whole touch; overlays are loaded for every analysed touch, so they show while the whole video
  plays ("Whole video" button); and overlays are drawn on the exact frame the browser is showing. Frames
  between touches have no skeleton because the pose model only runs on touch windows; the player says so.
- **A new touch could show "last job failed"** although nothing was wrong (two frame extractions collided).

### Changed
- **Label names are Tim's**: Start of touch, En garde, Step forward, Advance → lunge, Lunge, Between touches,
  Reprise attack, Pull/retreat, No touch, End of touch. Existing labels are renamed once (history kept, database
  copied first). Rule-baseline guesses use the same names. See `docs/SHARED_LABELS.md`.
- **Touches are listed and numbered in time order**, including touches added by hand later.
- **Fencer numbers are the same on every touch of a bout**: 1 = left fencer, 2 = right fencer, 3 and up = other
  people. Boxes on the video carry the number, the side and the fencer's name.
- **Training no longer waits for more data.** With one labelled bout a model is trained and scored by holding out
  touches of that bout (marked as optimistic). Insights > Models lists what the current labels support.
- CSV exports open correctly in Excel (the arrow in "Advance → lunge").

### Added
- **Delete video** (labels are saved to `data/trash/` first).
- **Import labels** from a teammate's export (bundle, or training-labels JSON / CSV), matched to the same video
  file and to existing touches. **Export labels** on each video card.
- **Use for: Training / Test** on each video; test videos are never trained on.
- **Lock / unlock** a video; a log of deletes, locks, imports (`/api/audit`).
- **Videos page**: totals (videos uploaded, bouts pending review, reviewed, touches with human labels), status
  on every card (Pending review / In progress / Reviewed), search by title or athlete, filters.
- **A note that explains "rule" and "to review"** (the (i) button above the touch list, and hover text).
- `/api/dashboard`: the same numbers as JSON, for the Platform team's dashboard to read.
- **GitHub**: `.gitignore` that keeps videos, databases and sponsor data out, `github_first_push.bat`,
  `update.bat` / `update.command`, `docs/GITHUB.md`. The version number shows in the app header.
- 12 new tests (`backend/tests/test_v040.py`); 59 in total, all passing.

### Not in this version
- **One shared, live database** (everyone sees the same labels at once) and moving the app from each laptop to
  one address. Needs a server and logins. Until then: code through GitHub, labels through Export / Import.
- **Approval flow for locked content, admin grant / revoke.** Needs logins. Lock is an honour system for now.
- **Upload from Google Drive or a URL.** Download the file and use Upload video.
- **The Platform team's dashboard** (the "MLOT / classification" fields) is not in this code base. It can read
  `/api/dashboard` and `/api/videos`.
- **Skeleton between touches** (pose on the whole video rather than on touch windows).
- **Tim's 164 tagged touches in the same training set as the team's labels.** They are still in their own
  folder with his 8 model classes (`docs/TIM_DATA.md`); merging needs his tag definitions.

### Assumptions to confirm
- "Other/Still" was renamed to "En garde". Tim's written definitions decide whether that holds.
- The ten names were taken from the team's note; spelling there is treated as Tim's.

## v0.3.0 (Nov 13, 2026)

### Added
- **Importer for Tim's tagged touches** (`scripts/import_tim_data.py`, `backend/engarde/tim_data.py`): reads the
  Model comparison page's `data.json` (164 touches, 5 sabre bouts), downloads the clips, loads one video per bout
  and Tim's gold footwork tags as annotations, runs our pipeline, and compares our models with his on the same
  touches. See `docs/TIM_DATA.md`.
- **Scorer matching Tim's page**: EXACT / PARTIAL (same family) / WRONG / NO READ on a 1/30 s grid. Re-scores
  Tim's own models next to his published numbers as a check.
- **Taxonomy preset `tim8`** (`{"preset": "tim8"}` in `data/taxonomy.json`): Tim's 8 footwork classes.
- **gpu_setup.bat**: switches PyTorch to the CUDA build so YOLO pose tracking runs on an NVIDIA GPU (tested target: RTX 5080), and checks it.
- **Launchers**: `tim_data.bat`, `start_tim.bat` (app on Tim's data at port 8002), `compare_tim.bat`, and Mac `.command` versions.
- **8 new tests** (`backend/tests/test_tim_data.py`); 47 in total, all passing.

### Assumptions to confirm
- The mapping from Tim's 24 gold tags to his 8 model classes is ours; it reproduces his XGBoost headline within
  about 2 points. Light frames for imported touches are approximate (clip end).

## v0.2.0 (Oct 25, 2026)

Builds on Alaia's v0.1.0. Nothing she built was taken away; the items under "Replaced" were rewritten or folded into the new code with the same behaviour.

### Added
- **E2 bidirectional TCN** (`backend/engarde/tcn.py`), as specified in PRD section 7.4: 6 non-causal dilated blocks, 127-frame receptive field, weighted cross-entropy plus T-MSE smoothing loss, early stopping on an inner bout.
- **One shared training protocol for E1 and E2** (`ml.run_protocol`): same leave-one-bout-out folds, same test-bout firewall (blind or adjudicated labels only), same temperature scaling and scoring. Out-of-fold predictions are saved per model.
- **Evaluation module** (`backend/engarde/evaluation.py`):
  - Calibration report (4 confidence bins, ECE, Brier, risk-coverage curve, over-confidence alarm, monotonicity rule, and a show/hide-confidence flag for coaches).
  - 95% intervals resampled by bout, flagged unreliable below 5 bouts.
  - Paired E1 vs E2 comparison with the PRD section 10.3 rule (adopt E2 only if the gain is at least 0.03 and its interval excludes 0).
  - Pooled confusion matrix.
- **Insights > Models panel**: frozen-test-bout picker, "Train XGBoost (E1)" and "Train TCN (E2)" buttons, CV and test Macro-F1, interval by bout, coverage, abstention, per-model details (warnings and calibration), and the E1 vs E2 decision.
- **API**: `POST /api/models/train` accepts `method` = `xgboost` or `tcn`; new `GET /api/models/compare?a=&b=` and `GET /api/bouts`.
- **Windows launchers**: `setup.bat`, `start.bat`, `link_videos.bat`, plus cross-platform `scripts/link_videos.py`. Not yet tested on a Windows machine.
- **One-click testing**: `demo.bat` / `demo.command` load a 5-bout synthetic demo with blind labels into a separate `demo_data` folder and open the app on it at http://127.0.0.1:8001 (your real `data` folder and the normal app at :8000 are untouched); `run_tests.bat` / `run_tests.command` run the automated tests. `scripts/demo_fixture.py` gained `--bouts N` and `--blind`.
- **11 new tests** (`backend/tests/test_models.py`); 39 in total, all passing.

### Changed
- Window features are vectorised: identical output, about 15x faster.
- Training-set building checks frame evidence once per sequence instead of once per frame for every frame (was quadratic per touch).
- Analysing a touch with a model of the wrong kind (for example an XGBoost id passed as TCN) is refused instead of silently used.
- Version number 0.1.0 to 0.2.0.

### Replaced (same behaviour, new code)
- The read-only Models card on Insights is replaced by the new Models panel.
- The per-frame loop in `window_features` is replaced by the vectorised version (a test checks both give the same numbers).
- The internal XGBoost helpers `_fit` / `_probs` are folded into an `XGBMethod` class. `ml.train`, `ml.build_dataset` and `ml.macro_f1` still work under their old names.

### Still not done
- No real labels yet, so no model has been trained or scored on real fencing video. All model numbers so far are from synthetic test data.
- TCN ablations (skeleton-only inputs, causal vs bidirectional, keypoint sets), piste calibration, event head, pose-model comparison, AIRS three-arm experiment.
