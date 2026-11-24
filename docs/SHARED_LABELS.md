# Sharing labels, training vs test, locking (v0.4.0)

## The same labels for everyone

The app runs on each person's computer, so labels made on one computer are not on the others until they
are shared as a file.

**To share:** Videos page > on the video's card > **Export labels** (or Export > Full bundle for all videos).
Put the `.json` file in the team's NYU Drive folder.

**To receive:** Videos page > **Import labels** > choose the file.

- The video itself must already be uploaded on your computer, and it must be the **same file** (the app checks
  the file fingerprint, the frame count and the frame rate, and refuses a different video).
- Older exports work too: the Training labels JSON or CSV from v0.3.0. For those, pick the video by hand in
  the "Put the labels on" box, because those files do not carry the file fingerprint.
- If you both ran the automatic analysis, your touches are matched by their light frame (within 3 frames), so
  you do not get every touch twice.
- Nothing already on your computer is overwritten. If an incoming label would sit on top of one you have,
  it is skipped and listed, and a person decides.
- Importing the same file twice changes nothing.
- Blind labels stay blind; the annotator's name is kept.

One person (data owner: Mrunal) should export after each labelling session, so there is one current file per video.

Real-time sharing, where everyone sees a new label the moment it is made, needs the app to run on one shared
server with logins. That is not in this version. See "Not in this version" in `CHANGELOG.md`.

## Label names

The labels are Tim's: Start of touch, En garde, Step forward, Advance → lunge, Lunge, Between touches,
Reprise attack, Pull/retreat, No touch, End of touch. Two more stay available at the bottom of the list for
honesty about the footage: Unknown (visible, cannot decide) and Unobservable (not visible).

- The six movement labels are what a model learns. The four bout-phase labels (Start of touch, End of touch,
  Between touches, No touch) can be tagged but are never model classes.
- Labels made before v0.4.0 were renamed once, automatically: Advance > Step forward, Retreat > Pull/retreat,
  Other/Still > En garde, Lunge stays Lunge. Every rename is in the label's history, and a copy of the
  database from before the upgrade sits next to it as `engarde.sqlite.before-v0.4.0`.
- **Assumption to confirm with Tim:** "Other/Still" became "En garde". If Tim's "En garde" means only the
  starting stance, some of those labels need a second look. Filter: `label_version` contains "renamed from".
- To keep the old four labels in a data folder, put `{"preset": "mvp4"}` in `data/taxonomy.json`.

## Training videos and test videos

Each video card has **Use for**: Not decided, Training, or Test. A Test video is never trained on; the model
is scored on it once, on blind labels only. Insights > Models shows the same setting and uses it for every
training run. Decide the test videos before anyone looks at model output on them.

## Lock

**Lock** on a video card stops label, review and touch changes on that video (and stops it being deleted),
for example once a bout has been checked. The person who locked it can unlock it.

The app has no logins yet, so this is a guard against accidents, not security. To name admins who can unlock
anything, create `data/team.json`:

```json
{"admins": ["Ahror", "Mrunal"]}
```

Without that file anyone can unlock, and the name in the Annotator ID box is written to the log
(`/api/audit`). An approval step for editing locked content needs logins and is not in this version.

## Delete a video

**Delete** on a video card removes the video, its touches and all analysis from the app. Human labels are
saved first to `data/trash/` as a file that Import labels can read, so a wrong click does not lose work.
