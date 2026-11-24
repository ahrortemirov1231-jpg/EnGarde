import { useState } from "react";
import { LogIn } from "lucide-react";
import { api, Me } from "./api";

export default function Login({ me, onDone }: { me: Me; onDone: () => void }) {
  const [name, setName] = useState(""), [pw, setPw] = useState("");
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { await api.login(name.trim(), pw); onDone(); }
    catch (e2: any) { setErr(e2.message); setBusy(false); }
  };
  return (
    <div className="login-wrap">
      <form className="login panel" onSubmit={go}>
        <div className="wordmark">EN <span>GARDE</span><small className="ver">v{me.version}</small></div>
        <h1>Sign in</h1>
        <p>Team workspace for Proving the AI Referee. Your labels and reviews are saved under your name.</p>
        {me.needs_first_admin && <div className="issue">No account exists yet. On the computer that runs the server, close this window's server and start serve_team.bat again: it asks for the first admin's name and password.</div>}
        <div><label className="lab" htmlFor="ln">Name</label><input id="ln" className="field" autoComplete="username" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><label className="lab" htmlFor="lp">Password</label><input id="lp" className="field" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></div>
        {err && <div className="issue danger" role="alert">{err}</div>}
        <button className="btn primary" disabled={busy || !name.trim() || !pw}><LogIn size={15} /> {busy ? "Signing in…" : "Sign in"}</button>
        <p className="fine">No account or forgot the password? Ask a team admin. They can add you or set a new password on the Team page.</p>
      </form>
    </div>
  );
}
