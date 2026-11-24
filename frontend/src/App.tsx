import { useEffect, useRef, useState, createContext, useContext, useCallback } from "react";
import { Download, ChevronDown, LogOut, UserRound } from "lucide-react";
import { api, Config, Job, Me, User } from "./api";
import Review from "./Review";
import Videos from "./Videos";
import Insights from "./Insights";
import Login from "./Login";
import Home from "./Home";
import Team from "./Team";

type ToastT = { id: number; msg: string; err?: boolean };
export const Ctx = createContext<{
  toast: (m: string, err?: boolean) => void; config: Config | null; annotator: string; setAnnotator: (a: string) => void;
  refreshJobs: () => void; user: User | null; teamMode: boolean;
}>({ toast: () => {}, config: null, annotator: "coach", setAnnotator: () => {}, refreshJobs: () => {}, user: null, teamMode: false });
export const useApp = () => useContext(Ctx);

function useHash() {
  const [h, setH] = useState(window.location.hash || "#/videos");
  useEffect(() => {
    const f = () => setH(window.location.hash || "#/videos");
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return h;
}

const store = {
  get(k: string, d: string) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};
export { store };

export default function App() {
  const hash = useHash();
  const [toasts, setToasts] = useState<ToastT[]>([]);
  const [config, setConfig] = useState<Config | null>(null);
  const [localAnnotator, setAnn] = useState(store.get("engarde.annotator", "coach"));
  // team server: who is signed in. null = not asked yet.
  const [me, setMe] = useState<Me | null>(null);
  const loadMe = useCallback(() => api.me().then(setMe).catch(() => setMe({ team_mode: false, user: null, version: "", needs_first_admin: false })), []);
  useEffect(() => { loadMe(); const f = () => loadMe(); window.addEventListener("engarde:signin", f); return () => window.removeEventListener("engarde:signin", f); }, [loadMe]);
  const teamMode = !!me?.team_mode, user = me?.user ?? null;
  const signedIn = !!me && (!teamMode || !!user);
  const annotator = teamMode && user ? user.name : localAnnotator;   // on the team server your name is your account
  const [jobs, setJobs] = useState<Job[]>([]);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const toast = useCallback((msg: string, err = false) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, err }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), err ? 7000 : 3200);
  }, []);
  const refreshJobs = useCallback(() => { if (signedIn) api.jobs().then(setJobs).catch(() => {}); }, [signedIn]);
  useEffect(() => {
    if (!signedIn) return;
    api.config().then((c) => {
      setConfig(c);
      if (c.label_migration?.renamed && store.get("engarde.renameSeen", "") !== "0.4.0") {
        store.set("engarde.renameSeen", "0.4.0");
        toast(`${c.label_migration.renamed} existing labels were renamed to Tim's label names. Nothing was lost: each change is in the label history.`);
      }
    }).catch((e) => toast(`backend unreachable: ${e.message}`, true));
  }, [toast, signedIn]);
  useEffect(() => { refreshJobs(); const t = setInterval(() => { if (!document.hidden) refreshJobs(); }, 2500); return () => clearInterval(t); }, [refreshJobs]);
  useEffect(() => {
    const f = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", f); return () => document.removeEventListener("mousedown", f);
  }, []);
  const setAnnotator = (a: string) => { if (teamMode) return; setAnn(a); store.set("engarde.annotator", a); };
  const signOut = async () => { try { await api.logout(); } catch { /* already out */ } setJobs([]); loadMe(); };

  // "#/review?video=vid_1" -> page "review"; "#/review/tch_1?frame=5" -> page "review", touch "tch_1".
  // (Before v0.4.0 the "?video=..." part was read as part of the page name, so the Review button on a
  // video card fell back to the Videos page and only the first video could ever be opened.)
  const route = hash.replace(/^#\/?/, "");
  const [page, ...rest] = route.split("?")[0].split("/");
  const running = jobs.filter((j) => j.status === "running" || j.status === "queued");
  const failed = jobs.filter((j) => j.status === "failed").slice(0, 1);
  const videoParam = (hash.match(/video=([\w]+)/) || [])[1];

  if (!me) return null;
  if (!signedIn) return (
    <>
      <div className="ambient" aria-hidden><i className="a1" /><i className="a2" /><i className="a3" /></div>
      <Login me={me} onDone={loadMe} />
    </>
  );

  return (
    <Ctx.Provider value={{ toast, config, annotator, setAnnotator, refreshJobs, user, teamMode }}>
      <div className="ambient" aria-hidden><i className="a1" /><i className="a2" /><i className="a3" /></div>
      <div className="app">
        <header className="topnav glass">
          <div className="wordmark">EN <span>GARDE</span>{config?.version && <small className="ver">v{config.version}</small>}</div>
          <nav className="navlinks" aria-label="Main">
            <a href="#/home" className={page === "home" ? "active" : ""}>Home</a>
            <a href="#/videos" className={page === "videos" || page === "" ? "active" : ""}>Videos</a>
            <a href="#/review" className={page === "review" ? "active" : ""}>Review</a>
            <a href="#/insights" className={page === "insights" ? "active" : ""}>Insights</a>
            {teamMode && <a href="#/team" className={page === "team" ? "active" : ""}>Team</a>}
          </nav>
          <div className="nav-spacer" />
          <div className="statuschip" title={running.map((j) => `${j.kind} ${j.stage ?? ""}`).join("\n")}>
            {running.length ? (<><span className="dot run" />{running.length} job{running.length > 1 ? "s" : ""} · {running[0].kind.replace("_", " ")} {running[0].stage ?? ""} {Math.round((running[0].progress || 0) * 100)}%</>)
              : failed.length && Date.now() - Date.parse(failed[0].created_at) < 10 * 60e3 ? (<><span className="dot fail" />last job failed: {failed[0].message?.slice(0, 60)}</>)
              : (<><span className="dot ok" />analysis idle</>)}
          </div>
          <div style={{ position: "relative" }} ref={menuRef}>
            <button className="btn primary" onClick={() => setMenu((m) => !m)} aria-expanded={menu}>
              <Download size={15} /> Export <ChevronDown size={14} />
            </button>
            {menu && <ExportMenu videoId={videoParam} />}
          </div>
          {teamMode && user && (
            <div className="userchip" title={`Signed in as ${user.name} (${user.role})`}>
              <UserRound size={15} /><span>{user.name}</span>
              <button className="btn ghost icon sm" onClick={signOut} aria-label="Sign out" title="Sign out"><LogOut size={15} /></button>
            </div>
          )}
        </header>
        {page === "review" ? <Review touchId={rest[0] || undefined} hash={hash} />
          : page === "insights" ? <Insights />
          : page === "home" ? <Home />
          : page === "team" && teamMode ? <Team />
          : <Videos />}
      </div>
      <div className="toast-wrap" role="status" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.err ? "err" : ""}`}>{t.msg}</div>)}
      </div>
    </Ctx.Provider>
  );
}

function ExportMenu({ videoId }: { videoId?: string }) {
  const q = videoId ? `?video_id=${videoId}` : "";
  const items: [string, string, string][] = [
    ["Predictions", `/api/export/predictions.csv${q}`, "CSV"], ["Predictions", `/api/export/predictions.json${q}`, "JSON"],
    ["Training labels (annotations)", `/api/export/annotations.csv${q}`, "CSV"], ["Training labels (annotations)", `/api/export/annotations.json${q}`, "JSON"],
    ["Review records", `/api/export/reviews.csv${q}`, "CSV"], ["review.csv (coach sheet)", `/api/export/review.csv${q}`, "CSV"],
    ["Events", `/api/export/events.csv${q}`, "CSV"], ["Run configs & versions", `/api/export/runs.json${q}`, "JSON"],
    ["Full bundle (re-importable)", `/api/export/bundle.json${q}`, "JSON"],
  ];
  return (
    <div className="menu" role="menu">
      <div className="mh">{videoId ? "This video" : "All videos"}</div>
      {items.map(([n, href, f]) => <a key={href} href={href} download role="menuitem">{n}<small>{f}</small></a>)}
      <div className="mh">AIRS timeline</div>
      <div style={{ padding: "2px 10px 8px", fontSize: 12.5, color: "var(--ink-3)" }}>Per touch, from the review panel.</div>
    </div>
  );
}
