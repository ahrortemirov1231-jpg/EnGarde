#!/bin/bash
# Link an imported analysis (data/ANALYSIS_MANIFEST.json) to the original videos on this Mac.
# Looks for each original file in ~/Downloads (or asks), verifies it is the same video, then links it.
cd "$(dirname "$0")" || exit 1
source .venv/bin/activate || { echo "Run setup.command first."; read -r -p "Press Return"; exit 1; }
export PYTHONUTF8=1
mkdir -p data
if [ ! -f data/ANALYSIS_MANIFEST.json ]; then
  for z in ./engarde-analysis*.zip ../engarde-analysis*.zip ~/Downloads/engarde-analysis*.zip ~/Downloads/engarde_work/engarde-analysis*.zip; do
    [ -f "$z" ] && { echo "unpacking $z"; unzip -oq "$z" -d data; }
  done
fi
if [ ! -f data/ANALYSIS_MANIFEST.json ]; then echo "No analysis found. Put engarde-analysis-part*.zip next to this file (or in ~/Downloads/engarde_work)."; read -r -p "Press Return"; exit 1; fi
python - <<'PY'
import json, os, subprocess, sys
m = json.load(open("data/ANALYSIS_MANIFEST.json"))
for v in m["videos"]:
    cand = os.path.expanduser(os.path.join("~/Downloads", v["original_file"]))
    if not os.path.exists(cand):
        cand = input(f'Path to "{v["original_file"]}" ({v["title"]}): ').strip().strip("'\"")
    print(f'\n== {v["title"]} ==')
    r = subprocess.run([sys.executable, "-W", "ignore", "scripts/relink_video.py", "--data", "data", "--video", v["video_id"], "--file", cand])
    print("OK" if r.returncode == 0 else "NOT LINKED (see message above)")
PY
echo ""; echo "Done. Start the app with start.command."
read -r -p "Press Return to close"
