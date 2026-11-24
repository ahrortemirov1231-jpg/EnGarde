"""Download USA Fencing's sabre bout videos from their YouTube channel into incoming/usafencing,
ready for Videos > Bulk import > Folder. Approved by USA Fencing (via Tim Morehouse) for this project.

    python scripts/fetch_usafencing.py            # up to 200 bouts
    python scripts/fetch_usafencing.py --max 350  # more
    python scripts/fetch_usafencing.py --list-only

Only sabre bouts are picked (title says Saber/Sabre, length 1.5 to 45 minutes; shows, interviews,
podcasts and highlights are skipped). Videos already downloaded are remembered in downloaded.txt,
so running it again only fetches new ones. Quality is capped at 720p to save disk space.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

# Most strip bouts are posted on the Live tab (one stream per bout), finals also on the Videos tab.
TABS = ["https://www.youtube.com/@USAFencing/streams", "https://www.youtube.com/@USAFencing/videos"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "incoming", "usafencing")

SABRE = re.compile(r"\bsab(er|re)s?\b", re.I)
NOT_A_BOUT = re.compile(r"fencing show|first to 15|interview|podcast|highlight|recap|behind the scenes|"
                        r"beginner|guide|clinic|lesson|drill|trailer|teaser|reaction|documentary|vlog|"
                        r"hall of fame|episode|\bep\.?\s*\d", re.I)
LOOKS_LIKE_BOUT = re.compile(r"\bvs?\.?\b|\bfinal|semi|quarter|table of|\bt\d{1,3}\b|\bde\b|pool|bout|gold|bronze|medal|^[a-z]\d{1,2}\s*-", re.I)


def is_sabre_bout(title: str, duration) -> bool:
    if not title or not SABRE.search(title) or NOT_A_BOUT.search(title):
        return False
    if not LOOKS_LIKE_BOUT.search(title):
        return False
    if duration is None:          # unknown length: keep, the download step will still get it
        return True
    return 90 <= float(duration) <= 45 * 60


def list_channel(tabs=TABS) -> list[dict]:
    import yt_dlp
    out, seen = [], set()
    with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "quiet": True, "skip_download": True,
                           "ignoreerrors": True}) as ydl:
        for url in tabs:
            info = ydl.extract_info(url, download=False)
            for e in (info or {}).get("entries") or []:
                if not e or not e.get("id") or e["id"] in seen:
                    continue
                if e.get("live_status") in ("is_upcoming", "is_live"):   # not finished yet
                    continue
                seen.add(e["id"])
                out.append({"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration"),
                            "url": f"https://www.youtube.com/watch?v={e['id']}"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200, help="most bouts to download this run (default 200)")
    ap.add_argument("--list-only", action="store_true", help="only write the list, download nothing")
    ap.add_argument("--cookies-from", default=None, help='browser to borrow a YouTube sign-in from: edge, firefox or chrome; a profile can follow, e.g. "edge:Profile 2"')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    print("Reading the USA Fencing channel (Live and Videos tabs, several thousand items, takes a few minutes)...")
    allv = list_channel()
    picks = [v for v in allv if is_sabre_bout(v["title"], v["duration"])]
    with open(os.path.join(OUT, "sabre_bouts_list.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "title", "duration", "url"])
        w.writeheader()
        w.writerows(picks)
    print(f"{len(allv)} videos on the channel, {len(picks)} look like sabre bouts. List: {os.path.join(OUT, 'sabre_bouts_list.csv')}")
    if a.list_only or not picks:
        return 0
    archive = os.path.join(OUT, "downloaded.txt")
    done = set()
    if os.path.exists(archive):
        done = {ln.split()[-1] for ln in open(archive, encoding="utf-8") if ln.strip()}
    todo = [v for v in picks if v["id"] not in done][: a.max]
    print(f"Downloading {len(todo)} bouts (720p max) into {OUT} ... this can take hours; you can leave it running.")
    import shutil
    import yt_dlp
    if not shutil.which("deno"):
        print("NOTE: Deno is not installed. YouTube needs it for most stream recordings (the Live tab).")
        print("      In PowerShell run:  winget install DenoLand.Deno   then close this window and run again.")
    errlog = os.path.join(OUT, "errors.log")

    class Blocked(Exception):
        pass

    class Log:
        def __init__(self):
            self.errors = 0
            self.blocked = 0
        def debug(self, msg):
            if not msg.startswith("[debug]"):
                print(msg)
        def info(self, msg):
            print(msg)
        def warning(self, msg):
            print("WARNING:", msg)
        def error(self, msg):
            self.errors += 1
            print(msg)
            with open(errlog, "a", encoding="utf-8") as f:
                f.write(msg.strip() + "\n")
            if "not a bot" in msg or "HTTP Error 429" in msg:
                self.blocked += 1
                if self.blocked >= 3:     # YouTube is blocking this connection: stop, do not dig deeper
                    raise Blocked()
            else:
                self.blocked = 0

    log = Log()
    before = len(done)
    opts = {
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(OUT, "%(title).150B [%(id)s].%(ext)s"),
        "download_archive": archive,
        "ignoreerrors": True, "windowsfilenames": True, "noplaylist": True,
        # slow and steady: YouTube blocks connections that request too fast
        "sleep_interval": 20, "max_sleep_interval": 45, "sleep_interval_requests": 2,
        "concurrent_fragment_downloads": 2,
        "retries": 5, "fragment_retries": 5, "quiet": False, "noprogress": False,
        "logger": log,
    }
    cookiefile = os.path.join(OUT, "cookies.txt")
    if a.cookies_from:
        browser, _, profile = a.cookies_from.partition(":")      # e.g. "edge" or "edge:Profile 2"
        opts["cookiesfrombrowser"] = (browser.strip().lower(), profile.strip() or None, None, None)
        print(f"Using the YouTube sign-in from {browser.strip()} ({profile.strip() or 'default profile'}). Close that browser first.")
    elif os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
    stopped = False
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([v["url"] for v in todo])
        except Blocked:
            stopped = True
        except Exception as e:  # noqa: BLE001
            if "cookie" in str(e).lower() or type(e).__name__ == "CookieLoadError":
                print("\nCOULD NOT READ THE BROWSER SIGN-IN.")
                print("The browser is probably still running (Edge keeps running in the background), or it locks")
                print("its sign-in file. Use Firefox instead: sign in to YouTube there with the spare account, close")
                print("Firefox, then double-click download_usafencing_firefox.bat.")
                return 1
            raise
    have = [f for f in os.listdir(OUT) if f.lower().endswith((".mp4", ".webm", ".mkv"))]
    now = sum(1 for ln in open(archive, encoding="utf-8") if ln.strip()) if os.path.exists(archive) else 0
    print(f"\nThis run: {now - before} downloaded, {log.errors} failed (reasons saved in {errlog}).")
    print(f"{len(have)} video files are in {OUT}.")
    if stopped:
        print("\nSTOPPED: YouTube is asking this connection to 'confirm you're not a bot'.")
        print("Wait a few hours (overnight is safest) and run again. Each run now goes slowly on purpose.")
        print("Nothing is lost: finished videos are kept and remembered.")
    print("Next: open the workspace > Videos > Bulk import > Folder on the host PC, enter that folder, Start import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
