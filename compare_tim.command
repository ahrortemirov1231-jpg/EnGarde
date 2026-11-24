#!/bin/bash
# Compare our rule baseline and latest trained model with Tim's models on the same touches.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
python scripts/import_tim_data.py compare --model rule
python scripts/import_tim_data.py compare --model latest
read -r -p "Press Return to close"
