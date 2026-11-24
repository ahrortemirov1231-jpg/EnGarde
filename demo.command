#!/bin/bash
# Engarde Analytics: load a SYNTHETIC demo (5 bouts of stick figures with labels) and open the app on it.
# Uses a separate demo_data folder, so your real data/ folder is never touched. Close this window to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
rm -rf demo_data
echo "Loading 5 synthetic bouts with blind labels. This takes about 1-2 minutes..."
python -W ignore scripts/demo_fixture.py --data demo_data --with-labels --bouts 5 --blind > demo_data_load.json || { echo "demo load failed"; read -r -p "Press Return to close"; exit 1; }
export ENGARDE_DATA="$PWD/demo_data"
( sleep 3; open "http://127.0.0.1:8001" ) &
echo "DEMO running at http://127.0.0.1:8001 on SYNTHETIC data. Keep this window open; close it to stop."
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8001
