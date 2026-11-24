import { useRef, useState } from "react";
import { EventRow, Fencer, lowerBound, Segment, SHORT, slug, Side } from "./api";

const EV_SHORT: Record<string, string> = {
  lunge_start: "lunge ▸", lunge_land: "land", arm_extension_start: "arm ▸", arm_extension_full: "arm full", foot_contact: "foot",
};

export type Sel = { kind: "pred" | "ann"; id: string } | null;

interface Props {
  start: number; end: number; light: number; relMs: number[]; current: number;
  ann: Segment[]; pred: Segment[] | null; events: EventRow[]; fencers: Fencer[];
  sel: Sel; drawLabel: string;
  onSeek: (f: number) => void; onSelect: (s: Sel) => void;
  onCreate: (side: Side, s: number, e: number) => void;
  onResize: (seg: Segment, s: number, e: number) => void;
}

const glyph = (s: Segment, preds: Segment[] | null) => {
  if (s.kind === "prediction") {
    const r = s.reviews || [];
    if (!r.length) return "";
    const a = r[r.length - 1].action;
    return a === "confirm" ? "✓" : a === "reject" ? "✕" : "✎";
  }
  if (!s.derived_from_segment_id) return "";
  const src = preds?.find((x) => x.segment_id === s.derived_from_segment_id);
  return src && src.label === s.label && src.start_frame === s.start_frame && src.end_frame === s.end_frame ? "✓" : "✎";
};

export default function Timeline(p: Props) {
  const N = p.end - p.start;
  const pct = (f: number) => ((f - p.start) / N) * 100;
  const bodyRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<null | { mode: "draw" | "l" | "r"; side: Side; seg?: Segment; a: number; b: number }>(null);

  const frameAt = (clientX: number, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - r.left, 0), r.width);
    return Math.min(p.end - 1, p.start + Math.floor((x / r.width) * N));
  };
  // boundary (for [start,end)): nearest frame edge
  const edgeAt = (clientX: number, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - r.left, 0), r.width);
    return p.start + Math.round((x / r.width) * N);
  };

  const ticks: { f: number; ms: number; major: boolean }[] = [];
  if (p.relMs.length) {
    const lo = Math.ceil(p.relMs[0] / 100) * 100, hi = p.relMs[p.relMs.length - 1];
    for (let ms = lo; ms <= hi; ms += 100) {
      const i = lowerBound(p.relMs, ms - 1e-6);
      if (i < p.relMs.length) ticks.push({ f: p.start + i, ms, major: ms % 500 === 0 });
    }
  }

  const startDrag = (e: React.PointerEvent, mode: "draw" | "l" | "r", side: Side, seg?: Segment) => {
    e.stopPropagation(); e.preventDefault();
    const track = (e.currentTarget as HTMLElement).closest(".tl-track") as HTMLElement;
    const f0 = mode === "draw" ? frameAt(e.clientX, track) : 0;
    const st = { mode, side, seg, a: seg ? seg.start_frame : f0, b: seg ? seg.end_frame : f0 + 1 };
    setDrag(st);
    const move = (ev: PointerEvent) => {
      if (mode === "draw") {
        const f = frameAt(ev.clientX, track);
        setDrag({ ...st, a: Math.min(f0, f), b: Math.max(f0, f) + 1 });
      } else if (mode === "l") {
        setDrag({ ...st, a: Math.min(edgeAt(ev.clientX, track), st.b - 1) });
      } else {
        setDrag({ ...st, b: Math.max(edgeAt(ev.clientX, track), st.a + 1) });
      }
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
      setDrag((d) => {
        if (d) {
          if (d.mode === "draw") { if (d.b - d.a >= 2) p.onCreate(side, d.a, d.b); else p.onSeek(d.a); }
          else if (d.seg && (d.a !== d.seg.start_frame || d.b !== d.seg.end_frame)) p.onResize(d.seg, d.a, d.b);
        }
        return null;
      });
      void ev;
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };

  const renderSeg = (s: Segment) => {
    const isPred = s.kind === "prediction";
    const d = drag && drag.seg?.segment_id === s.segment_id ? drag : null;
    const a = d ? d.a : s.start_frame, b = d ? d.b : s.end_frame;
    const selected = p.sel && p.sel.id === s.segment_id;
    const nocall = isPred && !!s.no_call;
    const reviewed = isPred && (s.reviews?.length ?? 0) > 0;
    const cls = ["tl-seg", `lbl-${slug(s.label)}`, isPred ? "pred" : "", nocall ? "nocall" : "", selected ? "sel" : "", reviewed ? "reviewed" : ""].join(" ");
    const text = nocall ? "no call" : (SHORT[s.label] ?? s.label);
    const title = `${isPred ? "Prediction" : "Human"} · ${s.side} · ${nocall ? `no_call (${s.no_call_reason})` : s.label} · [${s.start_frame}, ${s.end_frame})${s.label_source ? ` · ${s.label_source}` : ""}`;
    return (
      <div key={s.segment_id} className={cls} style={{ left: `${pct(a)}%`, width: `${Math.max(0.3, pct(b) - pct(a))}%` }}
        title={title} tabIndex={0} role="button" aria-label={title} aria-pressed={!!selected}
        onPointerDown={(e) => { e.stopPropagation(); p.onSelect({ kind: isPred ? "pred" : "ann", id: s.segment_id }); p.onSeek(s.start_frame); }}
        onKeyDown={(e) => { if (e.key === "Enter") { p.onSelect({ kind: isPred ? "pred" : "ann", id: s.segment_id }); p.onSeek(s.start_frame); } }}>
        <span>{text}</span>{glyph(s, p.pred) && <span className="glyph" aria-hidden>{glyph(s, p.pred)}</span>}
        {!isPred && selected && <>
          <span className="h l" onPointerDown={(e) => startDrag(e, "l", s.side, s)} aria-hidden />
          <span className="h r" onPointerDown={(e) => startDrag(e, "r", s.side, s)} aria-hidden />
        </>}
      </div>
    );
  };

  const fencer = (side: Side) => p.fencers.find((f) => f.side === side);
  const lane = (side: Side) => (
    <>
      <div className="tl-lanelabel">
        <b>{side === "left" ? "Left" : "Right"}</b>
        <small>{fencer(side)?.display_name || fencer(side)?.athlete_id || "athlete not set"}</small>
      </div>
      <div>
        {p.events.some((e) => e.side === side) && (
          <div className="tl-evlane" aria-label={`${side} events`}>
            {(() => {
              let lastLabelPct = -100;
              return p.events.filter((e) => e.side === side && e.event_frame != null)
                .sort((a, b) => a.event_frame! - b.event_frame!).map((e) => {
                  const x = pct(e.event_frame!);
                  const showText = x - lastLabelPct > 6;
                  if (showText) lastLabelPct = x;
                  return (
                    <div key={e.event_id} className={`tl-ev ${e.source}`} style={{ left: `${x}%` }}
                      title={`${e.event_type} · ${e.source} · frame ${e.event_frame}`} onPointerDown={() => p.onSeek(e.event_frame!)}>
                      <i />{showText && <span>{EV_SHORT[e.event_type] ?? e.event_type}</span>}
                    </div>
                  );
                });
            })()}
          </div>
        )}
        {p.pred && (
          <div className="tl-track pred" aria-label={`${side} predictions`}
            onPointerDown={(e) => p.onSeek(frameAt(e.clientX, e.currentTarget))}>
            {p.pred.filter((s) => s.side === side).map(renderSeg)}
          </div>
        )}
        <div className="tl-track" aria-label={`${side} human labels — drag to add`}
          onPointerDown={(e) => startDrag(e, "draw", side)}>
          {p.ann.filter((s) => s.side === side).map(renderSeg)}
          {drag && drag.mode === "draw" && drag.side === side && (
            <div className="tl-drawing" style={{ left: `${pct(drag.a)}%`, width: `${pct(drag.b) - pct(drag.a)}%` }} />
          )}
        </div>
      </div>
    </>
  );

  return (
    <div className="timeline panel">
      <div className="tl-head">
        <h4>Action timeline</h4>
        <div className="tl-legend">
          <span><i className="lamp" style={{ border: "1.5px dashed #888" }} /> prediction</span>
          <span><i className="lamp" style={{ background: "#d3e0c6" }} /> human label</span>
          <span>✓ confirmed · ✎ corrected · ✕ rejected</span>
          <span>drag on a human row to add <b style={{ color: "var(--ink)" }}>{SHORT[p.drawLabel] ?? p.drawLabel}</b></span>
        </div>
      </div>
      <div className="tl-grid">
        <div className="tl-sub" style={{ alignSelf: "end" }}>rel. light</div>
        <div className="tl-ruler" onPointerDown={(e) => p.onSeek(frameAt(e.clientX, e.currentTarget))}>
          {ticks.map((t) => (
            <div key={t.ms} className={`tl-tick ${t.major ? "" : "minor"}`} style={{ left: `${pct(t.f)}%` }}>
              {t.major ? <span className="num" style={t.ms === 0 ? { color: "#8e3328", fontWeight: 600 } : undefined}>{t.ms === 0 ? "light" : `${(t.ms / 1000).toFixed(1)}s`}</span> : null}
            </div>
          ))}
        </div>
      </div>
      <div className="tl-body">
        <div className="tl-grid">{lane("left")}</div>
        <div className="tl-grid">{lane("right")}</div>
        <div style={{ position: "absolute", left: 120, right: 0, top: 0, bottom: 0, pointerEvents: "none" }}>
          <div className="tl-light" style={{ left: `${pct(p.light)}%` }} />
          {p.current >= p.start && p.current < p.end && <div className="tl-playhead" style={{ left: `${pct(p.current + 0.5)}%` }} />}
        </div>
      </div>
    </div>
  );
}
