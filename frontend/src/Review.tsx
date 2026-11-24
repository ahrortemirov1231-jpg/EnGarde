import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft, ChevronRight, Pause, Play, SkipBack, SkipForward, PanelLeftClose, PanelLeftOpen, Bone, Square,
  TriangleAlert, Crosshair, Plus, Cpu, RotateCw, Users,
} from "lucide-react";
import type { Candidates, Fencer } from "./api";
import { api, EventRow, fmtClock, fmtMs, frameAtTime, Overlay, Segment, SHORT, Touch, TouchDetail, Video, Side } from "./api";
import { Lock, Info } from "lucide-react";
import { useApp, store } from "./App";
import Player, { PlayerHandle } from "./Player";
import Timeline, { Sel } from "./Timeline";
import ReviewPanel from "./ReviewPanel";

export default function Review({ touchId, hash }: { touchId?: string; hash: string }) {
  const { toast, config, annotator } = useApp();
  const videoFromHash = (hash.match(/video=([\w]+)/) || [])[1];
  const frameFromHash = Number((hash.match(/frame=(\d+)/) || [])[1] ?? NaN);
  const [videoId, setVideoId] = useState<string | undefined>(videoFromHash);
  const [video, setVideo] = useState<(Video & { bouts: any[] }) | null>(null);
  const [times, setTimes] = useState<number[]>([]);
  const [touches, setTouches] = useState<Touch[]>([]);
  const [touch, setTouch] = useState<TouchDetail | null>(null);
  const [preds, setPreds] = useState<{ predictions: Segment[]; events: EventRow[]; run: any } | null>(null);
  // skeleton / box data for every analysed touch of this video, loaded as playback gets near it,
  // so overlays show while the video plays through, not only on the selected touch
  const ovCache = useRef<Map<string, Overlay | "loading">>(new Map());
  const [, setOvTick] = useState(0);
  const [allVideos, setAllVideos] = useState<Video[]>([]);
  const [help, setHelp] = useState(false);
  const [cands, setCands] = useState<Candidates | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(store.get("engarde.rate", "1"));
  const [show, setShow] = useState({ skel: true, box: true, quality: true, people: false });
  const [sel, setSel] = useState<Sel>(null);
  const [blind, setBlind] = useState(store.get("engarde.blind", "0") === "1");
  const [drawLabel, setDrawLabel] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [lampL, setLampL] = useState(false), [lampR, setLampR] = useState(false);
  const pl = useRef<PlayerHandle>(null);
  const playingRef = useRef(false);

  // resolve which video we are on
  useEffect(() => {
    if (touchId) api.touch(touchId).then((t) => setVideoId(t.video_id)).catch((e) => toast(e.message, true));
    else if (videoFromHash) setVideoId(videoFromHash);          // follow the address bar every time it changes
    else api.videos().then((v) => {                              // plain "Review": reopen the video you were on
      const last = store.get("engarde.lastVideo", "");
      const pick = v.find((x) => x.video_id === last) ?? v[0];
      if (pick) setVideoId(pick.video_id);
    });
  }, [touchId, videoFromHash, toast]);
  useEffect(() => { api.videos().then(setAllVideos).catch(() => {}); }, [videoId]);
  useEffect(() => {
    if (!videoId) return;
    store.set("engarde.lastVideo", videoId);
    // a different video: forget everything that belonged to the previous one
    pl.current?.video?.pause(); playingRef.current = false; setPlaying(false);
    setVideo(null); setTimes([]); setTouches([]); ovCache.current = new Map();
    if (!touchId) setFrame(0);
    api.video(videoId).then(setVideo).catch((e) => toast(e.message, true));
    api.frameTimes(videoId).then((r) => setTimes(r.t)).catch(() => setTimes([]));
  }, [videoId, toast]);
  useEffect(() => { if (config && !drawLabel) setDrawLabel(config.footwork[1] ?? config.footwork[0] ?? "Unknown"); }, [config, drawLabel]);
  const fetchOverlay = useCallback((tid: string, force = false) => {
    if (!force && ovCache.current.has(tid)) return;
    if (!ovCache.current.has(tid)) ovCache.current.set(tid, "loading");
    api.overlay(tid).then((o) => { ovCache.current.set(tid, o); setOvTick((x) => x + 1); })
      .catch(() => { ovCache.current.delete(tid); });
  }, []);
  const loadTouches = useCallback(() => { if (videoId) api.touches(`video_id=${videoId}`).then(setTouches); }, [videoId]);
  useEffect(() => { loadTouches(); const t = setInterval(() => { if (!document.hidden) loadTouches(); }, 4000); return () => clearInterval(t); }, [loadTouches]);

  const loadTouch = useCallback(async (keepFrame = true) => {
    if (!touchId) { setTouch(null); setPreds(null); setCands(null); return; }
    const t = await api.touch(touchId);
    setTouch(t);
    if (!keepFrame) setFrame(Number.isFinite(frameFromHash) && frameFromHash >= t.start_frame && frameFromHash < t.end_frame ? frameFromHash : t.light_frame);
    fetchOverlay(touchId, true);
    api.candidates(touchId).then((c) => {
      setCands(c);
      // show everyone automatically when the system could not find a plausible fencer pair
      if (c.available && c.status && !["two_fencers", "reviewer_assigned", "imported"].includes(c.status)) setShow((x) => ({ ...x, people: true }));
    }).catch(() => setCands(null));
    if (blind) setPreds(null);
    else api.predictions(touchId, annotator, false).then((r) => setPreds(r.hidden ? null : r)).catch((e) => toast(e.message, true));
  }, [touchId, blind, annotator, toast]); // eslint-disable-line
  useEffect(() => { setSel(null); loadTouch(false).catch((e) => toast(e.message, true)); }, [touchId]); // eslint-disable-line
  useEffect(() => { loadTouch(true).catch(() => {}); }, [blind, annotator]); // eslint-disable-line
  // refresh when an analysis job finishes
  const lastRun = touches.find((t) => t.touch_id === touchId)?.latest_run?.run_id;
  useEffect(() => { if (lastRun && touch && lastRun !== touch.latest_run?.run_id) loadTouch(true); }, [lastRun]); // eslint-disable-line

  const inWindow = !!touch && frame >= touch.start_frame && frame < touch.end_frame;
  const nFrames = times.length;
  // which analysed touch covers the frame on screen (the selected touch wins when windows overlap)
  const coverId = inWindow ? touch!.touch_id
    : touches.find((t) => t.latest_run && frame >= t.start_frame && frame < t.end_frame)?.touch_id ?? null;
  const cover = coverId ? ovCache.current.get(coverId) : undefined;
  const overlay: Overlay | null = cover && cover !== "loading" ? cover : null;
  const nearKey = touches.filter((t) => t.latest_run && t.end_frame > frame && t.start_frame < frame + 300).map((t) => t.touch_id).join(",");
  useEffect(() => { nearKey.split(",").filter(Boolean).forEach((id) => fetchOverlay(id)); }, [nearKey, fetchOverlay]);

  // seek helpers
  const seek = useCallback((f: number) => {
    if (!nFrames) return;
    const n = Math.max(0, Math.min(nFrames - 1, f));
    setFrame(n);
    const v = pl.current?.video;
    if (v) {
      const dt = n + 1 < nFrames ? times[n + 1] - times[n] : 1 / 30;
      v.currentTime = times[n] + dt * 0.4; // inside the frame's display interval (playback is approximate)
    }
  }, [times, nFrames]);
  const pause = useCallback(() => { const v = pl.current?.video; v?.pause(); playingRef.current = false; setPlaying(false); }, []);
  const play = useCallback(() => {
    const v = pl.current?.video; if (!v) return;
    // A touch opens on its light frame, which is 0.5 s before the end of its window. Pressing play there
    // used to show half a second and stop; now it replays the whole touch from its first frame.
    if (touch && frame >= touch.light_frame && frame < touch.end_frame) seek(touch.start_frame);
    else if (times.length && Math.abs(frameAtTime(times, v.currentTime) - frame) > 1) seek(frame);   // start where the readout says
    v.playbackRate = Number(rate); v.play().catch(() => {}); playingRef.current = true; setPlaying(true);
  }, [rate, touch, frame, seek, times]);
  useEffect(() => { const v = pl.current?.video; if (v) v.playbackRate = Number(rate); store.set("engarde.rate", rate); }, [rate]);
  // Follow playback frame by frame. requestVideoFrameCallback reports the exact frame the browser
  // has just put on screen, so skeleton and boxes are drawn on the frame they belong to; older
  // browsers fall back to the screen refresh timer.
  useEffect(() => {
    if (!playing) return;
    const v = pl.current?.video as any;
    if (!v) return;
    const useVfc = typeof v.requestVideoFrameCallback === "function";
    let id = 0, stopped = false;
    const onFrame = (t: number) => {
      if (stopped) return;
      if (times.length) {
        const f = frameAtTime(times, t);
        setFrame(f);
        if (touch && f >= touch.end_frame - 1 && f <= touch.end_frame + 5) { pause(); seek(touch.end_frame - 1); return; }
      }
      schedule();
    };
    const schedule = () => {
      id = useVfc ? v.requestVideoFrameCallback((_now: number, meta: { mediaTime: number }) => onFrame(meta.mediaTime))
        : requestAnimationFrame(() => onFrame(v.currentTime));
    };
    schedule();
    return () => { stopped = true; if (useVfc) v.cancelVideoFrameCallback(id); else cancelAnimationFrame(id); };
  }, [playing, times, touch, pause, seek]);

  const step = (d: number) => { pause(); seek(frame + d); };
  const segAll: Segment[] = useMemo(() => [...(touch?.annotations ?? []), ...(preds?.predictions ?? [])], [touch, preds]);
  const selected = sel ? segAll.find((s) => s.segment_id === sel.id) ?? null : null;
  const jumpTo = (which: "start" | "end") => { if (selected) { pause(); seek(which === "start" ? selected.start_frame : selected.end_frame - 1); } };

  // keyboard
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (["INPUT", "SELECT", "TEXTAREA"].includes(tag)) return;
      if (e.key === " ") { e.preventDefault(); playingRef.current ? pause() : play(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); step(e.shiftKey ? 10 : 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(e.shiftKey ? -10 : -1); }
      else if (e.key === "[") jumpTo("start");
      else if (e.key === "]") jumpTo("end");
    };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  });

  // --- annotation ops
  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) toast(ok); await loadTouch(true); } catch (e: any) { toast(e.message, true); }
  };
  const onCreate = (side: Side, s: number, e: number) => run(async () => {
    const a = await api.createAnn(touch!.touch_id, { side, start_frame: s, end_frame: e, label: drawLabel, annotator_id: annotator, label_source: blind ? "blind" : undefined });
    setSel({ kind: "ann", id: a.segment_id });
  }, `${side} ${drawLabel} [${s}, ${e}) saved`);
  const onResize = (seg: Segment, s: number, e: number) => run(() => api.patchAnn(seg.segment_id, { start_frame: s, end_frame: e, annotator_id: annotator }), `bounds saved [${s}, ${e})`);

  // --- touch ops
  const bout = video?.bouts?.[0];
  const addTouchHere = () => run(async () => {
    if (!bout) throw new Error("create a bout for this video first (Videos page)");
    const r = await api.addTouch(bout.bout_id, { light_frame: frame, lamp_left: lampL, lamp_right: lampR, notes: inWindow ? "light set on exact frame" : "light set during approximate playback — verify on exact frames" });
    window.location.hash = `#/review/${r.touch.touch_id}`;
  }, "touch created · extracting exact frames");
  const analyze = (force = false) => run(() => api.analyze(touch!.touch_id, { force }), "analysis queued");

  const fencers = touch?.bout.fencers ?? bout?.fencers ?? [];
  const lightRel = touch ? touch.times.rel_ms[frame - touch.start_frame] : undefined;
  const relLabel = inWindow && lightRel !== undefined ? fmtMs(lightRel) : "outside window";
  const runInfo = preds?.run;
  const pose = touch?.manifest?.s2_tracking?.info;
  const badges = (
    <>
      {runInfo && !blind && <span className="badge warn">{runInfo.status_label}</span>}
      {blind && <span className="badge">blind mode · predictions hidden</span>}
      {video?.locked ? <span className="badge warn">locked by {video.locked_by ?? "someone"} · labels cannot change</span> : null}
      {!overlay && nFrames > 0 && <span className="badge">{cover === "loading" ? "loading skeleton…"
        : touches.some((t) => t.latest_run) ? "no skeleton on this frame · it is outside the analysed touches" : "no skeleton yet · run Analyse on a touch"}</span>}
      {pose?.backend === "imported_skeleton" && <span className="badge">skeleton: imported{pose.synthetic ? " (synthetic)" : ""}</span>}
      {touch?.manifest?.s2_tracking?.status === "failed" && <span className="badge danger">tracking failed: {touch.manifest.s2_tracking.error}</span>}
    </>
  );

  if (!videoId) return <div className="page"><div className="empty panel"><h3>No video yet</h3><p>Import a video on the Videos page first.</p><a className="btn primary" href="#/videos">Go to Videos</a></div></div>;

  return (
    <div className="workspace">
      {/* ---------------- touch list */}
      <aside className={`touches panel ${collapsed ? "collapsed" : ""}`} aria-label="Touches">
        <div className="touches-head">
          {!collapsed && <h3>Touches <span className="num" style={{ color: "var(--ink-3)", fontWeight: 500 }}>{touches.length}</span></h3>}
          {!collapsed && <button className="btn ghost icon" onClick={() => setHelp((h) => !h)} aria-expanded={help} aria-label="What do the words under each touch mean?" title="What do the words under each touch mean?"><Info size={16} /></button>}
          <button className="btn ghost icon" onClick={() => setCollapsed((c) => !c)} aria-label={collapsed ? "Expand touch list" : "Collapse touch list"}>
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        </div>
        {!collapsed && <>
          <div className="video-pick">
            <label className="lab" htmlFor="vpick">Video</label>
            <select id="vpick" className="field" value={videoId} onChange={(e) => { window.location.hash = `#/review?video=${e.target.value}`; }}>
              {allVideos.map((v) => <option key={v.video_id} value={v.video_id}>{v.title}</option>)}
            </select>
            {touchId && <button className="btn sm" onClick={() => { window.location.hash = `#/review?video=${videoId}`; }} title="Close the touch and play the whole video. Skeleton and boxes appear on every analysed touch.">Whole video</button>}
          </div>
          {help && (
            <div className="legend-note" role="note">
              <p><b>rule</b>: the labels on this touch are the computer's first guess from simple rules (how far the feet moved). It is not a trained model and it has not been checked.</p>
              <p><b>model</b>: the guess came from a trained model instead.</p>
              <p><b>N to review</b>: N guessed segments that no person has confirmed or corrected yet. The number goes down as you confirm, correct or reject them.</p>
              <p><b>N labels</b>: segments a person has labelled. <b>not analysed</b>: the computer has not looked at this touch yet.</p>
              <p>Touches are listed in time order. Fencer numbers are the same on every touch: 1 = left fencer, 2 = right fencer.</p>
            </div>
          )}
          <div className="touch-list">
            {touches.length === 0 && <p style={{ color: "var(--ink-3)", fontSize: 12.5, padding: "0 8px" }}>No touches yet. Play the video, stop on the scoring light and mark it below.</p>}
            {touches.map((t) => (
              <button key={t.touch_id} className={`touch-item ${t.touch_id === touchId ? "active" : ""}`} onClick={() => { window.location.hash = `#/review/${t.touch_id}`; }}>
                <span className="seq num">{t.seq}</span>
                <span className="t1 num">
                  {times.length ? fmtClock(times[t.light_frame]) : `f ${t.light_frame}`}
                  <i className={`lamp l ${t.lamp_left == null ? "unk" : t.lamp_left ? "on" : ""}`} title={`left lamp ${t.lamp_left == null ? "unknown" : t.lamp_left ? "on" : "off"}`} />
                  <i className={`lamp r ${t.lamp_right == null ? "unk" : t.lamp_right ? "on" : ""}`} title={`right lamp ${t.lamp_right == null ? "unknown" : t.lamp_right ? "on" : "off"}`} />
                </span>
                <span className="t2">
                  <span title={t.latest_run ? (t.latest_run.method === "rule_baseline" ? "First guess from simple rules, not a trained model. Not checked by a person." : "Guess from a trained model.") : "The computer has not analysed this touch yet."}>{t.latest_run ? (t.latest_run.method === "rule_baseline" ? "rule" : "model") : "not analysed"}</span>
                  {t.pending_review ? <span title="Guessed segments that no person has confirmed or corrected yet.">{t.pending_review} to review</span> : null}
                  {t.n_annotations ? <span>{t.n_annotations} labels</span> : null}
                  {t.light_source === "detector_candidate" && <span style={{ color: "var(--warn)" }}>light unverified</span>}
                  {t.light_source === "manual_verified" && <span>light verified</span>}
                </span>
              </button>
            ))}
          </div>
          <div className="touches-foot">
            <div style={{ display: "flex", gap: 12, fontSize: 12.5 }}>
              <label className="toggle"><input type="checkbox" checked={lampL} onChange={(e) => setLampL(e.target.checked)} />left lamp</label>
              <label className="toggle"><input type="checkbox" checked={lampR} onChange={(e) => setLampR(e.target.checked)} />right lamp</label>
            </div>
            <button className="btn" onClick={addTouchHere} disabled={!nFrames}><Plus size={15} /> Light at frame {frame}</button>
          </div>
        </>}
      </aside>

      {/* ---------------- center */}
      <section className="center" aria-label="Video and timeline">
        {video && (
          <Player key={video.video_id} ref={pl} videoId={video.video_id} touchId={touch?.touch_id ?? null} width={video.width} height={video.height} contentRect={video.content_rect}
            frame={frame} exact={!playing} framesReady={!!touch /* the API extracts a window's exact frames on first request */} inWindow={inWindow}
            overlay={overlay} show={show} synthetic={!!video.is_synthetic} stageBadges={badges}
            people={cands?.available ? cands.boxes : null} chosen={cands?.assignment ?? null} display={cands?.display ?? null}
            names={{ left: fencers.find((f: Fencer) => f.side === "left")?.display_name, right: fencers.find((f: Fencer) => f.side === "right")?.display_name }}
            onVideoTime={(t) => { if (!playingRef.current) return; void t; }} onEnded={pause} />
        )}
        <div className="toolbar glass" role="toolbar" aria-label="Playback">
          <button className="tb-btn" onClick={() => jumpTo("start")} disabled={!selected} title="Jump to label start ( [ )" aria-label="Jump to label start"><SkipBack size={16} /></button>
          <button className="tb-btn" onClick={() => step(-1)} title="Previous frame (←, shift = 10)" aria-label="Previous frame"><ChevronLeft size={18} /></button>
          <button className="tb-btn play" onClick={() => (playing ? pause() : play())} aria-label={playing ? "Pause" : "Play"} title="Play / pause (space)">
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button className="tb-btn" onClick={() => step(1)} title="Next frame (→, shift = 10)" aria-label="Next frame"><ChevronRight size={18} /></button>
          <button className="tb-btn" onClick={() => jumpTo("end")} disabled={!selected} title="Jump to label end ( ] )" aria-label="Jump to label end"><SkipForward size={16} /></button>
          <select className="speed" value={rate} onChange={(e) => setRate(e.target.value)} aria-label="Playback speed">
            {["0.1", "0.25", "0.5", "1"].map((r) => <option key={r} value={r}>{r}×</option>)}
          </select>
          <span className="sep" />
          <div className="readout num">
            <b>{playing ? "≈ " : ""}frame {frame}</b>
            <small>{times[frame] !== undefined ? fmtClock(times[frame]) : "—"} · {relLabel}</small>
          </div>
          <span className="sep" />
          <button className={`tb-btn ${show.skel ? "on" : ""}`} onClick={() => setShow((s) => ({ ...s, skel: !s.skel }))} aria-pressed={show.skel}><Bone size={15} /> Skeleton</button>
          <button className={`tb-btn ${show.box ? "on" : ""}`} onClick={() => setShow((s) => ({ ...s, box: !s.box }))} aria-pressed={show.box}><Square size={14} /> Boxes</button>
          <button className={`tb-btn ${show.quality ? "on" : ""}`} onClick={() => setShow((s) => ({ ...s, quality: !s.quality }))} aria-pressed={show.quality}><TriangleAlert size={14} /> Quality</button>
          <button className={`tb-btn ${show.people ? "on" : ""}`} onClick={() => setShow((s) => ({ ...s, people: !s.people }))} aria-pressed={show.people} title="Show every tracked person with its track number"><Users size={14} /> All people</button>
          {touch && <>
            <span className="sep" />
            <button className="tb-btn" onClick={() => seek(touch.light_frame)} title="Go to light frame"><Crosshair size={15} /> Light</button>
          </>}
        </div>
        {touch ? (
          <Timeline start={touch.start_frame} end={touch.end_frame} light={touch.light_frame} relMs={touch.times.rel_ms} current={frame}
            ann={touch.annotations} pred={blind ? null : preds?.predictions ?? []} events={blind ? [] : [...(preds?.events ?? []), ...touch.expert_events]}
            fencers={fencers} sel={sel} drawLabel={drawLabel} onSeek={(f) => { pause(); seek(f); }} onSelect={setSel}
            onCreate={onCreate} onResize={onResize} />
        ) : (
          <div className="panel" style={{ padding: 16, color: "var(--ink-2)", fontSize: 13 }}>
            Select a touch, or play the video and mark a scoring light. Timelines cover the analysis window [−3.0 s, +0.5 s] around the light.
          </div>
        )}
      </section>

      {/* ---------------- review panel */}
      <aside className="rpanel panel" aria-label="Review">
        <div className="rpanel-scroll">
          {touch ? (
            <>
              {video?.locked ? <div className="rsec"><div className="issue"><Lock size={14} /> This video is locked by {video.locked_by ?? "someone"}. Labels, reviews and touches cannot change until it is unlocked on the Videos page.</div></div> : null}
              <div className="rsec">
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <h5 style={{ margin: 0, flex: 1 }}>Touch {touch.seq}</h5>
                  <button className="btn sm" onClick={() => analyze(false)} title="Run pipeline S1–S6 (cached stages are reused)"><Cpu size={14} /> Analyze</button>
                  <button className="btn sm ghost icon" onClick={() => analyze(true)} title="Re-run all stages" aria-label="Re-run all stages"><RotateCw size={14} /></button>
                </div>
                <dl className="kv num">
                  <dt>Light frame</dt><dd>{touch.light_frame} · {fmtClock(touch.light_time_s - touch.times.t0_video_s)}</dd>
                  <dt>Window</dt><dd>[{touch.start_frame}, {touch.end_frame}){touch.window_clipped ? " · clipped" : ""}</dd>
                  <dt>Lamps</dt><dd>L {touch.lamp_left == null ? "?" : touch.lamp_left ? "on" : "off"} · R {touch.lamp_right == null ? "?" : touch.lamp_right ? "on" : "off"}</dd>
                  <dt>Analysis</dt><dd>{touch.latest_run ? touch.latest_run.status_label : touch.jobs.find((j) => j.status === "running") ? "running…" : "not run"}</dd>
                </dl>
                {touch.light_source === "detector_candidate" && (
                  <div className="issue" style={{ marginTop: 10, flexDirection: "column", gap: 8 }}>
                    <span>Light frame is a detector candidate (chroma onset). Step to the first lit frame and verify it.</span>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button className="btn sm" disabled={frame !== touch.light_frame}
                        title={frame !== touch.light_frame ? "go to the light frame first" : ""}
                        onClick={() => run(() => api.patchTouch(touch.touch_id, { light_source: "manual_verified" }), "light verified")}>Verify frame {touch.light_frame}</button>
                      <button className="btn sm danger" onClick={() => run(async () => { await api.deleteTouch(touch.touch_id); window.location.hash = `#/review?video=${touch.video_id}`; }, "touch deleted")}>Not a touch · delete</button>
                    </div>
                    <span style={{ fontSize: 11.5 }}>Wrong frame? Mark the right one with “Light at frame …” and delete this candidate.</span>
                  </div>
                )}
                {touch.jobs.filter((j) => j.status === "failed").slice(0, 1).map((j) => (
                  <div key={j.job_id} className="issue danger" style={{ marginTop: 8 }}>{j.kind} failed: {j.message}</div>
                ))}
                {touch.jobs.filter((j) => j.status === "running" || j.status === "queued").slice(0, 1).map((j) => (
                  <div key={j.job_id} style={{ marginTop: 8 }}><div className="status-note">{j.kind.replace("_", " ")} · {j.status} · {j.stage ?? ""}</div><div className="progress" style={{ marginTop: 6 }}><i style={{ width: `${(j.progress || 0) * 100}%` }} /></div></div>
                ))}
              </div>
              <div className="divider" />
              <ReviewPanel touch={touch} selected={selected} preds={preds?.predictions ?? []} blind={blind} setBlind={(b) => { setBlind(b); store.set("engarde.blind", b ? "1" : "0"); }}
                drawLabel={drawLabel} setDrawLabel={setDrawLabel} frame={frame} config={config} onChanged={() => loadTouch(true)}
                onSelect={setSel} onSeek={(f) => { pause(); seek(f); }} cands={cands} />
            </>
          ) : (
            <div className="rsec"><h5>Review</h5><p style={{ color: "var(--ink-2)", fontSize: 13 }}>No touch selected.</p></div>
          )}
        </div>
      </aside>
    </div>
  );
}
export { SHORT };
