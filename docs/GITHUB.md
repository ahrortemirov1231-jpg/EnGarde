# GitHub: the one place for the code

GitHub holds the **code**. It does not hold videos, labels or anything Tim gave us.

| Goes in GitHub | Never goes in GitHub |
|---|---|
| `backend/`, `frontend/`, `scripts/`, `docs/`, the `.bat` and `.command` launchers | `data/`, `data_tim/`, `tim_data/`, `demo_data/`, `incoming/` (bulk-import and USA Fencing downloads) |
| `frontend/dist/` (the built website, so nobody needs Node to run the app) | any video file, any `.sqlite` database |
| `requirements.txt`, `CHANGELOG.md`, `README.md` | pose weights (`weights/*.pt`, downloaded by setup) |

`.gitignore` already enforces the right-hand column. Do not edit it to add data.

**Keep the repository private.** Reasons: the course confidentiality terms for sponsor material, the scripts
point at Tim's site, and Tim's tagged bouts include Y-12 and Y-14 fencers. Videos and label files travel on
the team's NYU Drive only.

## One time: put the code on GitHub (one person)

1. On github.com: **New repository**, name it (for example `engarde`), choose **Private**, do not add a README.
2. Install Git for Windows (https://git-scm.com/download/win).
3. Double-click `github_first_push.bat` and paste the repository address when asked. It refuses to upload if
   it finds a video, a database or a data folder in the list.
4. On github.com: repository > Settings > Collaborators > add the five teammates and Prof. Lee if he wants access.

`github_first_push.bat` was written for this release and has not been run on a Windows machine yet. If it
stops with an error, the same thing by hand is: `git init -b main`, `git add -A`, `git commit -m "Engarde MVP v0.5.6"`,
`git remote add origin <address>`, `git push -u origin main`.

## Getting the code (everyone else)

1. Install Git (Windows: link above; Mac: it installs itself the first time you type `git` in Terminal) or GitHub Desktop.
2. Clone the repository to a normal folder, not OneDrive or iCloud. Example: `C:\engarde` or `~/engarde`.
3. Run `setup.bat` (Windows) or `setup.command` (Mac) once, then `start.bat` / `start.command`.
4. Mac only, if a `.command` file will not open: in Terminal, inside the folder, run `chmod +x *.command` once.

## Getting an update

Close the app, then double-click `update.bat` (Windows) or `update.command` (Mac). It pulls the latest code
and installs anything new. Your `data` folder is untouched.

## Changing the code

- Work on a branch, open a pull request, have one other person look at it, then merge. `main` should always run.
- Run `run_tests.bat` / `run_tests.command` before you push. The last line should say `73 passed`.
- If you changed anything in `frontend/src`, run `npm install` once and `npm run build` in `frontend/`, and
  commit the new `frontend/dist` with your change. That keeps the website and the model code in step:
  whoever pulls gets a matching pair.
- Bump the version in `backend/engarde/api.py` (`VERSION`) and add a `CHANGELOG.md` entry for anything a
  teammate would notice. The version shows next to the EN GARDE name in the app, so everyone can see
  which version they are running.

## Note on licences

Pose tracking uses Ultralytics YOLO, which is AGPL-3.0. That is fine for a private course project. It matters
if the code is ever published or handed to Tim as a product: raise it before then.
