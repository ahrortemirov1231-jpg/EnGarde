#!/bin/bash
# Engarde Analytics: get the latest version from GitHub. Close the app first.
# Your data folder, videos and labels are not touched: they are not stored in GitHub.
cd "$(dirname "$0")" || exit 1
if [ ! -d .git ]; then echo "This folder did not come from GitHub. See docs/GITHUB.md, 'Getting the code'."; read -r -p "Press Return to close"; exit 1; fi
if ! git pull --ff-only; then
  echo; echo "Could not update. Take a screenshot of the message above and send it to the model team."
  read -r -p "Press Return to close"; exit 1
fi
if [ -d .venv ]; then source .venv/bin/activate; python -m pip install -q -r requirements.txt; fi
echo; echo "Up to date. Start the app with start.command."
read -r -p "Press Return to close"
