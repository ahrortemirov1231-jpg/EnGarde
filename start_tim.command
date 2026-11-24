#!/bin/bash
# Engarde Analytics on Tim's data (data_tim), at http://127.0.0.1:8002. Close this window to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
export ENGARDE_DATA="$PWD/data_tim"
( sleep 3; open "http://127.0.0.1:8002" ) &
python -W ignore -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8002
