"""Cross-platform version of link_videos.command: link data/ANALYSIS_MANIFEST.json to the original videos.
Looks for each original file in ~/Downloads, otherwise asks for its path, then verifies and links it."""
import json
import os
import subprocess
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(root)
man = os.path.join("data", "ANALYSIS_MANIFEST.json")
if not os.path.exists(man):
    sys.exit("No analysis found: put the analysis files into data/ first.")
for v in json.load(open(man))["videos"]:
    cand = os.path.join(os.path.expanduser("~"), "Downloads", v["original_file"])
    if not os.path.exists(cand):
        cand = input(f'Path to "{v["original_file"]}" ({v["title"]}): ').strip().strip("'\"")
    print(f'\n== {v["title"]} ==')
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join("scripts", "relink_video.py"),
                        "--data", "data", "--video", v["video_id"], "--file", cand])
    print("OK" if r.returncode == 0 else "NOT LINKED (see message above)")
print("\nDone. Start the app with start.bat (Windows) or start.command (Mac).")
