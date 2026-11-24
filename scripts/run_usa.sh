#!/usr/bin/env bash
# USA vs Poland: verified import -> bout -> (proxy in parallel) -> light candidates -> per-touch pipeline
set -euo pipefail
cd "$(dirname "$0")/.."
P=/mnt/user-data/uploads/Downloads/engarde_work/usa_parts
python3 -W ignore scripts/import_reassembled.py --data data --no-proxy \
  --parts $P/part_00.webm $P/part_01.webm $P/part_02.webm $P/part_03.webm $P/part_04.webm \
  --framemd5 /mnt/user-data/uploads/Downloads/engarde_work/usa_original_video.framemd5 \
  --title "USA vs Poland — men's team sabre" --original-name USA-vs-Poland-mens-team-sabre.webm | tee data/reports/usa_import.json
V=$(python3 -c "import json;print(json.load(open('data/reports/usa_import.json'))['video_id'])")
python3 -W ignore -c "
import sys; sys.path.insert(0,'backend')
from engarde.store import Store
s=Store('data')
b=s.add_bout('$V','USA vs POL — team relay (all legs)', left={'athlete_id':None,'display_name':'USA (team; athlete varies by leg)'}, right={'athlete_id':None,'display_name':'POL (team; athlete varies by leg)'})
print('bout', b['bout_id'])"
( python3 -W ignore -c "
import sys, os; sys.path.insert(0,'backend')
from engarde.store import Store
from engarde import video as vid
s=Store('data'); v=s.get_video('$V'); vid.make_browser_proxy(v['path'], os.path.join(s.video_dir('$V'),'proxy.mp4')); print('proxy done')
" > data/reports/usa_proxy.log 2>&1 & )
python3 -W ignore scripts/process_video.py --data data --video $V --from 0 --chunk 300 --report data/reports/usa_full.json
