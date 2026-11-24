import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Overlay, Side } from "./api";

export interface PlayerHandle { video: HTMLVideoElement | null }
interface Props {
  videoId: string; touchId: string | null; width: number; height: number; contentRect?: number[] | null;
  frame: number; exact: boolean; framesReady: boolean; inWindow: boolean;
  overlay: Overlay | null; show: { skel: boolean; box: boolean; quality: boolean; people?: boolean };
  people?: Record<string, number[][]> | null; chosen?: { left: number | null; right: number | null } | null;
  /** tracker id -> stable number (1 = left fencer, 2 = right fencer, 3+ = others) */
  display?: Record<string, number> | null; names?: { left?: string | null; right?: string | null };
  onVideoTime: (t: number) => void; onEnded: () => void; synthetic: boolean; stageBadges: React.ReactNode;
}

// COCO-17 bones
const BONES = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [11, 12], [5, 11], [6, 12], [11, 13], [13, 15], [12, 14], [14, 16], [0, 5], [0, 6]];
const COL: Record<Side, string> = { left: "#ff8a7a", right: "#7fe0a2" };

const Player = forwardRef<PlayerHandle, Props>(function Player(p, ref) {
  const vref = useRef<HTMLVideoElement>(null);
  const cref = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [imgOk, setImgOk] = useState(true);
  const [box, setBox] = useState({ W: 0, H: 0 });
  useEffect(() => {
    const el = wrap.current; if (!el) return;
    const ro = new ResizeObserver(() => setBox({ W: el.clientWidth, H: el.clientHeight }));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  // display-only fit: the content rectangle (without baked-in letterbox bars) fills the stage
  const cr = p.contentRect && p.contentRect[2] > 0 ? p.contentRect : [0, 0, p.width, p.height];
  const sc = box.W && box.H ? Math.min(box.W / cr[2], box.H / cr[3]) : 0;
  const mediaStyle: React.CSSProperties = sc ? {
    position: "absolute", width: p.width * sc, height: p.height * sc,
    left: (box.W - cr[2] * sc) / 2 - cr[0] * sc, top: (box.H - cr[3] * sc) / 2 - cr[1] * sc, objectFit: "fill",
    // right / bottom, NOT the "inset" shorthand: React writes style keys in order, so "inset: auto" written
    // after left / top erased them whenever the exact-frame image was created again (after every pause).
    // The picture then sat in the corner of the stage while skeleton and boxes were drawn where it should be.
    right: "auto", bottom: "auto",
  } : {};
  useImperativeHandle(ref, () => ({ get video() { return vref.current; } }), []);

  // draw overlay
  useEffect(() => {
    const c = cref.current, w = wrap.current;
    if (!c || !w) return;
    const dpr = window.devicePixelRatio || 1;
    const W = w.clientWidth, H = w.clientHeight;
    c.width = W * dpr; c.height = H * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const ov = p.overlay;
    // every tracked person (track number), to check or fix who the fencers are
    const drawPeople = (s: number, ox: number, oy: number) => {
      const list = p.people?.[String(p.frame)] ?? [];
      g.font = "600 12px Manrope, sans-serif";
      for (const [tid, x1, y1, x2, y2] of list) {
        const isL = p.chosen?.left === tid, isR = p.chosen?.right === tid;
        g.setLineDash(isL || isR ? [] : [5, 4]);
        g.strokeStyle = isL ? COL.left : isR ? COL.right : "rgba(255,255,255,.75)";
        g.lineWidth = 1.5;
        g.strokeRect(ox + x1 * s, oy + y1 * s, (x2 - x1) * s, (y2 - y1) * s);
        const tag = `#${p.display?.[String(tid)] ?? tid}`; const tw = g.measureText(tag).width + 8;
        g.setLineDash([]); g.fillStyle = "rgba(18,22,20,.8)";
        g.fillRect(ox + x2 * s - tw, oy + y1 * s, tw, 17);
        g.fillStyle = "#fff"; g.fillText(tag, ox + x2 * s - tw + 4, oy + y1 * s + 13);
      }
    };
    if (p.show.people && p.people) {
      const s0 = sc || Math.min(W / p.width, H / p.height);
      const ox0 = sc ? (W - cr[2] * sc) / 2 - cr[0] * sc : (W - p.width * s0) / 2;
      const oy0 = sc ? (H - cr[3] * sc) / 2 - cr[1] * sc : (H - p.height * s0) / 2;
      drawPeople(s0, ox0, oy0);
    }
    if (!ov?.available || !ov.frames || !ov.sides) return;
    const i = ov.frames.indexOf(p.frame);
    if (i < 0) return;
    const s = sc || Math.min(W / p.width, H / p.height);
    const ox = sc ? (W - cr[2] * sc) / 2 - cr[0] * sc : (W - p.width * s) / 2;
    const oy = sc ? (H - cr[3] * sc) / 2 - cr[1] * sc : (H - p.height * s) / 2;
    const X = (x: number) => ox + x * s, Y = (y: number) => oy + y * s;
    (["left", "right"] as Side[]).forEach((side) => {
      const sd = ov.sides![side];
      const bb = sd.bbox[i], kp = sd.kp[i], fl = sd.flags[i];
      g.lineWidth = 2;
      if (bb && p.show.box) {
        g.strokeStyle = COL[side]; g.setLineDash(fl?.identity_uncertain ? [6, 4] : []);
        g.strokeRect(X(bb[0]), Y(bb[1]), (bb[2] - bb[0]) * s, (bb[3] - bb[1]) * s);
        g.setLineDash([]);
        g.font = "600 12px Manrope, sans-serif"; g.fillStyle = "rgba(18,22,20,.78)";
        const who = p.names?.[side];
        const tag = `${side === "left" ? 1 : 2} · ${side.toUpperCase()}${who ? " · " + who : ""}${fl && fl.screen_position !== side && fl.screen_position !== "absent" ? " (on screen " + fl.screen_position + ")" : ""}`;
        const tw = g.measureText(tag).width + 10;
        g.fillRect(X(bb[0]), Y(bb[1]) - 20, tw, 18); g.fillStyle = COL[side]; g.fillText(tag, X(bb[0]) + 5, Y(bb[1]) - 7);
      }
      if (kp && p.show.skel) {
        g.strokeStyle = COL[side]; g.lineWidth = 2.5;
        for (const [a, b] of BONES) {
          const A = kp[a], B = kp[b];
          if (A && B && A[3] && B[3]) { g.beginPath(); g.moveTo(X(A[0]), Y(A[1])); g.lineTo(X(B[0]), Y(B[1])); g.stroke(); }
        }
        for (const q of kp) {
          if (!q) continue;
          g.beginPath(); g.arc(X(q[0]), Y(q[1]), 3, 0, Math.PI * 2);
          if (q[3]) { g.fillStyle = COL[side]; g.fill(); } else { g.strokeStyle = "rgba(255,255,255,.55)"; g.lineWidth = 1; g.stroke(); }
        }
      }
      if (p.show.quality && fl) {
        const msgs = [fl.track_break && "track break", fl.id_switch && "ID switch?", (fl as any).pair_implausible && "not a plausible fencer pair", fl.identity_uncertain && "identity uncertain"].filter(Boolean) as string[];
        if (!bb) msgs.unshift(`${side}: not detected`);
        if (msgs.length) {
          g.font = "600 12px Manrope, sans-serif";
          const txt = msgs.join(" · "); const tw = g.measureText(txt).width + 14;
          const x0 = bb ? X(bb[0]) : side === "left" ? 16 : W - tw - 16, y0 = bb ? Y(bb[3]) + 6 : H - 40;
          g.fillStyle = "rgba(246,229,225,.95)"; g.fillRect(x0, y0, tw, 20);
          g.fillStyle = "#7d2d23"; g.fillText(txt, x0 + 7, y0 + 14);
        }
      }
    });
  });

  const exactSrc = p.touchId && p.framesReady && p.inWindow ? `/api/touches/${p.touchId}/frames/${p.frame}.jpg` : null;
  useEffect(() => setImgOk(true), [exactSrc]);
  return (
    <div className="stage" ref={wrap} style={{ aspectRatio: `${cr[2]} / ${cr[3]}` }}>
      <video ref={vref} style={mediaStyle} src={`/api/videos/${p.videoId}/stream`} preload="auto" playsInline muted
        onTimeUpdate={(e) => p.onVideoTime(e.currentTarget.currentTime)} onEnded={p.onEnded}
        aria-label="Bout video" />
      {p.exact && exactSrc && imgOk && <img className="exact" style={mediaStyle} src={exactSrc} alt={`Decoded frame ${p.frame}`} onError={() => setImgOk(false)} />}
      <canvas ref={cref} aria-hidden />
      <div className="stage-badge">
        {p.synthetic && <span className="badge synthetic">SYNTHETIC FIXTURE · not real footage</span>}
        {p.exact && exactSrc && imgOk ? <span className="badge">exact decoded frame</span>
          : <span className="badge warn">{p.exact ? (p.inWindow ? "frames not extracted yet · approximate" : "outside touch window · approximate") : "playing · frame ≈ from currentTime"}</span>}
        {p.stageBadges}
      </div>
    </div>
  );
});
export default Player;
