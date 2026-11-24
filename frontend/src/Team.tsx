import { useCallback, useEffect, useState } from "react";
import { Check, KeyRound, UserPlus, X, HardDriveDownload } from "lucide-react";
import { api, UnlockRequest, User } from "./api";
import { useApp } from "./App";

const ROLE_HELP: Record<string, string> = {
  admin: "everything, plus accounts, deleting videos and unlocking",
  member: "upload, label, review, lock, train",
  viewer: "look only",
};

export default function Team() {
  const { toast, user } = useApp();
  const admin = user?.role === "admin";
  const [users, setUsers] = useState<User[]>([]);
  const [reqs, setReqs] = useState<UnlockRequest[]>([]);
  const [nu, setNu] = useState({ name: "", password: "", role: "member" });
  const [pw, setPw] = useState({ old: "", next: "" });
  const [reset, setReset] = useState<User | null>(null);
  const [resetPw, setResetPw] = useState("");
  const load = useCallback(() => {
    if (admin) api.users().then(setUsers).catch((e) => toast(e.message, true));
    api.unlockRequests().then(setReqs).catch(() => {});
  }, [admin, toast]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const act = async (fn: () => Promise<unknown>, ok: string) => { try { await fn(); toast(ok); load(); return true; } catch (e: any) { toast(e.message, true); return false; } };

  return (
    <main className="page">
      <div className="page-head"><div style={{ flex: 1 }}>
        <h1>Team</h1>
        <p>{admin ? "Who can sign in, what they can do, and requests waiting for you." : "Your account, and your requests to unlock videos."}</p>
      </div></div>

      {(reqs.length > 0 || admin) && (
        <section className="card panel" style={{ marginBottom: 20 }}>
          <h3>{admin ? "Requests to unlock a video" : "Your unlock requests"}</h3>
          {reqs.length === 0 ? <p className="unavail">Nothing is waiting.</p> : (
            <table className="t">
              <thead><tr><th>Video</th><th>Asked by</th><th>Reason</th><th>Locked by</th><th /></tr></thead>
              <tbody>{reqs.map((r) => (
                <tr key={r.request_id}>
                  <td>{r.video_title ?? r.video_id}</td><td>{r.requested_by}</td><td>{r.reason ?? "—"}</td><td>{r.locked_by ?? "—"}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{admin ? <>
                    <button className="btn sm primary" onClick={() => act(() => api.decideUnlock(r.request_id, true), "unlocked")}><Check size={14} /> Unlock</button>{" "}
                    <button className="btn sm" onClick={() => act(() => api.decideUnlock(r.request_id, false), "request refused")}><X size={14} /> Refuse</button>
                  </> : <span className="unavail">waiting for an admin</span>}</td>
                </tr>))}</tbody>
            </table>
          )}
        </section>
      )}

      {admin && (
        <section className="card panel" style={{ marginBottom: 20 }}>
          <h3>People</h3>
          <table className="t">
            <thead><tr><th>Name</th><th>Can do</th><th>Last sign-in</th><th>Sign-in allowed</th><th /></tr></thead>
            <tbody>{users.map((u) => (
              <tr key={u.user_id} style={u.active ? undefined : { opacity: .55 }}>
                <td>{u.name}{u.user_id === user?.user_id ? <span className="unavail"> (you)</span> : null}</td>
                <td>
                  <select className="field" style={{ width: 130, height: 32 }} value={u.role} aria-label={`Role of ${u.name}`}
                    onChange={(e) => act(() => api.patchUser(u.user_id, { role: e.target.value }), `${u.name} is now ${e.target.value}`)}>
                    {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                  <span className="unavail" style={{ marginLeft: 8 }}>{ROLE_HELP[u.role]}</span>
                </td>
                <td className="num">{u.last_login_at ? u.last_login_at.slice(0, 16).replace("T", " ") : "never"}</td>
                <td><label className="toggle"><input type="checkbox" checked={!!u.active} onChange={(e) => act(() => api.patchUser(u.user_id, { active: e.target.checked }), e.target.checked ? `${u.name} can sign in again` : `${u.name} can no longer sign in`)} />{u.active ? "yes" : "no"}</label></td>
                <td style={{ textAlign: "right" }}><button className="btn sm ghost" onClick={() => { setReset(u); setResetPw(""); }}><KeyRound size={14} /> New password</button></td>
              </tr>))}</tbody>
          </table>
          <h4 style={{ margin: "18px 0 8px", fontSize: 13 }}>Add a person</h4>
          <form className="adduser" onSubmit={async (e) => { e.preventDefault(); if (await act(() => api.addUser(nu), `account for ${nu.name} created`)) setNu({ name: "", password: "", role: "member" }); }}>
            <input className="field" placeholder="Name (shown on their labels)" aria-label="Name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} />
            <input className="field" placeholder="First password (8 or more characters)" aria-label="First password" type="text" autoComplete="off" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
            <select className="field" aria-label="Role" value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>{Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}</select>
            <button className="btn primary" disabled={!nu.name.trim() || nu.password.length < 8}><UserPlus size={15} /> Add</button>
          </form>
          <p className="unavail" style={{ marginTop: 8 }}>Tell each person their name and first password in a private message, and ask them to change it below after signing in.</p>
        </section>
      )}

      <div className="cards2">
        <section className="card panel">
          <h3>Your password</h3>
          <form className="formgrid" onSubmit={async (e) => { e.preventDefault(); if (await act(() => api.changePassword(pw.old, pw.next), "password changed · other devices were signed out")) setPw({ old: "", next: "" }); }}>
            <div><label className="lab" htmlFor="po">Current password</label><input id="po" className="field" type="password" autoComplete="current-password" value={pw.old} onChange={(e) => setPw({ ...pw, old: e.target.value })} /></div>
            <div><label className="lab" htmlFor="pn">New password (8 or more)</label><input id="pn" className="field" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></div>
            <button className="btn full" disabled={!pw.old || pw.next.length < 8}>Change password</button>
          </form>
        </section>
        {admin && (
          <section className="card panel">
            <h3>Backups</h3>
            <p className="sub">The database and a labels file are copied to data/backups a minute after the server starts and every 6 hours. The newest 14 are kept.</p>
            <button className="btn" onClick={() => act(async () => { const r = await api.backupNow(); if (r.copy_error) throw new Error(`saved here, but the second copy failed: ${r.copy_error}`); }, "backup made")}><HardDriveDownload size={15} /> Back up now</button>
          </section>
        )}
      </div>

      {reset && (
        <div className="modal-back" onClick={() => setReset(null)}>
          <div className="modal panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="New password">
            <h3>New password for {reset.name}</h3>
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>They are signed out everywhere and sign in again with this password.</p>
            <input className="field" autoFocus placeholder="8 or more characters" aria-label="New password" value={resetPw} onChange={(e) => setResetPw(e.target.value)} />
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setReset(null)}>Cancel</button>
              <button className="btn primary" disabled={resetPw.length < 8} onClick={async () => { if (await act(() => api.patchUser(reset.user_id, { password: resetPw }), `new password set for ${reset.name}`)) setReset(null); }}>Set password</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
