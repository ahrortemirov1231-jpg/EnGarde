# Team server: one PC runs the app, the team opens a link (v0.5.0)

One Windows PC (the one with the RTX 5080) runs the app. Everyone else opens a web address and signs in.
Nobody else installs the app, everyone is on the same version, and everyone sees the same videos and labels.

The link is made with Tailscale, a free private network. Only people you share the PC with can reach it.
Videos travel encrypted from the PC straight to the teammate's browser and are not stored anywhere else.

Time needed: about 30 minutes the first time.

## Part 1. Update the app (host PC, once)

1. Close the app if it is running (close the black window).
2. Unzip `engarde-mvp-v0.5.0.zip`.
3. Open the unzipped `engarde-mvp-v0.5.0` folder, select everything inside it, copy, and paste it into
   `C:\EnGarde`. Choose **Replace the files in the destination**.
   Your `data`, `data_tim`, `weights` and `.venv` folders are not in the zip, so they stay as they are.

Nothing needs to be installed again.

## Part 2. Install Tailscale (host PC, once)

1. Go to https://tailscale.com/download and install Tailscale for Windows.
2. Sign in when it asks. **Use a personal Google, Microsoft, GitHub or Apple account, not your NYU email.**
   A school address can place you in a network run by the school's domain, where you are not the owner and
   cannot share the PC.
3. Open https://login.tailscale.com/admin/machines . You should see this PC in the list.
4. On this PC's row, open the `...` menu:
   - **Edit machine name**: change it to `engarde`. The name becomes part of the team's link.
   - **Disable key expiry**: otherwise Tailscale signs the PC out after some months and the link stops.

## Part 3. Start the team server (host PC)

1. In `C:\EnGarde`, double-click **`serve_team.bat`**.
2. The first time, it asks for **your name and a password**. This creates the first admin account.
   Use the name you want on your labels (for example `Ahror`). Nothing shows while you type the password.
3. Leave the black window open. It says `TEAM SERVER is running`.
4. Check it on the PC itself: open http://127.0.0.1:8000 and sign in.

From now on use `serve_team.bat` instead of `start.bat`. Both use the same `data` folder, so everything you
already have is there. They cannot run at the same time.

## Part 4. Turn on the team link (host PC, once)

1. Double-click **`team_link_on.bat`**.
2. It prints an address like `https://engarde.tail1234.ts.net`. That is the team's link. Copy it.
3. Open the link on the PC to check it. The first time, the page can take up to a minute while the
   security certificate is made.

The link stays on after a restart. `team_link_off.bat` turns it off.

**The link does not appear.** Open https://login.tailscale.com/admin/dns and check that **MagicDNS** is on and
that **HTTPS Certificates** is enabled (both are on by default for new accounts), then run `team_link_on.bat` again.

## Part 5. Let each teammate in (host PC, once per person)

There are two locks: Tailscale decides who can reach the PC, and the app's sign-in decides who they are.

**Tailscale access**
1. https://login.tailscale.com/admin/machines > the `engarde` row > `...` > **Share**.
2. **Copy invite link** > turn on **Reusable link** > **Copy share link**.
3. Send that link to the five teammates in a private message. Treat it like a password.

Sharing the machine (rather than inviting people into your network) means teammates can reach this one PC and
nothing else of yours, and they do not use up the six users of the free plan.

**App accounts**
1. On the team link, sign in and open the **Team** page.
2. Under **Add a person**, enter the teammate's name, a first password (8 or more characters), and the role:
   - `member`: upload, label, review, lock, train. Use this for the team.
   - `admin`: also add people, delete videos, unlock anything. Give this to one more person as a spare.
   - `viewer`: look only. Use this for Prof. Lee or Tim if they want to see it.
3. Send each person their name and first password privately. They change it on the Team page.

## Part 6. What each teammate does (once)

Send them `docs/TEAMMATE_QUICKSTART.md`, or this:

1. Install Tailscale from https://tailscale.com/download (Windows, Mac, iPhone and Android all work).
2. Sign in with a **personal** Google, Microsoft, GitHub or Apple account, not the NYU email.
3. Open the share link you were sent and choose **Accept**.
4. Open the team link, for example `https://engarde.tail1234.ts.net`, and sign in with the name and
   password you were given. Bookmark it.
5. Change your password on the **Team** page.

After that: make sure Tailscale is on (the icon in the menu bar or system tray), open the bookmark.

## Part 7. Keep it running

- **Start by itself:** run `team_autostart_on.bat` once. The server then starts, minimised, every time you sign
  in to Windows. `team_autostart_off.bat` undoes it.
- **Sleep:** while the server window is open, the PC does not go to sleep. The screen may turn off; that is fine.
  Do not shut the PC down or close the window when the team is working.
- **Windows Update restarts:** after a restart the PC waits at the sign-in screen and the site is down until
  you sign in to Windows. In Settings > Windows Update > Advanced options, set **Active hours** to the team's
  working hours so restarts happen outside them.
- **When it is off:** teammates see "This site can't be reached". Nothing is lost. Tell the group chat when
  you take it down on purpose.
- **Heavy use of the PC** (games, other GPU work) slows the site and the analysis for everyone.

## Part 8. Backups

All the team's work is now on one PC, so copies matter.

- **Automatic:** one minute after the server starts and every 6 hours, the app writes the database and a labels
  file to `C:\EnGarde\data\backups`. The newest 14 days are kept. The Home page shows the last time.
- **Second copy off the PC (do this):** install Google Drive for desktop with the account that has the team's
  NYU Drive, make a folder there, and tell the app about it by creating `data\team.json`:

  ```json
  {"backup_dir": "G:\\My Drive\\Capstone ML4\\engarde-backups"}
  ```

  Use your real folder path, with every `\` written twice. Restart the server. The Home page then shows the
  folder next to "Second copy".
- **Videos are not backed up.** They are large and can be uploaded again. Keep the original files.
- **To restore:** stop the server, copy a backup `engarde_YYYY-MM-DD.sqlite` over `data\engarde.sqlite`, start again.

## Part 9. Updating the app later

1. Tell the team, then close the server window.
2. Update the files (copy a new zip over the folder, or `update.bat` once the folder comes from GitHub).
3. Start `serve_team.bat`. Everyone has the new version the next time they load the page. The version number
   is next to EN GARDE at the top left, and the Home page lists what changed.

## Tim's 5 tagged bouts on the team site

The 5 bouts with 164 touches from Andre's file live in the benchmark folder `data_tim`, not in the team's
`data` folder. To put them on the team site: close the server window, double-click **`team_add_tim_bouts.bat`**,
then start `serve_team.bat`. It takes a minute or two and analyses nothing again. The bouts appear on the Videos
page as "Tim rec-...", every touch labelled with Tim's tags under his ten label names. Decide per video whether
it is for Training or Test.

## Move the team server out of OneDrive

If the app folder is inside OneDrive (for example on the Desktop), move the server to `C:\EnGarde`:
OneDrive uploads everything in the folder, videos included, to Microsoft's cloud, and it can lock the database
while it syncs. `C:\EnGarde` is also the copy that was set up for the RTX 5080.

1. Close the server window.
2. Copy the whole `data` folder from the OneDrive copy into `C:\EnGarde` and choose Replace
   (accounts and everything else are in that folder).
3. From now on start `serve_team.bat` in `C:\EnGarde`. The team's link does not change.
4. If you turned autostart on, run `team_autostart_on.bat` once more, from `C:\EnGarde`.

## If something goes wrong

| What you see | What to do |
|---|---|
| Teammate: "This site can't be reached" | Is the server window open on the host PC? Is Tailscale on at both ends? Did they accept the share link? |
| Teammate: sign-in page loads, password refused | Team page > **New password** for that person. Five wrong tries lock that name for one minute. |
| You forgot the admin password | On the host PC, open a command window in the folder and run `team_accounts.bat reset YourName`. |
| `serve_team.bat` says port 8000 is in use | Another Engarde window is open (`start.bat` or a second server). Close it. |
| Video plays in short bursts for teammates | Your home upload speed is the limit. Wired network instead of Wi-Fi on the host PC helps most. |
| The link stopped working after months | Tailscale key expiry was not disabled (Part 2, step 4). Sign in to Tailscale on the PC again. |
| A teammate left the project | Team page: switch off "Sign-in allowed". In Tailscale, Machines > `engarde` > Share: remove them. |

## What this does and does not protect

- Only people you shared the PC with in Tailscale can reach the site at all, and each still needs an app account.
- Passwords are stored as salted hashes. The app listens only on the PC itself (127.0.0.1); Tailscale carries
  the traffic, encrypted, to teammates.
- Every label, review, lock, unlock and delete is saved under the signed-in name, and admins can read the log
  (`/api/audit`).
- This is a course-project server, not a hardened product. Do not share the link or the share invite outside
  the team, and remove people who leave.
- Tim's bouts with Y-12 and Y-14 fencers stay on a team device with this setup. Teammates should not download
  or re-share those videos.

Checked against Tailscale's documentation on Nov 17, 2026: the free Personal plan allows 6 users, shared-device
recipients do not count toward that limit, and `tailscale serve --bg` survives restarts. The batch files in
this guide were written for this release and have not been run on a Windows PC yet; if one fails, the commands
they run are short and are listed below.

```
serve_team.bat        set ENGARDE_TEAM=1, set ENGARDE_DATA=<folder>\data, then:
                      python -m uvicorn engarde.api:app --app-dir backend --host 127.0.0.1 --port 8000
team_link_on.bat      tailscale serve --bg 8000        then        tailscale serve status
team_link_off.bat     tailscale serve reset
```
