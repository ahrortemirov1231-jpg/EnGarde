import { Fragment, useCallback, useEffect, useState } from "react";
import { Check, Circle } from "lucide-react";
import { api, Readiness } from "./api";
import { useApp } from "./App";

const METHOD_NAME: Record<string, string> = { xgboost: "XGBoost (E1)", tcn: "Bidirectional TCN (E2)" };
const f3 = (x: number | null | undefined) => (x == null ? "—" : x.toFixed(3));

function Ci({ ci }: { ci: any }) {
  if (!ci) return <span className="unavail">—</span>;
  if (!ci.ci95) return <span className="unavail" title={ci.reason}>no interval</span>;
  return (
    <span title={ci.reason ?? "95% interval, resampled by bout (PRD §10.2)"}>
      {f3(ci.ci95[0])}–{f3(ci.ci95[1])}
      {!ci.reliable && <span className="model-flag">unreliable</span>}
    </span>
  );
}

function Calibration({ m }: { m: any }) {
  const rep = m.metrics.test?.calibration ?? m.metrics.calibration;
  if (!rep || !rep.available) return <p className="unavail">No calibration report: it needs at least 3 development bouts (temperature scaling, PRD §9.1).</p>;
  return (
    <div className="calib">
      <div className="calib-head">
        <b>Calibration</b> · {rep.set}{rep.caveat ? <span className="unavail"> ({rep.caveat})</span> : null}
      </div>
      <table className="t num">
        <thead><tr><th>Confidence</th><th className="r">n</th><th className="r">Accuracy</th><th className="r">Mean conf.</th></tr></thead>
        <tbody>{rep.bins.map((b: any) => (
          <tr key={b.bin}><td>{b.bin}</td><td className="r">{b.n}</td><td className="r">{f3(b.accuracy)}</td><td className="r">{f3(b.mean_confidence)}</td></tr>
        ))}</tbody>
      </table>
      <div className="calib-foot">
        ECE {f3(rep.ece)} · Brier {f3(rep.brier)} · {(rep.share_above_0_9 * 100).toFixed(0)}% of predictions ≥ 0.9
        {rep.overconfidence_alarm && <span className="model-flag bad">over-confidence alarm</span>}
        {rep.show_confidence_to_coaches ? <span className="model-flag good">confidence can be shown</span>
          : <span className="model-flag bad">hide confidence from coaches</span>}
      </div>
    </div>
  );
}

export default function ModelsPanel() {
  const { toast, annotator } = useApp();
  const [models, setModels] = useState<any[]>([]);
  const [bouts, setBouts] = useState<{ bout_id: string; video_id: string; name: string; split?: string | null }[]>([]);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [cmp, setCmp] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    api.models().then(setModels).catch(() => {});
    api.bouts().then(setBouts).catch(() => {});
    api.readiness().then(setReady).catch(() => setReady(null));
  }, []);
  const setSplit = async (videoId: string, test: boolean) => {
    try { await api.patchVideo(videoId, { split: test ? "test" : "train", actor: annotator }); load(); } catch (e: any) { toast(e.message, true); }
  };
  useEffect(load, [load]);

  const latest = (method: string) => models.find((m) => m.method === method);
  const e1 = latest("xgboost"), e2 = latest("tcn");
  useEffect(() => {
    if (e1 && e2) api.compareModels(e1.model_id, e2.model_id).then(setCmp).catch(() => setCmp(null));
    else setCmp(null);
  }, [e1?.model_id, e2?.model_id]); // eslint-disable-line

  const train = async (method: "xgboost" | "tcn") => {
    setBusy(method);
    try {
      const job = await api.train({ method });   // test bouts = videos marked "Test"
      toast(`${METHOD_NAME[method]} training started`);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const j = await api.job(job.job_id);
        if (j.status === "completed") { toast(`${METHOD_NAME[method]} trained`); break; }
        if (j.status === "failed") { toast(j.message ?? "training failed", true); break; }
      }
      load();
    } catch (e: any) {
      toast(e.message, true);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card panel models-panel">
      <h3>Models</h3>
      <p className="sub">
        E1 (XGBoost) and E2 (bidirectional TCN) are trained under one protocol: leave-one-bout-out cross-validation,
        frozen test bouts scored once on blind labels only, and no_call counted as an error. Intervals are resampled by bout.
      </p>

      <div className="train-row">
        <div className="test-bouts">
          <span className="unavail">Test videos (never trained on; same setting as “Use for” on the Videos page):</span>
          {bouts.length === 0 ? <span className="unavail"> no bouts yet</span> : bouts.map((b) => (
            <label key={b.bout_id} className="chk">
              <input type="checkbox" checked={b.split === "test"} onChange={(e) => setSplit(b.video_id, e.target.checked)} />
              {b.name}
            </label>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn sm" disabled={!!busy || ready?.can_train === false} onClick={() => train("xgboost")}>{busy === "xgboost" ? "Training E1…" : "Train XGBoost (E1)"}</button>
          <button className="btn sm" disabled={!!busy || ready?.can_train === false} onClick={() => train("tcn")}>{busy === "tcn" ? "Training E2…" : "Train TCN (E2)"}</button>
        </div>
      </div>

      {ready && (
        <div className="ready">
          <div className="ready-head">
            <b>What the labels support right now</b>
            <span className="unavail"> · {ready.train_bouts.length} training bout{ready.train_bouts.length === 1 ? "" : "s"}, {ready.n_train_touches} labelled touches, {ready.test_bouts.length} test bout{ready.test_bouts.length === 1 ? "" : "s"}</span>
          </div>
          {ready.why_not && <div className="issue" style={{ margin: "6px 0" }}>{ready.why_not}</div>}
          <ul className="ready-steps">
            {ready.steps.map((s) => (
              <li key={s.need} className={s.ok ? "ok" : ""}>{s.ok ? <Check size={14} /> : <Circle size={12} />}<span><b>{s.need}</b>: {s.gives}</span></li>
            ))}
          </ul>
          {Object.keys(ready.frames_per_label).length > 0 && (
            <p className="unavail" style={{ margin: "6px 0 0" }}>Labelled frames per label: {Object.entries(ready.frames_per_label).map(([k, n]) => `${k} ${n}`).join(" · ")}. Training never waits for more data; a small set only means fewer of the numbers above.</p>
          )}
        </div>
      )}

      {models.length === 0 ? (
        <p className="unavail">No model trained. The current pre-labels are the rule baseline (unvalidated); no model performance exists to report.</p>
      ) : (
        <table className="t num">
          <thead><tr><th>Model</th><th className="r">CV Macro-F1</th><th className="r">95% CI (by bout)</th><th className="r">Test F1</th><th className="r">Coverage</th><th className="r">Abstain</th><th /></tr></thead>
          <tbody>{models.map((m) => {
            const cv = m.metrics.cv_summary, te = m.metrics.test;
            return (
              <Fragment key={m.model_id}>
                <tr>
                  <td>{METHOD_NAME[m.method] ?? m.method}<div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{m.model_id} · {m.dataset_version}</div></td>
                  <td className="r">{cv ? `${f3(cv.macro_f1_all_frames.mean)} ± ${f3(cv.macro_f1_all_frames.std)}` : "unavailable"}</td>
                  <td className="r"><Ci ci={m.metrics.cv_pooled_ci} /></td>
                  <td className="r">{te?.macro_f1_all_frames != null ? f3(te.macro_f1_all_frames) : "—"}</td>
                  <td className="r">{cv ? f3(cv.coverage.mean) : "—"}</td>
                  <td className="r">{cv ? f3(cv.abstention_rate.mean) : "—"}</td>
                  <td><button className="btn sm ghost" onClick={() => setOpen(open === m.model_id ? null : m.model_id)}>{open === m.model_id ? "Hide" : "Details"}</button></td>
                </tr>
                {open === m.model_id && (
                  <tr><td colSpan={7}>
                    {m.metrics.cv_mode === "within_bout_by_touch" && <p className="unavail">This score comes from one bout only (touches held out in turn). It will look better than the model really is.</p>}
                    {(m.metrics.warnings ?? []).length > 0 && <ul className="warn-list">{m.metrics.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
                    <Calibration m={m} />
                  </td></tr>
                )}
              </Fragment>
            );
          })}</tbody>
        </table>
      )}

      {cmp && (
        <div className="compare">
          <h4>E1 vs E2 (deliverable T2)</h4>
          {!cmp.available ? <p className="unavail">{cmp.reason}</p> : (
            <>
              <div className="compare-nums num">
                <div><div className="bignum">{f3(cmp.E1)}</div><div className="bignum-l">E1 Macro-F1</div></div>
                <div><div className="bignum">{f3(cmp.E2)}</div><div className="bignum-l">E2 Macro-F1</div></div>
                <div><div className="bignum">{cmp.difference >= 0 ? "+" : ""}{f3(cmp.difference)}</div>
                  <div className="bignum-l">difference{cmp.difference_ci95 ? ` (95% CI ${f3(cmp.difference_ci95[0])} to ${f3(cmp.difference_ci95[1])})` : ""}</div></div>
              </div>
              <p><b>Decision: {cmp.decision}</b> — {cmp.decision_reason}. <span className="unavail">{cmp.rule}</span></p>
              {cmp.warning && <p className="unavail">{cmp.warning}</p>}
              {!cmp.same_dataset && <p className="unavail">The two models were trained on different label versions; retrain both before relying on this comparison.</p>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
