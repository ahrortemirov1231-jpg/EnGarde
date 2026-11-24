import { useEffect, useMemo, useRef, useState } from "react";
import { Upload, Play, Users, Cpu, Trash2, Lock, LockOpen, Download, FileUp, Search, FolderInput } from "lucide-react";
import { api, BulkJob, Dashboard, fmtClock, ImportReport, Video } from "./api";
import { useApp } from "./App";

const STATUS: Record<string, [string, string, string]> = {
  // key: [chip text, chip class, explanation]
  reviewed: ["Reviewed", "ok", "Every touch in this video has at least one human label."],
  in_progress: ["In progress", "warn", "Some touches have human labels, some do not yet."],
  pending: ["Pending review", "todo", "Touches were found, but nobody has labelled them yet."],
  no_touches: ["No touches yet", "muted", "No touch has been marked or found in this video."],
};

export default function Videos() {
  const { toast, annotator, user, teamMode } = useApp();
  const canDelete = !teamMode || user?.role === "admin";
  const readOnly = teamMode && user?.role === "viewer";
  const [ask, setAsk] = useState<Video | null>(null);
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [boutFor, setBoutFor] = useState<Video | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [del, setDel] = useState<Video | null>(null);
  const [q, setQ] = useState(""), [fStatus, setFStatus] = useState("all"), [fSplit, setFSplit] = useState("all");
  // v0.5.6: never start a refresh while the last one is still running, and pause while the tab is hidden,
  // so open tabs cannot pile requests up on the host PC.
  const busyRef = useRef(false);
  const load = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const [v, d] = await Promise.all([api.videos(), api.dashboard().catch(() => null)]);
      setVideos(v); if (d) setDash(d);
    } catch (e: any) { toast(e.message, true); }
    busyRef.current = false;
  };
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 8000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line

  const act = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); toast(ok); load(); } catch (e: any) { toast(e.message, true); } };
  const analyze = (v: Video) => act(() => api.analyzeVideo(v.video_id), "whole-video analysis queued");
  const anyRunning = !!videos?.some((v) => v.job?.status === "running");

  const shown = useMemo(() => (videos ?? []).filter((v) => {
    const names = (v.bouts ?? []).flatMap((b) => b.fencers.map((f) => `${f.display_name ?? ""} ${f.athlete_id ?? ""}`)).join(" ");
    if (q && !`${v.title} ${names}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (fStatus !== "all" && v.review_status !== fStatus) return false;
    if (fSplit !== "all" && (v.split ?? "none") !== fSplit) return false;
    return true;
  }), [videos, q, fStatus, fSplit]);

  return (
    <main className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}>
          <h1>Videos</h1>
          <p>Bout recordings processed on this computer. Nothing is sent to a third-party service.</p>
        </div>
        {canDelete && <button className="btn" onClick={() => setBulkOpen(true)} title="Add many videos at once from a folder on the host PC or a list of links"><FolderInput size={15} /> Bulk import</button>}
        <button className="btn" onClick={() => setImportOpen(true)} title="Bring in labels a teammate exported from the same video"><FileUp size={15} /> Import labels</button>
        <button className="btn primary" onClick={() => setUploadOpen(true)}><Upload size={15} /> Upload video</button>
      </div>

      {dash && dash.videos_total > 0 && (
        <section className="overview glass dash" aria-label="Summary">
          <div><div className="bignum num">{dash.videos_total}</div><div className="bignum-l">videos uploaded</div></div>
          <div><div className="bignum num">{dash.videos_pending + dash.videos_in_progress}</div><div className="bignum-l">bouts pending review</div></div>
          <div><div className="bignum num">{dash.videos_reviewed}</div><div className="bignum-l">bouts reviewed</div></div>
          <div><div className="bignum num">{dash.touches_labelled}<span className="of"> / {dash.touches_total}</span></div><div className="bignum-l">touches with human labels</div></div>
          <div><div className="bignum num">{dash.videos_train}<span className="of"> / {dash.videos_test}</span></div><div className="bignum-l">training / test videos</div></div>
        </section>
      )}

      {videos && videos.length > 0 && (
        <div className="filters">
          <label className="searchbox"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title or athlete" aria-label="Search title or athlete" /></label>
          <select className="field" value={fStatus} onChange={(e) => setFStatus(e.target.value)} aria-label="Filter by review status">
            <option value="all">All statuses</option><option value="pending">Pending review</option><option value="in_progress">In progress</option>
            <option value="reviewed">Reviewed</option><option value="no_touches">No touches yet</option>
          </select>
          <select className="field" value={fSplit} onChange={(e) => setFSplit(e.target.value)} aria-label="Filter by training or test">
            <option value="all">Training and test</option><option value="train">Training only</option><option value="test">Test only</option><option value="none">Not decided</option>
          </select>
          <span className="count num">{shown.length} of {videos.length}</span>
        </div>
      )}

      {videos && videos.length === 0 && (
        <div className="empty panel"><h3>No videos yet</h3><p>Upload a bout recording to start marking touches.</p></div>
      )}
      {videos && videos.length > 0 && shown.length === 0 && (
        <div className="empty panel"><h3>No video matches these filters</h3><p>Clear the search box or set the filters back to All.</p></div>
      )}
      <div className="vgrid">
        {shown.map((v) => {
          const bout = v.bouts?.[0];
          const L = bout?.fencers.find((f) => f.side === "left"), R = bout?.fencers.find((f) => f.side === "right");
          const indexing = !v.indexed;
          const j = v.job;
          const busy = j && (j.status === "queued" || j.status === "running");
          const [chip, chipCls, chipWhy] = STATUS[v.review_status ?? "no_touches"];
          return (
            <article key={v.video_id} className="vcard panel">
              <div className="vposter" style={{ backgroundImage: v.indexed && v.linked !== false ? `url(/api/videos/${v.video_id}/poster.jpg)` : undefined }}>
                {v.linked === false && <span className="badge warn" style={{ top: "auto", bottom: 12 }}>video file not linked · run link_videos</span>}
                {v.is_synthetic ? <span className="badge synthetic">SYNTHETIC FIXTURE</span> : null}
                <span className={`chip st-${chipCls}`} title={chipWhy}>{chip}</span>
                {v.locked ? <span className="chip st-lock" title={`Locked by ${v.locked_by ?? "someone"}: labels cannot change`}><Lock size={12} /> Locked</span> : null}
              </div>
              <div className="vbody">
                <h3>{v.title}</h3>
                {bout ? (
                  <div className="vfencers">
                    <span><span className="sidechip left" style={{ height: 20, fontSize: 11 }}>1 L</span> {L?.display_name || L?.athlete_id || "—"}</span>
                    <span>{R?.display_name || R?.athlete_id || "—"} <span className="sidechip right" style={{ height: 20, fontSize: 11 }}>2 R</span></span>
                  </div>
                ) : <span style={{ fontSize: 13, color: "var(--ink-3)" }}>No bout / athletes set</span>}
                <div className="vmeta num">
                  <span>{v.duration_s ? fmtClock(v.duration_s) : "—"}</span>
                  <span>{v.fps_measured ? `${v.fps_measured.toFixed(2)} fps${v.vfr ? " (VFR)" : ""}` : ""}</span>
                  <span><b>{v.n_touches ?? 0}</b> touches</span>
                  <span title="Touches that have at least one human label"><b>{v.n_labelled_touches ?? 0}</b> labelled</span>
                  <span title="Computer guesses that no person has confirmed or corrected yet"><b>{v.pending_review ?? 0}</b> to review</span>
                </div>
                {j && (busy ? (
                  <div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginBottom: 6 }}>
                      {j.status === "queued" ? (anyRunning ? "Waiting in line: another video is being analysed first. It starts by itself." : "queued") : `${j.stage ?? "working"}${j.message ? " · " + j.message : ""}`}
                    </div>
                    <div className="progress"><i style={{ width: `${Math.round((j.progress || 0) * 100)}%` }} /></div>
                  </div>
                ) : j.status === "failed" ? (
                  <div className="issue danger">Analysis failed: {j.message}</div>
                ) : j.kind === "auto_analyze" ? (
                  <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>Automatic analysis complete. Light frames are candidates: verify them in Review.</div>
                ) : null)}
                <div className="vsplit">
                  <label htmlFor={`sp-${v.video_id}`}>Use for</label>
                  <select id={`sp-${v.video_id}`} className="field" value={v.split ?? ""} title="A video is used either to train the model or to test it, never both."
                    onChange={(e) => act(() => api.patchVideo(v.video_id, { split: e.target.value || null, actor: annotator }), "saved")}>
                    <option value="">Not decided</option><option value="train">Training</option><option value="test">Test (never trained on)</option>
                  </select>
                </div>
                <div className="vactions">
                  <a className="btn primary sm" href={`#/review?video=${v.video_id}`} aria-disabled={indexing} style={indexing ? { pointerEvents: "none", opacity: .5 } : undefined}><Play size={14} /> Review</a>
                  {!bout && <button className="btn sm" onClick={() => setBoutFor(v)} disabled={indexing}><Users size={14} /> Set athletes</button>}
                  <button className="btn sm" onClick={() => analyze(v)} disabled={!!busy} title="Find scoring lights and analyse every touch that has no analysis yet"><Cpu size={14} /> Analyse whole video</button>
                </div>
                <div className="vtools">
                  <a className="btn sm ghost" href={`/api/export/bundle.json?video_id=${v.video_id}`} download title="Save this video's labels as a file to share with the team"><Download size={14} /> Export labels</a>
                  {teamMode && v.locked && user?.role !== "admin" && v.locked_by !== user?.name ? (
                    <button className="btn sm ghost" disabled={readOnly} title={`Locked by ${v.locked_by}. Send an admin a request to unlock it.`} aria-label="Ask to unlock video" onClick={() => setAsk(v)}><LockOpen size={14} /> Ask to unlock</button>
                  ) : (
                    <button className="btn sm ghost" disabled={readOnly} title={v.locked ? `Locked by ${v.locked_by}. Click to unlock.` : "Stop labels on this video from being changed"} aria-label={v.locked ? "Unlock video" : "Lock video"}
                      onClick={() => act(() => api.patchVideo(v.video_id, { locked: !v.locked, actor: annotator }), v.locked ? "unlocked" : `locked by ${annotator}`)}>
                      {v.locked ? <><LockOpen size={14} /> Unlock</> : <><Lock size={14} /> Lock</>}</button>
                  )}
                  <span style={{ flex: 1 }} />
                  {canDelete && <button className="btn sm ghost danger" aria-label="Delete this video" onClick={() => setDel(v)}><Trash2 size={14} /> Delete</button>}
                </div>
              </div>
            </article>
          );
        })}
      </div>
      {boutFor && <BoutModal v={boutFor} onClose={() => { setBoutFor(null); load(); }} />}
      {uploadOpen && <UploadModal onClose={() => { setUploadOpen(false); load(); }} />}
      {importOpen && <ImportModal videos={videos ?? []} onClose={() => { setImportOpen(false); load(); }} />}
      {bulkOpen && <BulkModal onClose={() => { setBulkOpen(false); load(); }} />}
      {del && <DeleteModal v={del} onClose={() => { setDel(null); load(); }} />}
      {ask && <AskUnlockModal v={ask} onClose={() => { setAsk(null); load(); }} />}
    </main>
  );
}

function AskUnlockModal({ v, onClose }: { v: Video; onClose: () => void }) {
  const { toast } = useApp();
  const [reason, setReason] = useState("");
  const go = async () => {
    try { await api.requestUnlock(v.video_id, reason); toast("request sent · an admin sees it on the Team page"); onClose(); }
    catch (e: any) { toast(e.message, true); }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Ask to unlock">
        <h3>Ask to unlock “{v.title}”</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>It was locked by {v.locked_by ?? "someone"}, so its labels cannot change. An admin can unlock it. Say what needs changing.</p>
        <div><label className="lab" htmlFor="ur">What needs to change?</label><input id="ur" className="field" autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: touch 3 has the wrong light frame" /></div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={go} disabled={!reason.trim()}>Send request</button>
        </div>
      </div>
    </div>
  );
}

function DeleteModal({ v, onClose }: { v: Video; onClose: () => void }) {
  const { toast, annotator, setAnnotator, teamMode } = useApp();
  const [busy, setBusy] = useState(false);
  const hasWork = (v.n_labels ?? 0) > 0;
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.deleteVideo(v.video_id, annotator);
      toast(`deleted "${v.title}"${r.backup ? " · its labels were saved in the data/trash folder" : ""}`);
      if (r.leftover_folders.length) toast("Some files are still open in the browser and stay on disk until the app restarts.", true);
      onClose();
    } catch (e: any) { toast(e.message, true); setBusy(false); }
  };
  return (
    <div className="modal-back" onClick={busy ? undefined : onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Delete video">
        <h3>Delete “{v.title}”?</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
          This removes the video file from this app, its {v.n_touches ?? 0} touches and all analysis made from it.
          {hasWork ? ` It also removes ${v.n_labels} human labels. They are saved first as a file in the data/trash folder, and can be imported again with Import labels.` : " It has no human labels."}
        </p>
        {v.locked ? <div className="issue danger">This video is locked by {v.locked_by}. Unlock it first.</div> : null}
        <div><label className="lab" htmlFor="dn">Your name (kept in the log)</label><input id="dn" className="field" value={annotator} disabled={teamMode} onChange={(e) => setAnnotator(e.target.value.trim())} /></div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose} disabled={busy}>Keep video</button>
          <button className="btn danger" onClick={go} disabled={busy || !annotator || !!v.locked}><Trash2 size={15} /> {busy ? "Deleting…" : "Delete video"}</button>
        </div>
      </div>
    </div>
  );
}

function ImportModal({ videos, onClose }: { videos: Video[]; onClose: () => void }) {
  const { toast, annotator } = useApp();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [rep, setRep] = useState<ImportReport | null>(null);
  const go = async () => {
    if (!file) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file); fd.append("actor", annotator);
    if (target) fd.append("target_video_id", target);
    try { setRep(await api.importLabels(fd)); } catch (e: any) { toast(e.message, true); }
    setBusy(false);
  };
  return (
    <div className="modal-back" onClick={busy ? undefined : onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Import labels">
        <h3>Import labels from a teammate</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
          Use a file made with Export (Full bundle, or Training labels as JSON or CSV). The same video file must already be uploaded here.
          Labels already on this computer are never overwritten; anything that clashes is listed for you to decide.
        </p>
        <input ref={fileRef} type="file" accept=".json,.csv" hidden onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRep(null); }} />
        <button className="btn" onClick={() => fileRef.current?.click()}><FileUp size={15} /> {file ? file.name : "Choose labels file"}</button>
        <div><label className="lab" htmlFor="it">Put the labels on</label>
          <select id="it" className="field" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Find the video automatically (same file)</option>
            {videos.map((v) => <option key={v.video_id} value={v.video_id}>{v.title}</option>)}
          </select></div>
        {rep && (
          <div className="import-report">
            <div className={`issue ${rep.labels_imported ? "ok" : ""}`}>
              {rep.labels_imported} labels imported · {rep.already_here} were already here · {rep.touches_created} touches added · {rep.touches_matched} touches matched
            </div>
            {rep.unmatched_videos.map((u, i) => <div key={i} className="issue danger">{u.title ?? "A video in the file"} ({u.labels} labels): {u.why}</div>)}
            {rep.conflicts.length > 0 && <div className="issue">{rep.conflicts.length} labels were not imported because a label is already in that spot here, e.g. touch {rep.conflicts[0].touch} {rep.conflicts[0].side} {rep.conflicts[0].label}.</div>}
            {rep.errors.slice(0, 3).map((e, i) => <div key={i} className="issue danger">{e.video ? `${e.video}: ` : ""}{e.error}</div>)}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose} disabled={busy}>{rep ? "Done" : "Cancel"}</button>
          {!rep && <button className="btn primary" onClick={go} disabled={!file || busy}>{busy ? "Importing…" : "Import labels"}</button>}
        </div>
      </div>
    </div>
  );
}

function BoutModal({ v, onClose }: { v: Video; onClose: () => void }) {
  const { toast } = useApp();
  const [name, setName] = useState(v.title);
  const [l, setL] = useState(""), [r, setR] = useState("");
  const save = async () => {
    try {
      await api.addBout(v.video_id, { name, left: { display_name: l || null, athlete_id: l || null }, right: { display_name: r || null, athlete_id: r || null } });
      onClose();
    } catch (e: any) { toast(e.message, true); }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Bout and athletes">
        <h3>Bout and athletes</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>Left / right are the screen sides at the start of the bout. They stay fixed; per-frame screen position and crossings are tracked separately.</p>
        <div><label className="lab" htmlFor="bn">Bout name</label><input id="bn" className="field" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="formgrid">
          <div><label className="lab" htmlFor="fl">Left athlete</label><input id="fl" className="field" value={l} onChange={(e) => setL(e.target.value)} /></div>
          <div><label className="lab" htmlFor="fr">Right athlete</label><input id="fr" className="field" value={r} onChange={(e) => setR(e.target.value)} /></div>
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></div>
      </div>
    </div>
  );
}

function UploadModal({ onClose }: { onClose: () => void }) {
  const { toast } = useApp();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState(""), [l, setL] = useState(""), [r, setR] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!file) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file); fd.append("title", title || file.name.replace(/\.[^.]+$/, ""));
    if (l) fd.append("left_name", l);
    if (r) fd.append("right_name", r);
    fd.append("auto_analyze", "true");
    try { await api.upload(fd); toast("uploaded · automatic analysis started"); onClose(); }
    catch (e: any) { toast(e.message, true); setBusy(false); }
  };
  return (
    <div className="modal-back" onClick={busy ? undefined : onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Upload video">
        <h3>Upload a bout</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
          After upload the video is analysed automatically: exact frame index, scoring-light candidates, both fencers
          tracked, footwork pre-labels. Everything runs on this computer.</p>
        <input ref={fileRef} type="file" accept="video/*" hidden onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, "")); }} />
        <button className="btn" onClick={() => fileRef.current?.click()}><Upload size={15} /> {file ? file.name : "Choose video file"}</button>
        <div><label className="lab" htmlFor="ut">Title</label><input id="ut" className="field" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div className="formgrid">
          <div><label className="lab" htmlFor="ul">Left athlete (screen left at start)</label><input id="ul" className="field" value={l} onChange={(e) => setL(e.target.value)} /></div>
          <div><label className="lab" htmlFor="ur">Right athlete</label><input id="ur" className="field" value={r} onChange={(e) => setR(e.target.value)} /></div>
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary" onClick={go} disabled={!file || busy}>{busy ? "Uploading…" : "Upload and analyse"}</button>
        </div>
      </div>
    </div>
  );
}

function BulkModal({ onClose }: { onClose: () => void }) {
  const { toast } = useApp();
  const [mode, setMode] = useState<"folder" | "links">("folder");
  const [folder, setFolder] = useState("C:\\EnGarde\\incoming");
  const [text, setText] = useState("");
  const [analyze, setAnalyze] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<BulkJob[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const poll = () => api.bulkStatus().then(setRuns).catch(() => {});
  useEffect(() => { poll(); const t = setInterval(poll, 3000); return () => clearInterval(t); }, []);
  const running = runs.find((r) => r.status === "running" || r.status === "queued");
  const start = async () => {
    setBusy(true);
    try {
      const r = mode === "folder" ? await api.bulkFolder(folder, analyze) : await api.bulkLinks(text, analyze);
      toast(`${r.found} videos queued for import`);
      poll();
    } catch (e: any) { toast(e.message, true); }
    setBusy(false);
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Bulk import videos" style={{ maxWidth: 620 }}>
        <h3>Bulk import videos</h3>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
          Adds many bout videos at once, without labels. Videos already here are skipped, so running it twice is safe.
          It runs in the background on the host PC; you can close this window.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className={`btn ${mode === "folder" ? "primary" : ""}`} onClick={() => setMode("folder")}>Folder on the host PC</button>
          <button className={`btn ${mode === "links" ? "primary" : ""}`} onClick={() => setMode("links")}>List of links</button>
        </div>
        {mode === "folder" ? (
          <div><label className="lab" htmlFor="bf">Folder (sub-folders included)</label>
            <input id="bf" className="field" value={folder} onChange={(e) => setFolder(e.target.value)} />
            <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4 }}>Copy the video files into this folder on the host PC first. .mp4, .mov, .m4v, .mkv, .webm and .avi are read.</div></div>
        ) : (
          <div><label className="lab" htmlFor="bl">Links: one per line, "title, link" lines, or a CSV with a video_url column</label>
            <textarea id="bl" className="field" rows={7} value={text} onChange={(e) => setText(e.target.value)} placeholder={"https://.../bout1.mp4\nLUKASHENKO vs NITU, https://.../bout2.mp4"} />
            <input ref={fileRef} type="file" accept=".csv,.txt,.tsv" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
            <button className="btn" style={{ marginTop: 6 }} onClick={() => fileRef.current?.click()}><FileUp size={15} /> Load a CSV or text file</button></div>
        )}
        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <input type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} />
          Also run the automatic analysis on each video (slow: leave off to just add the videos)
        </label>
        {runs.length > 0 && (
          <div className="import-report">
            {runs.slice(0, 3).map((r) => (
              <div key={r.job_id} className={`issue ${r.status === "completed" ? "ok" : r.status === "failed" ? "danger" : ""}`}>
                {r.kind === "bulk_import_links" ? "Links" : "Folder"} · {r.status}
                {r.status === "running" && ` · ${Math.round((r.progress ?? 0) * 100)}% · ${r.message ?? ""}`}
                {r.result && ` · ${r.result.added} added, ${r.result.already_here} already here, ${r.result.failed.length} failed`}
                {r.status === "failed" && r.message && ` · ${r.message}`}
                {r.result && r.result.failed.slice(0, 3).map((f, i) => <div key={i} style={{ fontSize: 12 }}>{f.item}: {f.error}</div>)}
              </div>
            ))}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" onClick={start} disabled={busy || !!running || (mode === "folder" ? !folder.trim() : !text.trim())}>
            {running ? "Import running…" : busy ? "Starting…" : "Start import"}</button>
        </div>
      </div>
    </div>
  );
}
