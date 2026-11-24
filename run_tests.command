#!/bin/bash
# Engarde Analytics: run the 69 automated tests (about 5 minutes). Close the app first.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
cd backend
echo 'Running tests... the last line should say "73 passed".'
python -m pytest -q -p no:cacheprovider
read -r -p "Done. Press Return to close"
