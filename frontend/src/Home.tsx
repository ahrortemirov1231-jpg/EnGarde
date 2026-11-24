import { useEffect, useState } from "react";
import { About, api, Dashboard } from "./api";
import { useApp } from "./App";

/** Tiny renderer for the changelog: headings, bullets, **bold**, `code`. Nothing else is needed. */
function inline(s: string, key: number) {
  const parts = s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return <span key={key}>{parts.map((p, i) => p.startsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : p.startsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : p)}</span>;
}
function Changelog({ text }: { text: string }) {
  const out: JSX.Element[] = [];
  let list: string[] = [];
  const flush = () => { if (list.length) { out.push(<ul key={out.length}>{list.map((l, i) => <li key={i}>{inline(l, i)}</li>)}</ul>); list = []; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("## ")) { flush(); out.push(<h3 key={out.length}>{line.slice(3)}</h3>); }
    else if (line.startsWith("### ")) { flush(); out.push(<h4 key={out.length}>{line.slice(4)}</h4>); }
    else if (/^\s*- /.test(line)) list.push(line.replace(/^\s*- /, ""));
    else if (/^\s{2,}\S/.test(line) && list.length) list[list.length - 1] += " " + line.trim();
    else if (line.trim()) { flush(); out.push(<p key={out.length}>{inline(line, 0)}</p>); }
  }
  flush();
  return <div className="changelog">{out}</div>;
}

export default function Home() {
  const { toast, user, teamMode } = useApp();
  const [a, setA] = useState<About | null>(null);
  const [d, setD] = useState<Dashboard | null>(null);
  useEffect(() => { api.about().then(setA).catch((e) => toast(e.message, true)); api.dashboard().then(setD).catch(() => {}); }, [toast]);
  const b = a?.backup?.last;
  return (
    <main className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}>
          <h1>{user ? `Hello, ${user.name}` : "Home"}</h1>
          <p>{teamMode ? "This is the team's shared workspace. Everyone who signs in sees the same videos and labels, and always the latest version." : "This copy runs on this computer only. Start it with serve_team.bat to share it with the team."}</p>
        </div>
      </div>
      {d && (
        <section className="overview glass dash" aria-label="Where the work stands">
          <div><div className="bignum num">{d.videos_total}</div><div className="bignum-l">videos uploaded</div></div>
          <div><div className="bignum num">{d.videos_pending + d.videos_in_progress}</div><div className="bignum-l">bouts pending review</div></div>
          <div><div className="bignum num">{d.touches_labelled}<span className="of"> / {d.touches_total}</span></div><div className="bignum-l">touches with human labels</div></div>
          <div><div className="bignum num">{d.labels_total}</div><div className="bignum-l">labels made</div></div>
        </section>
      )}
      <div className="cards2">
        <section className="card panel">
          <h3>How to work here</h3>
          <ol className="howto">
            <li><b>Videos</b>: upload a bout, or pick one. The app finds the scoring lights and makes a first guess for every touch.</li>
            <li><b>Review</b>: open a touch, check the two fencers, then confirm, correct or draw labels on the timeline. Turn on Blind mode when the labels are for testing the model.</li>
            <li><b>Use for</b>: mark each video as Training or Test. Test videos are never trained on.</li>
            <li><b>Lock</b> a video when it has been checked. To change a locked video, use Ask to unlock.</li>
            <li><b>Insights</b>: counts, and model training and scores.</li>
          </ol>
          <p className="unavail">Label names are Tim's. The (i) button above the touch list explains “rule” and “to review”.</p>
        </section>
        <section className="card panel">
          <h3>This server</h3>
          <dl className="kv num">
            <dt>Version</dt><dd>{a ? `v${a.version}` : "…"}</dd>
            <dt>Running since</dt><dd>{a?.started_at ?? "…"}</dd>
            <dt>Mode</dt><dd>{teamMode ? "team server (sign-in required)" : "single computer"}</dd>
            {teamMode && <><dt>Last backup</dt><dd>{b ? b.at.replace("T", " ") : a?.backup?.error ? `failed: ${a.backup.error}` : "first one runs a minute after start"}</dd>
              <dt>Second copy</dt><dd>{a?.backup_dir ? (b?.copy_error ? `failed: ${b.copy_error}` : a.backup_dir) : "not set (see docs/TEAM_SERVER.md)"}</dd></>}
          </dl>
          <p className="unavail" style={{ marginTop: 10 }}>Videos and labels stay on the team's own computer. Nothing is sent to a third-party service.</p>
        </section>
      </div>
      <section className="card panel" style={{ marginTop: 20 }}>
        <h3>What is new</h3>
        {a ? <Changelog text={a.changelog} /> : null}
      </section>
    </main>
  );
}
