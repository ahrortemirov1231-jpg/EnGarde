import { useEffect, useState } from "react";
import { Check, PenLine, EyeOff, HelpCircle, X, Trash2, Flag, FileText } from "lucide-react";
import { api, Candidates, Config, fmtMs, labelGroups, Segment, SHORT, slug, TouchDetail } from "./api";
import { useApp } from "./App";
import type { Sel } from "./Timeline";

interface Props {
  touch: TouchDetail; selected: Segment | null; preds: Segment[]; blind: boolean; setBlind: (b: boolean) => void;
  drawLabel: string; setDrawLabel: (l: string) => void; frame: number; config: Config | null;
  onChanged: () => void; onSelect: (s: Sel) => void; onSeek: (f: number) => void; cands: Candidates | null;
}

const STATUS_TEXT: Record<string, [string, string]> = {
  two_fencers: ["ok", "Automatic: a plausible fencer pair was found (white uniforms, similar size, same strip). Check and confirm."],
  reviewer_assigned: ["ok", "Confirmed by a reviewer."],
  no_plausible_pair: ["danger", "No plausible fencer pair found. Every frame is no_call until you assign the two fencers."],
  one_fencer_only: ["danger", "Only one fencer-like person found. Assign the two fencers if they are visible."],
  no_tracks: ["danger", "No people were tracked in this window."],
  imported: ["ok", "Skeleton imported (not tracked here)."],
};

function IdentitySection({ touchId, cands, onChanged }: { touchId: string; cands: Candidates | null; onChanged: () => void }) {
  const { toast, annotator } = useApp();
  const [l, setL] = useState<string>(""), [r, setR] = useState<string>("");
  useEffect(() => {
    setL(cands?.assignment?.left != null ? String(cands.assignment.left) : "");
    setR(cands?.assignment?.right != null ? String(cands.assignment.right) : "");
  }, [cands]);
  if (!cands?.available) return <div className="rsec"><h5>Fencer identity</h5><p style={{ fontSize: 12.5, color: "var(--ink-3)", margin: 0 }}>Available after analysis.</p></div>;
  const [tone, text] = STATUS_TEXT[cands.status ?? ""] ?? ["", cands.status ?? ""];
  const opts = cands.candidates ?? [];
  const num = (tid: number) => cands.display?.[String(tid)] ?? tid;     // same number on every touch of the bout
  const save = async () => {
    try {
      await api.setIdentity(touchId, { left: Number(l), right: Number(r), reviewer: annotator });
      toast("identity saved · re-analysing this touch"); onChanged();
    } catch (e: any) { toast(e.message, true); }
  };
  const reset = async () => {
    try { await api.resetIdentity(touchId, annotator); toast("back to automatic selection · re-analysing"); onChanged(); }
    catch (e: any) { toast(e.message, true); }
  };
  const same = cands.identity_confirmed && String(cands.identity_confirmed.left) === l && String(cands.identity_confirmed.right) === r;
  return (
    <div className="rsec">
      <h5>Fencer identity</h5>
      <div className={`issue ${tone}`} style={{ marginBottom: 8 }}>{text}{cands.identity_confirmed ? ` (${cands.identity_confirmed.confirmed_by})` : ""}</div>
      <table className="t num" style={{ fontSize: 12 }}>
        <thead><tr><th>Track</th><th className="r">White</th><th className="r">Height</th><th className="r">Frames</th><th /></tr></thead>
        <tbody>{opts.slice(0, 8).map((c) => (
          <tr key={c.track_id}>
            <td>#{num(c.track_id)}{c.members.length > 1 ? <span style={{ color: "var(--ink-3)" }}> (+{c.members.length - 1})</span> : null}</td>
            <td className="r">{c.whiteness == null ? "—" : c.whiteness.toFixed(2)}</td>
            <td className="r">{c.height_px}</td><td className="r">{c.frames}</td>
            <td style={{ color: c.eligible ? "var(--ink-3)" : "var(--danger)", fontSize: 11 }}>
              {cands.assignment?.left === c.track_id ? "LEFT" : cands.assignment?.right === c.track_id ? "RIGHT" : c.eligible ? "" : "not white"}</td>
          </tr>))}</tbody>
      </table>
      <div className="formgrid" style={{ marginTop: 8 }}>
        <div><label className="lab" htmlFor="idl">Left fencer</label>
          <select id="idl" className="field" value={l} onChange={(e) => setL(e.target.value)}>
            <option value="">—</option>{opts.map((c) => <option key={c.track_id} value={c.track_id}>#{num(c.track_id)}</option>)}</select></div>
        <div><label className="lab" htmlFor="idr">Right fencer</label>
          <select id="idr" className="field" value={r} onChange={(e) => setR(e.target.value)}>
            <option value="">—</option>{opts.map((c) => <option key={c.track_id} value={c.track_id}>#{num(c.track_id)}</option>)}</select></div>
        <button className="btn primary full" disabled={!l || !r || l === r || !!same} onClick={save}>
          {cands.assignment && String(cands.assignment.left) === l && String(cands.assignment.right) === r ? "Confirm these two fencers" : "Assign and re-analyse"}</button>
        {cands.identity_confirmed && <button className="btn full" onClick={reset}>Back to automatic</button>}
      </div>
      <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "6px 0 0" }}>Numbers mean the same on every touch of this bout: #1 is the left fencer, #2 the right fencer, #3 and up are other people in the picture (referee, coaches), left to right. Turn on “All people” to see them on the video. Only this touch is re-analysed.</p>
    </div>
  );
}

const REASON_TEXT: Record<string, string> = {
  pose_missing: "Pose missing on > 30% of frames (or no person detected)",
  track_unstable: "Track unstable: break, suspected ID switch, crossing, implausible pair (background person / official) or after a shot cut",
  camera_cut: "Shot change inside the interval",
  low_confidence: "Mean calibrated probability < 0.5",
};

export default function ReviewPanel(p: Props) {
  const { toast, annotator, setAnnotator, teamMode } = useApp();
  const groups = labelGroups(p.config);
  const first = p.config?.footwork?.[0] ?? "Unknown";
  const labelOptions = groups.map((g) => (
    <optgroup key={g.name} label={g.name}>{g.labels.map((l) => <option key={l} value={l}>{l}</option>)}</optgroup>
  ));
  const s = p.selected;
  const [label, setLabel] = useState("");
  const [a, setA] = useState(0), [b, setB] = useState(0);
  const [reason, setReason] = useState(""), [comment, setComment] = useState("");
  const [amb, setAmb] = useState(false);
  const [evType, setEvType] = useState("lunge_start"), [evSide, setEvSide] = useState<"left" | "right">("left");
  useEffect(() => {
    if (!s) return;
    setLabel(s.label === "none" ? first : s.label); setA(s.start_frame); setB(s.end_frame); setReason(""); setComment(""); setAmb(!!s.ambiguity_flag);
  }, [s?.segment_id, s?.updated_at]); // eslint-disable-line

  const rel = (f: number) => {
    const t = p.touch.times; const i = f - p.touch.start_frame;
    return i >= 0 && i < t.rel_ms.length ? fmtMs(t.rel_ms[i]) : f === p.touch.end_frame ? "window end" : "—";
  };
  const go = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); toast(ok); p.onChanged(); } catch (e: any) { toast(e.message, true); } };
  const review = (action: string, extra: Record<string, unknown> = {}) => go(async () => {
    const r: any = await api.review(s!.segment_id, { action, reviewer: annotator, error_reason: reason || undefined, comment: comment || undefined, ...extra });
    if (r.resulting_annotation_id) p.onSelect({ kind: "ann", id: r.resulting_annotation_id });
  }, `review saved (${action}) · original prediction kept`);
  const fencer = s ? p.touch.bout.fencers.find((f) => f.side === s.side) : null;
  const isPred = s?.kind === "prediction";
  const lastReview = isPred && s!.reviews?.length ? s!.reviews![s!.reviews!.length - 1] : null;
  const cov = p.touch.coverage;

  return (
    <>
      <IdentitySection touchId={p.touch.touch_id} cands={p.cands} onChanged={p.onChanged} />
      <div className="divider" />
      <div className="rsec">
        <h5>Reviewer</h5>
        <div className="formgrid">
          <div className="full"><label className="lab" htmlFor="ann">Annotator ID</label>
            <input id="ann" className="field" value={annotator} disabled={teamMode} title={teamMode ? "Your account name. Labels and reviews are saved under it." : undefined} onChange={(e) => setAnnotator(e.target.value.trim())} /></div>
          <label className="toggle full"><input type="checkbox" checked={p.blind} onChange={(e) => p.setBlind(e.target.checked)} />
            Blind mode: hide predictions, confidence and candidates</label>
        </div>
        <p style={{ fontSize: 12, color: "var(--ink-3)", margin: "8px 0 0" }}>
          {p.blind ? "Labels you make here are stored as blind, unless you have already seen predictions for this touch."
            : "Predictions are visible: anything you label on this touch is stored as model_assisted (training only, never blind test)."}
        </p>
      </div>
      <div className="rsec">
        <h5>New label brush</h5>
        <select className="field" value={p.drawLabel} onChange={(e) => p.setDrawLabel(e.target.value)} aria-label="Label for new segments">
          {labelOptions}
        </select>
        <p style={{ fontSize: 12, color: "var(--ink-3)", margin: "6px 0 0" }}>Tim's label names. Shown on the timeline as <b style={{ color: "var(--ink)" }}>{SHORT[p.drawLabel] ?? p.drawLabel}</b>.</p>
        {p.config?.pending_definition?.length ? <p style={{ fontSize: 12, color: "var(--ink-3)", margin: "6px 0 0" }}>Not enabled until defined by the sponsor: {p.config.pending_definition.join(", ")}.</p> : null}
      </div>
      <div className="divider" />

      {!s ? (
        <div className="rsec"><h5>Selection</h5><p style={{ fontSize: 13, color: "var(--ink-2)", margin: 0 }}>Click a block on the timeline to review it, or drag on a human row to add a label.</p></div>
      ) : (
        <div className="rsec">
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <span className={`sidechip ${s.side}`}>{s.side.toUpperCase()}</span>
            <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{fencer?.display_name || fencer?.athlete_id || "athlete not set"}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{isPred ? "Prediction" : "Human label"}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span className={`labelpill tl-seg lbl-${slug(s.label)} ${isPred && s.no_call ? "nocall" : ""}`} style={{ position: "static" }}>
              {isPred && s.no_call ? "no call" : s.label}
            </span>
            {isPred && !s.no_call && s.top2_label && s.class_probs && <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>2nd: {s.top2_label}</span>}
          </div>
          <dl className="kv num">
            <dt>Frames</dt><dd>[{s.start_frame}, {s.end_frame}) · {s.end_frame - s.start_frame} f</dd>
            <dt>Start</dt><dd>{rel(s.start_frame)}</dd>
            <dt>End (excl.)</dt><dd>{rel(s.end_frame)}</dd>
            {isPred ? <>
              <dt>Probability</dt><dd>{s.calibrated_prob != null ? `${s.calibrated_prob.toFixed(2)} (calibrated)` : s.class_probs ? "uncalibrated score — not shown" : "none (rule)"}</dd>
            </> : <>
              <dt>Source</dt><dd>{s.label_source}</dd>
              <dt>Annotator</dt><dd>{s.annotator_id}</dd>
            </>}
          </dl>
          {isPred && (
            <div className="issues" style={{ marginTop: 10 }}>
              {s.no_call_reason ? s.no_call_reason.split(",").map((r) => <div key={r} className="issue danger"><Flag size={14} />{REASON_TEXT[r] ?? r}</div>)
                : <div className="issue ok"><Check size={14} />Passed quality gate</div>}
              {s.track_quality && s.track_quality !== "ok" && !s.no_call_reason?.includes("track") && <div className="issue">Track: {s.track_quality}</div>}
              {s.camera_motion_flag ? <div className="issue">Camera moving (&gt; 2 px/frame) inside interval</div> : null}
              {s.pose_quality != null && <div className="issue ok" style={{ background: "var(--surface-2)", color: "var(--ink-2)" }}>Pose evidence on {(s.pose_quality * 100).toFixed(0)}% of frames</div>}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn sm" onClick={() => p.onSeek(s.start_frame)}>Go to start</button>
            <button className="btn sm" onClick={() => p.onSeek(s.end_frame - 1)}>Go to last frame</button>
          </div>

          {isPred ? (
            <div style={{ marginTop: 14 }}>
              {lastReview && <div className="status-note" style={{ marginBottom: 10 }}>Reviewed: {lastReview.action}{lastReview.corrected_label ? ` → ${lastReview.corrected_label}` : ""} by {lastReview.reviewer}. Append another review to change it.</div>}
              <div className="formgrid">
                <div><label className="lab" htmlFor="cl">Corrected label</label>
                  <select id="cl" className="field" value={label} onChange={(e) => setLabel(e.target.value)}>{labelOptions}</select></div>
                <div><label className="lab" htmlFor="er">Error reason</label>
                  <select id="er" className="field" value={reason} onChange={(e) => setReason(e.target.value)}>
                    <option value="">—</option>{(p.config?.error_reasons ?? []).map((r) => <option key={r}>{r}</option>)}</select></div>
                <div><label className="lab" htmlFor="cs">Start frame</label><input id="cs" className="field num" type="number" value={a} onChange={(e) => setA(Number(e.target.value))} /></div>
                <div><label className="lab" htmlFor="ce">End frame (excl.)</label><input id="ce" className="field num" type="number" value={b} onChange={(e) => setB(Number(e.target.value))} /></div>
                <div className="full"><label className="lab" htmlFor="cm">Comment</label><input id="cm" className="field" value={comment} onChange={(e) => setComment(e.target.value)} /></div>
              </div>
              <div className="actions" style={{ marginTop: 10 }}>
                <button className="btn primary wide" disabled={!!s.no_call} title={s.no_call ? "no_call has no label to confirm" : ""} onClick={() => review("confirm")}><Check size={15} /> Confirm {s.no_call ? "" : s.label}</button>
                <button className="btn wide" onClick={() => review("correct", { corrected_label: label, corrected_start_frame: a, corrected_end_frame: b })}><PenLine size={15} /> Correct to {label} [{a}, {b})</button>
                <button className="btn" onClick={() => review("unknown", { corrected_start_frame: a, corrected_end_frame: b })}><HelpCircle size={15} /> Unknown</button>
                <button className="btn" onClick={() => review("unobservable", { corrected_start_frame: a, corrected_end_frame: b })}><EyeOff size={15} /> Unobservable</button>
                <button className="btn danger wide" onClick={() => review("reject")}><X size={15} /> Reject (no label)</button>
              </div>
              <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "8px 0 0" }}>Reviews are appended; the original prediction is never changed.</p>
            </div>
          ) : (
            <div style={{ marginTop: 14 }}>
              <div className="formgrid">
                <div className="full"><label className="lab" htmlFor="al">Label</label>
                  <select id="al" className="field" value={label} onChange={(e) => setLabel(e.target.value)}>{labelOptions}</select></div>
                <div><label className="lab" htmlFor="as">Start frame</label><input id="as" className="field num" type="number" value={a} onChange={(e) => setA(Number(e.target.value))} /></div>
                <div><label className="lab" htmlFor="ae">End frame (excl.)</label><input id="ae" className="field num" type="number" value={b} onChange={(e) => setB(Number(e.target.value))} /></div>
                <label className="toggle full"><input type="checkbox" checked={amb} onChange={(e) => setAmb(e.target.checked)} />Ambiguous boundary / label</label>
              </div>
              <div className="actions" style={{ marginTop: 10 }}>
                <button className="btn primary" onClick={() => go(() => api.patchAnn(s.segment_id, { label, start_frame: a, end_frame: b, ambiguity_flag: amb, annotator_id: annotator }), "label saved")}><Check size={15} /> Save</button>
                <button className="btn" onClick={() => { setA(p.frame); }}>Start = frame {p.frame}</button>
                <button className="btn" onClick={() => { setB(p.frame + 1); }}>End = frame {p.frame} + 1</button>
                <button className="btn danger" onClick={() => go(async () => { await api.deleteAnn(s.segment_id, annotator); p.onSelect(null); }, "label deleted (kept in history)")}><Trash2 size={15} /> Delete</button>
              </div>
            </div>
          )}
        </div>
      )}
      <div className="divider" />
      <div className="rsec">
        <h5>Label coverage</h5>
        {(["left", "right"] as const).map((side) => (
          <div key={side} style={{ fontSize: 12.5, marginBottom: 4 }}>
            <b style={{ fontWeight: 600 }}>{side}</b>: {cov[side].complete ? "complete, no gaps" : `${cov[side].gaps.length} gap(s)${cov[side].gaps[0] ? ` e.g. [${cov[side].gaps[0][0]}, ${cov[side].gaps[0][1]})` : ""}`}
          </div>
        ))}
        <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "4px 0 0" }}>Training export needs one label per fencer per frame (PRD §3.5).</p>
      </div>
      <div className="rsec">
        <h5>Expert events</h5>
        <div className="formgrid">
          <select className="field" value={evSide} onChange={(e) => setEvSide(e.target.value as any)} aria-label="Event side"><option>left</option><option>right</option></select>
          <select className="field" value={evType} onChange={(e) => setEvType(e.target.value)} aria-label="Event type">{(p.config?.event_types ?? []).map((t) => <option key={t}>{t}</option>)}</select>
          <button className="btn full" onClick={() => go(() => api.addEvent(p.touch.touch_id, { side: evSide, event_type: evType, frame: p.frame }), "event saved")}>Add {evType} at frame {p.frame}</button>
        </div>
        {p.touch.expert_events.map((e) => (
          <div key={e.event_id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12.5, marginTop: 6 }} className="num">
            <span style={{ flex: 1 }}>{e.side} {e.event_type} · f {e.event_frame} · {fmtMs(e.event_time_ms)}</span>
            <button className="btn sm ghost icon" aria-label="Delete event" onClick={() => go(() => api.deleteEvent(e.event_id), "event deleted")}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>
      <div className="rsec">
        <h5>AIRS timeline</h5>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <a className="btn sm" href={`/api/airs/${p.touch.touch_id}?fmt=txt`} target="_blank" rel="noreferrer"><FileText size={14} /> Automated</a>
          <a className="btn sm" href={`/api/airs/${p.touch.touch_id}?arm=expert&fmt=txt`} target="_blank" rel="noreferrer"><FileText size={14} /> Expert</a>
          <a className="btn sm" href={`/api/airs/${p.touch.touch_id}`} target="_blank" rel="noreferrer">context.json</a>
        </div>
        <p style={{ fontSize: 11.5, color: "var(--ink-3)", margin: "6px 0 0" }}>Physical actions only; left/right = screen position; no referee vocabulary.</p>
      </div>
    </>
  );
}
