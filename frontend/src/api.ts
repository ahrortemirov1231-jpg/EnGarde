export type Side = "left" | "right";

export interface Fencer { fencer_id: string; side: Side; athlete_id: string | null; display_name: string | null }
export interface Bout { bout_id: string; video_id: string; name: string; fencers: Fencer[] }
export interface Video {
  video_id: string; title: string; width: number; height: number; n_frames: number | null; duration_s: number | null;
  fps_nominal: number | null; fps_measured: number | null; vfr: number; codec: string; capture_type: string;
  weapon_type: string; is_synthetic: number; indexed: boolean; calibrated: boolean; bouts?: Bout[];
  content_rect?: number[] | null; linked?: boolean;
  n_touches?: number; pending_review?: number; jobs?: Job[]; job?: Job | null;
  split?: "train" | "test" | null; locked?: number; locked_by?: string | null;
  n_labelled_touches?: number; n_labels?: number; review_status?: "no_touches" | "pending" | "in_progress" | "reviewed";
}
export interface Segment {
  segment_id: string; kind: "prediction" | "annotation"; touch_id: string; side: Side; start_frame: number; end_frame: number;
  label: string; run_id?: string | null; class_probs?: Record<string, number> | null; calibrated_prob?: number | null;
  top2_label?: string | null; no_call?: number; no_call_reason?: string | null; track_quality?: string | null;
  pose_quality?: number | null; camera_motion_flag?: number | null; label_source?: string | null; annotator_id?: string | null;
  ambiguity_flag?: number; derived_from_segment_id?: string | null; reviews?: Review[]; updated_at?: string;
}
export interface Review {
  review_id: string; prediction_segment_id: string; action: string; corrected_label: string | null;
  corrected_start_frame: number | null; corrected_end_frame: number | null; error_reason: string | null;
  reviewer: string; reviewed_at: string; resulting_annotation_id: string | null; comment: string | null;
}
export interface EventRow {
  event_id: string; side: Side; event_type: string; event_frame: number | null; event_time_ms: number | null;
  event_prob: number | null; source: string; determinable: number; note: string | null; run_id: string | null;
}
export interface Job { job_id: string; kind: string; target_id: string; status: "queued" | "running" | "completed" | "failed"; progress: number; stage: string | null; message: string | null; created_at: string }
export interface Touch {
  touch_id: string; bout_id: string; video_id: string; seq: number; light_frame: number; light_time_s: number;
  lamp_left: number | null; lamp_right: number | null; light_source: string; start_frame: number; end_frame: number;
  window_clipped: number; notes: string | null; n_annotations?: number; pending_review?: number; frames_ready?: boolean;
  latest_run?: { run_id: string; method: string; status_label: string; created_at: string } | null;
}
export interface TouchDetail extends Touch {
  video: Video; bout: Bout; times: { frames: number[]; t_s: number[]; rel_ms: number[]; t0_video_s: number };
  annotations: Segment[]; coverage: Record<Side, { gaps: number[][]; overlaps: number[][]; complete: boolean }>;
  expert_events: EventRow[];
  latest_run: { run_id: string; method: string; status_label: string; model_version: string; calibrated: number; created_at: string; stage_status: Record<string, string> } | null;
  manifest: Record<string, any>; jobs: Job[];
}
export interface Config {
  version?: string; preset?: string; markers?: string[]; admins?: string[];
  label_migration?: { renamed: number; by?: Record<string, number> };
  footwork: string[]; pending_definition: string[]; human_states: string[]; label_sources: string[]; event_types: string[];
  error_reasons: string[]; review_actions: string[]; interval_convention: string;
  pose_backend: { name: string; installed: boolean; weights_present: boolean; keypoints: string };
}
export interface Candidate { track_id: number; members: number[]; frames: number; height_px: number; median_x: number; whiteness: number | null; eligible: boolean }
export interface Candidates {
  available: boolean; reason?: string; status?: string; assignment?: { left: number | null; right: number | null };
  candidates?: Candidate[]; identity_confirmed?: { left: number; right: number; confirmed_by: string; confirmed_at: string } | null;
  boxes?: Record<string, number[][]>; rules?: Record<string, number>;
  /** tracker id -> the number shown to people: 1 = left fencer, 2 = right fencer, 3+ = everyone else */
  display?: Record<string, number>;
}
export interface Overlay {
  available: boolean; reason?: string; source?: any; frames?: number[];
  sides?: Record<Side, { bbox: (number[] | null)[]; flags: { track_break: boolean; id_switch: boolean; identity_uncertain: boolean; screen_position: string }[]; kp: ((number[] | null)[])[] }>;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, credentials: "same-origin", headers: init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined });
  // team server: the session ended (or this device was signed out) -> show the sign-in screen
  if (r.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event("engarde:signin"));
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const j = await r.json(); msg = j.error || (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j)); } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const api = {
  config: () => req<Config>("/api/config"),
  videos: () => req<Video[]>("/api/videos"),
  video: (id: string) => req<Video & { bouts: Bout[] }>(`/api/videos/${id}`),
  frameTimes: (id: string) => req<{ t0: number; t: number[] }>(`/api/videos/${id}/frame_times`),
  upload: (fd: FormData) => req<{ video: Video; job: Job }>("/api/videos", { method: "POST", body: fd }),
  analyzeVideo: (vid: string) => req<Job>(`/api/videos/${vid}/analyze`, { method: "POST" }),
  addBout: (vid: string, body: unknown) => req<Bout>(`/api/videos/${vid}/bouts`, json("POST", body)),
  patchFencer: (id: string, body: unknown) => req<Fencer>(`/api/fencers/${id}`, json("PATCH", body)),
  touches: (q: string) => req<Touch[]>(`/api/touches?${q}`),
  touch: (id: string) => req<TouchDetail>(`/api/touches/${id}`),
  addTouch: (bout: string, body: unknown) => req<{ touch: Touch; job: Job }>(`/api/bouts/${bout}/touches`, json("POST", body)),
  patchTouch: (id: string, body: unknown) => req<Touch>(`/api/touches/${id}`, json("PATCH", body)),
  deleteTouch: (id: string) => req(`/api/touches/${id}`, { method: "DELETE" }),
  overlay: (id: string) => req<Overlay>(`/api/touches/${id}/overlay`),
  candidates: (id: string) => req<Candidates>(`/api/touches/${id}/candidates`),
  setIdentity: (id: string, body: unknown) => req<{ identity: any; job: Job }>(`/api/touches/${id}/identity`, json("POST", body)),
  resetIdentity: (id: string, reviewer: string) => req<{ job: Job }>(`/api/touches/${id}/identity?reviewer=${encodeURIComponent(reviewer)}`, { method: "DELETE" }),
  predictions: (id: string, annotator: string, blind: boolean) =>
    req<{ hidden: boolean; reason?: string; run: any; predictions: Segment[]; events: EventRow[] }>(
      `/api/touches/${id}/predictions?annotator=${encodeURIComponent(annotator)}&blind=${blind}`),
  createAnn: (touch: string, body: unknown) => req<Segment>(`/api/touches/${touch}/annotations`, json("POST", body)),
  patchAnn: (id: string, body: unknown) => req<Segment>(`/api/annotations/${id}`, json("PATCH", body)),
  deleteAnn: (id: string, annotator: string) => req(`/api/annotations/${id}?annotator_id=${encodeURIComponent(annotator)}`, { method: "DELETE" }),
  review: (pred: string, body: unknown) => req<Review>(`/api/predictions/${pred}/review`, json("POST", body)),
  addEvent: (touch: string, body: unknown) => req<EventRow>(`/api/touches/${touch}/events`, json("POST", body)),
  deleteEvent: (id: string) => req(`/api/events/${id}`, { method: "DELETE" }),
  analyze: (touch: string, body: unknown) => req<Job>(`/api/touches/${touch}/analyze`, json("POST", body)),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  jobs: () => req<Job[]>(`/api/jobs`),
  insights: (q: string) => req<any>(`/api/insights?${q}`),
  models: () => req<any[]>(`/api/models`),
  train: (body: unknown) => req<Job>(`/api/models/train`, json("POST", body)),
  compareModels: (a: string, b: string) => req<any>(`/api/models/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  bouts: () => req<{ bout_id: string; video_id: string; name: string; split?: string | null; video_title?: string }[]>(`/api/bouts`),
  patchVideo: (id: string, body: unknown) => req<Video>(`/api/videos/${id}`, json("PATCH", body)),
  deleteVideo: (id: string, actor: string) => req<{ deleted: string; touches: number; labels: number; backup: string | null; leftover_folders: string[] }>(
    `/api/videos/${id}?actor=${encodeURIComponent(actor)}`, { method: "DELETE" }),
  dashboard: () => req<Dashboard>(`/api/dashboard`),
  importLabels: (fd: FormData) => req<ImportReport>(`/api/import/labels`, { method: "POST", body: fd }),
  readiness: () => req<Readiness>(`/api/models/readiness`),
  // team server (v0.5.0)
  me: () => req<Me>(`/api/auth/me`),
  login: (name: string, password: string) => req<{ user: User }>(`/api/auth/login`, json("POST", { name, password })),
  logout: () => req(`/api/auth/logout`, { method: "POST" }),
  changePassword: (old: string, next: string) => req(`/api/auth/password`, json("POST", { old, new: next })),
  users: () => req<User[]>(`/api/team/users`),
  addUser: (body: unknown) => req<User>(`/api/team/users`, json("POST", body)),
  patchUser: (id: string, body: unknown) => req<User>(`/api/team/users/${id}`, json("PATCH", body)),
  requestUnlock: (videoId: string, reason: string) => req<UnlockRequest>(`/api/videos/${videoId}/unlock_request`, json("POST", { reason })),
  unlockRequests: () => req<UnlockRequest[]>(`/api/team/unlock_requests`),
  decideUnlock: (id: string, approve: boolean) => req<UnlockRequest>(`/api/team/unlock_requests/${id}`, json("POST", { approve })),
  backupNow: () => req<any>(`/api/team/backup`, { method: "POST" }),
  about: () => req<About>(`/api/about`),
  // bulk video import (v0.5.5)
  bulkFolder: (folder: string, analyze: boolean) => req<{ found: number; job: Job }>(`/api/import/videos/folder`, json("POST", { folder, analyze })),
  bulkLinks: (text: string, analyze: boolean) => req<{ found: number; job: Job }>(`/api/import/videos/links`, json("POST", { text, analyze })),
  bulkStatus: () => req<BulkJob[]>(`/api/import/videos/status`),
};
export interface BulkJob extends Job { result: { found: number; added: number; already_here: number; failed: { item: string; error: string }[] } | null }

export interface User { user_id: string; name: string; role: "admin" | "member" | "viewer"; active: number; created_at: string; last_login_at: string | null }
export interface Me { team_mode: boolean; user: User | null; version: string; needs_first_admin: boolean }
export interface UnlockRequest { request_id: string; video_id: string; video_title?: string; locked_by?: string | null; requested_by: string; reason: string | null; status: string; created_at: string }
export interface About {
  version: string; team_mode: boolean; started_at: string; changelog: string; label_set: string; backup_dir: string | null;
  backup: { last: { at: string; copied_to: string | null; copy_error: string | null } | null; error: string | null } | null;
}

export interface Dashboard {
  videos_total: number; videos_reviewed: number; videos_in_progress: number; videos_pending: number; videos_without_touches: number;
  videos_locked: number; videos_train: number; videos_test: number; touches_total: number; touches_labelled: number;
  labels_total: number; predictions_to_review: number; labels_by_name: Record<string, number>; bouts_per_athlete: Record<string, number>;
}
export interface ImportReport {
  videos: { title: string; matched_by: string; labels_in_file: number; imported: number }[];
  touches_created: number; touches_matched: number; labels_imported: number; already_here: number;
  conflicts: any[]; errors: any[]; unmatched_videos: { title: string; labels: number; why: string }[];
}
export interface Readiness {
  can_train: boolean; why_not: string | null; train_bouts: string[]; test_bouts: string[]; n_train_touches: number;
  frames_per_label: Record<string, number>; steps: { need: string; ok: boolean; gives: string }[];
}

export const SHORT: Record<string, string> = {
  // Tim's label names (default since v0.4.0)
  "En garde": "EN GARDE", "Step forward": "STEP FWD", "Advance \u2192 lunge": "ADV\u2192LUNGE", Lunge: "LUNGE",
  "Reprise attack": "REPRISE", "Pull/retreat": "PULL", "Start of touch": "START", "End of touch": "END",
  "Between touches": "BETWEEN", "No touch": "NO TOUCH",
  // older label sets
  Advance: "ADV", Retreat: "RET", "Other/Still": "STILL", Unknown: "UNK", Unobservable: "UNOBS",
  Hop: "HOP", Recovery: "REC", none: "—",
};
/** CSS-safe form of a label: "Advance → lunge" -> "advance-lunge" (same rule as taxonomy.slug). */
export const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
/** Labels for the drop-downs, in groups. */
export function labelGroups(c: Config | null): { name: string; labels: string[] }[] {
  const g = [{ name: "Movement", labels: c?.footwork ?? [] }];
  if (c?.markers?.length) g.push({ name: "Bout phase (not used to train the model)", labels: c.markers });
  g.push({ name: "Can't tell", labels: c?.human_states ?? ["Unknown", "Unobservable"] });
  return g;
}

export function lowerBound(arr: number[], x: number): number {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; }
  return lo;
}
/** frame shown at presentation time t (last frame with timestamp <= t) */
export function frameAtTime(t: number[], time: number): number {
  const i = lowerBound(t, time + 1e-4);
  return Math.max(0, Math.min(t.length - 1, i - 1));
}
export const fmtMs = (ms: number | null | undefined) =>
  ms == null ? "—" : `${ms >= 0 ? "+" : "−"}${Math.abs(ms / 1000).toFixed(3)} s`;
export const fmtClock = (s: number) => {
  const m = Math.floor(s / 60); const r = s - m * 60;
  return `${m}:${r.toFixed(3).padStart(6, "0")}`;
};
