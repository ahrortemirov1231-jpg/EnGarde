import { useEffect, useState } from "react";
import { api } from "./api";
import { useApp } from "./App";
import ModelsPanel from "./Models";

const METRIC_NAMES: Record<string, string> = {
  launch_distance_m: "Launch distance", strip_position_m: "Strip position", advance_to_lunge_ms: "Advance → lunge interval",
  hand_vs_foot_ms: "Hand vs foot timing", recovery_duration_ms: "Recovery duration", torso_lean_at_lunge_land: "Torso lean at lunge land",
};

export default function Insights() {
  const { toast } = useApp();
  const [source, setSource] = useState("annotations");
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.insights(`source=${source}`).then(setD).catch((e) => toast(e.message, true)); }, [source, toast]);
  const LABELS: string[] = d?.labels ?? [];
  const total = (side: string) => d ? LABELS.reduce((n, l) => n + (d.by_side[side]?.[l]?.count ?? 0), 0) : 0;
  const maxCount = d ? Math.max(1, ...["left", "right"].flatMap((s) => LABELS.map((l) => d.by_side[s]?.[l]?.count ?? 0))) : 1;

  return (
    <main className="page">
      <div className="page-head">
        <div style={{ flex: 1 }}>
          <h1>Insights</h1>
          <p>Descriptive counts from {source === "annotations" ? "human labels" : "predictions that passed the quality gate"}. Metrics that need missing inputs are shown as unavailable, never as zero.</p>
        </div>
        <select className="field" style={{ width: 240 }} value={source} onChange={(e) => setSource(e.target.value)} aria-label="Data source">
          <option value="annotations">Human labels</option><option value="predictions">Predictions (unvalidated)</option>
        </select>
      </div>
      {!d ? null : d.n_segments === 0 ? (
        <div className="empty panel"><h3>Nothing to summarise yet</h3><p>{source === "annotations" ? "Label or confirm some segments in Review." : "Run analysis on a touch first."}</p></div>
      ) : (
        <>
          <section className="overview glass" aria-label="Overview">
            <div><div className="bignum num">{d.n_touches}</div><div className="bignum-l">touches</div></div>
            <div><div className="bignum num">{d.n_segments}</div><div className="bignum-l">segments</div></div>
            <div><div className="bignum num">{d.labelled_frame_fraction != null ? `${(d.labelled_frame_fraction * 100).toFixed(d.labelled_frame_fraction < 0.1 ? 1 : 0)}%` : "—"}</div><div className="bignum-l">of window frames covered</div></div>
            {d.label_sources && <div><div className="bignum num">{d.label_sources.blind ?? 0}<span style={{ fontSize: 20, color: "var(--ink-3)" }}> / {(d.label_sources.model_assisted ?? 0)}</span></div><div className="bignum-l">blind / model-assisted segments</div></div>}
          </section>
          <div className="cards2">
            <section className="card panel">
              <h3>Actions per fencer</h3>
              <p className="sub">Count, and median duration in ms. Durations use real frame timestamps.</p>
              <table className="t num">
                <thead><tr><th>Label</th><th className="r">Left</th><th></th><th className="r">Right</th><th></th></tr></thead>
                <tbody>
                  {LABELS.map((l) => {
                    const L = d.by_side.left?.[l], R = d.by_side.right?.[l];
                    return (
                      <tr key={l}>
                        <td>{l}</td>
                        <td className="r">{L?.count ?? 0}<div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{L?.median_ms != null ? `${L.median_ms.toFixed(0)} ms` : "—"}</div></td>
                        <td style={{ width: "22%" }}><div className="bar" style={{ width: `${((L?.count ?? 0) / maxCount) * 100}%` }} /></td>
                        <td className="r">{R?.count ?? 0}<div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{R?.median_ms != null ? `${R.median_ms.toFixed(0)} ms` : "—"}</div></td>
                        <td style={{ width: "22%" }}><div className="bar" style={{ width: `${((R?.count ?? 0) / maxCount) * 100}%`, background: "#2f6f45" }} /></td>
                      </tr>
                    );
                  })}
                  <tr><td><b>Total</b></td><td className="r"><b>{total("left")}</b></td><td /><td className="r"><b>{total("right")}</b></td><td /></tr>
                </tbody>
              </table>
            </section>
            <section className="card panel">
              <h3>Coaching metrics</h3>
              <p className="sub">PRD §12.2. Computed from manual events first; only when the inputs exist.</p>
              {Object.entries(d.metrics).map(([k, m]: [string, any]) => (
                <div key={k} className="metric-row">
                  <span>{METRIC_NAMES[k] ?? k}</span>
                  {m.available ? <span className="num"><b>{m.median}</b> ms median · n={m.n}</span> : <span className="unavail" style={{ textAlign: "right", maxWidth: "60%" }}>unavailable — {m.reason}</span>}
                </div>
              ))}
            </section>
          </div>
        </>
      )}
      <ModelsPanel />
    </main>
  );
}
