# Using Tim's tagged touches (v0.3.0)

Tim's site has a **Model comparison** page (engardeanalytics.netlify.app/compare/). Its data file,
`compare/data.json` (built Nov 11, 2026), holds 164 tagged sabre touches from 5 bouts. Each touch has:
- a link to its video clip, where the clip starts in the bout, and how long it lasts;
- **gold footwork tags** for each fencer: start time, end time, label (24 labels, e.g. EN_GARDE, LUNGE,
  ADVANCE_LUNGE, HALF_JUMP, PULL_ATTEMPT);
- intent tags (e.g. PREPARATION_STEPS_INTENT);
- predictions from Tim's models (Existing AI, XGBoost, TCN, LightGBM), each scored on bouts it was not
  trained on.

Permission to use this data for the project was confirmed with Tim and Andre (Nov 12, 2026).

## Quick start (Windows)

0. **gpu_setup.bat** (once, after setup.bat): installs the NVIDIA/CUDA version of PyTorch and checks the
   GPU. Without it, Windows uses the CPU version and the pose step is much slower.
1. **tim_data.bat**: downloads the file and the 164 clips into `tim_data\`, checks the file, loads the
   touches into a separate data folder `data_tim\`, and runs our pipeline on every touch. Uses the GPU
   if PyTorch can see it. Safe to re-run; finished steps are skipped.
2. **start_tim.bat**: opens the app on Tim's data at http://127.0.0.1:8002. Go to **Insights > Models**
   and train XGBoost and TCN with **no frozen test bout**, so every bout gets a held-out prediction,
   the same way Tim's page scores its models.
3. **compare_tim.bat**: scores our rule baseline and our latest model against Tim's models on the same
   touches, same grid, same scorer. Reports go to `data_tim\reports\`.

Mac: the same steps as `.command` files. Manual: `python scripts/import_tim_data.py {check|download|load|analyze|compare}`.

## How the data is loaded

- One video per bout: the touch clips are resampled to 30 fps, padded to 1280x720 and joined end to
  end, so our leave-one-bout-out folds match Tim's.
- One touch per clip. Tim's file has no scoring-light time, so each touch's light frame is set to the
  clip's last frame and marked approximate in the touch notes.
- Gold tags become annotations in Tim's **8-class vocabulary** (taxonomy preset `tim8`):
  EN_GARDE, STEPS_IN_PLACE, STEPS_FORWARD, STEPS_BACK, BACK_FAMILY, ADVANCE_LUNGE, LUNGE, RECOVERING.
  Blade-only tags (REPRISE_PARRY etc.) are not footwork and are left unlabelled.
- Tags are recorded with `label_source=adjudicated` (expert final labels). Change with `--label-source`.
- Fencer surnames are used as approximate athlete ids, so the app can warn when the same fencer appears
  in training and test bouts (MORRILL, HEATHCOCK and others appear in several bouts).

## How scoring works

Every 1/30 s moment that has a gold tag is scored for each fencer:
- **EXACT**: same class as the gold tag (after mapping to the 8 classes);
- **PARTIAL**: same movement family (still / forward / lunge / back), different class;
- **WRONG**: different family, or a blade-only gold tag;
- **NO READ**: the model gave no label (or said "no call").

Our 4-class labels (Advance, Retreat, Lunge, Other/Still) are mapped to the nearest of the 8 classes,
so the rule baseline can be scored too.

**Assumption to confirm with Andre:** the mapping from Tim's 24 gold tags to the 8 classes, and the family
groups, are ours (`backend/engarde/tim_data.py`, `GOLD_TO_TIM8` and `FAMILY`). With them, our scorer
gives XGBoost about 80% exact / 10% partial / 10% wrong on all 5 bouts; Tim's page says 78.8 / 10.6 / 10.6.
`check` prints both side by side. Update the two tables when Andre sends the real definitions.

## Limits

- This is a **held-out-bout** comparison, not a sealed test: the gold tags were used to build Tim's
  models too. The sealed test needs bouts nobody has trained or tuned on (asked for in the team's email).
- 5 bouts is small: confidence intervals from the app are flagged unreliable below 5 bouts.
- Several tagged bouts include Y-12 / Y-14 fencers. Keep `tim_data\` and `data_tim\` on team devices
  only (both are in .gitignore) and do not send youth footage to outside AI services without Tim's approval.
