#!/bin/bash
# Engarde Analytics — start the local app and open it in the browser. Close this window to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
export ENGARDE_DATA="$PWD/data"
( sleep 3; open "http://127.0.0.1:8000" ) &
echo "Engarde Analytics is running at http://127.0.0.1:8000 — keep this window open; close it to stop."
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8000
