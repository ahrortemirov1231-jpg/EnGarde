#!/bin/bash
# Engarde Analytics: bring in Tim's tagged touches and run our pipeline on them. Safe to re-run.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "Run setup.command first."; read -r -p "Press Return to close"; exit 1; fi
source .venv/bin/activate
export PYTHONUTF8=1
python scripts/import_tim_data.py download && python scripts/import_tim_data.py check && \
python scripts/import_tim_data.py load && python scripts/import_tim_data.py analyze && \
echo "Done. Next: start_tim.command, Insights > Models, train with no frozen test bout, then compare_tim.command."
read -r -p "Press Return to close"
