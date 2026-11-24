#!/bin/bash
# Engarde Analytics — one-time setup on macOS. Double-click this file (or run: bash setup.command).
cd "$(dirname "$0")" || exit 1
echo "== Engarde Analytics setup =="
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v $c >/dev/null 2>&1 && $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer is required. Install it with:  brew install python@3.12   then run this again."
  read -r -p "Press Return to close"; exit 1
fi
echo "Python: $($PY --version)"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffmpeg is required. Install it with:  brew install ffmpeg   then run this again."
  read -r -p "Press Return to close"; exit 1
fi
echo "ffmpeg: $(ffmpeg -version | head -1)"
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
export PYTHONUTF8=1
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt || { echo "pip install failed (see above)"; read -r -p "Press Return to close"; exit 1; }
mkdir -p weights data
if [ ! -s weights/yolo11n-pose.pt ]; then
  curl -L --fail -o weights/yolo11n-pose.pt https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt || { echo "weights download failed"; read -r -p "Press Return to close"; exit 1; }
fi
echo "weights sha256: $(shasum -a 256 weights/yolo11n-pose.pt | cut -d' ' -f1)  (expected 869e83fc...9319dc0)"
if [ ! -f frontend/dist/index.html ]; then
  if command -v npm >/dev/null 2>&1; then (cd frontend && npm install && npm run build); else echo "frontend/dist missing and npm not found"; fi
fi
echo ""
echo "Setup complete. Double-click start.command to open Engarde Analytics."
read -r -p "Press Return to close"
