import { useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelRecon,
  deleteRecon,
  getAppStatus,
  getConfig,
  getLogins,
  getReconHealth,
  getReconHistory,
  getReconJob,
  getReconQueue,
  getRelationshipEvents,
  getRelationshipHistory,
  getSchedules,
  getTargetsSummary,
  runRecon,
} from "./lib/api";
import type { ReconHistoryItem } from "./lib/schemas";

type ReconMode = "username" | "email" | "phone";

function tone(status: string | undefined): string {
  const s = String(status || "").toLowerCase();
  if (["success", "done", "ok", "enabled", "true", "active", "ready"].includes(s)) return "good";
  if (["running", "queued"].includes(s)) return "info";
  if (["error", "failed", "cancelled", "false", "disabled", "blocked"].includes(s)) return "bad";
  return "neutral";
}

function formatTime(value?: string): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function minutesSince(value?: string): number | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
}

function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">InstaLab</div>
        <nav>
          <NavLink to="/" end>
            Command Center
          </NavLink>
          <NavLink to="/targets">Targets</NavLink>
          <NavLink to="/recon">Recon Lab</NavLink>
          <NavLink to="/operations">Operations</NavLink>
          <NavLink to="/accounts">Accounts</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="sidebar-footer">
          <a href="/legacy/" className="legacy-link">
            Open Legacy UI
          </a>
        </div>
      </aside>

      <main className="content">{children}</main>

      <nav className="mobile-nav">
        <NavLink to="/" end>
          Home
        </NavLink>
        <NavLink to="/targets">Targets</NavLink>
        <NavLink to="/recon">Recon</NavLink>
        <NavLink to="/operations">Ops</NavLink>
        <NavLink to="/accounts">Acct</NavLink>
        <NavLink to="/settings">Cfg</NavLink>
      </nav>
    </div>
  );
}

function CommandCenterPage() {
  const statusQ = useQuery({ queryKey: ["status"], queryFn: getAppStatus, refetchInterval: 10000 });
  const reconHealthQ = useQuery({ queryKey: ["recon-health"], queryFn: getReconHealth, refetchInterval: 10000 });
  const reconQueueQ = useQuery({ queryKey: ["recon-queue"], queryFn: getReconQueue, refetchInterval: 6000 });
  const targetsQ = useQuery({ queryKey: ["targets-summary"], queryFn: getTargetsSummary, refetchInterval: 30000 });
  const schedulesQ = useQuery({ queryKey: ["schedules"], queryFn: getSchedules, refetchInterval: 20000 });
  const reconHistoryQ = useQuery({ queryKey: ["recon-history"], queryFn: getReconHistory, refetchInterval: 10000 });
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });

  const enabledSchedules = (schedulesQ.data ?? []).filter((s) => s.enabled).length;
  const failingReconJobs = (reconHistoryQ.data ?? []).filter((j) => ["error", "failed"].includes(String(j.status || "").toLowerCase())).length;
  const readyLogins = (loginsQ.data ?? []).filter((l) => l.private_session_exists && !l.blocked).length;

  return (
    <section>
      <header className="page-header">
        <h1>Command Center</h1>
        <p>Target-account intelligence snapshot: relationship-change timelines, run status, and monitoring health. This workspace is not a social-growth tool.</p>
      </header>

      <div className="card-grid">
        <article className="card">
          <h3>API</h3>
          <p className={`pill ${tone(statusQ.data?.status)}`}>{statusQ.data?.status || "loading"}</p>
        </article>
        <article className="card">
          <h3>Recon Module</h3>
          <p className={`pill ${reconHealthQ.data?.enabled ? "good" : "bad"}`}>
            {reconHealthQ.data?.enabled ? "enabled" : "disabled"}
          </p>
        </article>
        <article className="card">
          <h3>Queue Pressure</h3>
          <p className="stat">{reconQueueQ.data?.running ?? 0} running / {reconQueueQ.data?.queued ?? 0} queued</p>
          <p className="hint">limit {reconQueueQ.data?.queue_limit ?? "-"}</p>
        </article>
        <article className="card">
          <h3>Target Coverage</h3>
          <p className="stat">{targetsQ.data?.length ?? 0}</p>
          <p className="hint">{enabledSchedules}/{schedulesQ.data?.length ?? 0} schedules enabled</p>
        </article>
        <article className="card">
          <h3>Recon Reliability</h3>
          <p className={`pill ${failingReconJobs > 0 ? "bad" : "good"}`}>
            {failingReconJobs > 0 ? `${failingReconJobs} recent failures` : "stable"}
          </p>
        </article>
        <article className="card">
          <h3>Account Readiness</h3>
          <p className="stat">{readyLogins}/{loginsQ.data?.length ?? 0}</p>
          <p className="hint">session-ready and unblocked</p>
        </article>
      </div>
    </section>
  );
}

function TargetsPage() {
  const targetsQ = useQuery({ queryKey: ["targets-summary"], queryFn: getTargetsSummary, refetchInterval: 30000 });
  const [selectedTarget, setSelectedTarget] = useState("");

  useEffect(() => {
    if (!selectedTarget && (targetsQ.data?.length || 0) > 0) {
      setSelectedTarget(String(targetsQ.data?.[0]?.target_username || ""));
    }
  }, [selectedTarget, targetsQ.data]);

  const eventsQ = useQuery({
    queryKey: ["relationship-events", selectedTarget],
    queryFn: () => getRelationshipEvents(selectedTarget),
    enabled: Boolean(selectedTarget),
    refetchInterval: 15000,
  });
  const followersHistoryQ = useQuery({
    queryKey: ["relationship-history", selectedTarget, "followers"],
    queryFn: () => getRelationshipHistory(selectedTarget, "followers"),
    enabled: Boolean(selectedTarget),
    refetchInterval: 20000,
  });
  const followingHistoryQ = useQuery({
    queryKey: ["relationship-history", selectedTarget, "following"],
    queryFn: () => getRelationshipHistory(selectedTarget, "following"),
    enabled: Boolean(selectedTarget),
    refetchInterval: 20000,
  });

  const eventStats = useMemo(() => {
    const events = eventsQ.data ?? [];
    return {
      followersAdded: events.filter((e) => e.relation_type === "followers" && e.event_type === "added").length,
      followersRemoved: events.filter((e) => e.relation_type === "followers" && e.event_type === "removed").length,
      followingAdded: events.filter((e) => e.relation_type === "following" && e.event_type === "added").length,
      followingRemoved: events.filter((e) => e.relation_type === "following" && e.event_type === "removed").length,
    };
  }, [eventsQ.data]);

  return (
    <section>
      <header className="page-header">
        <h1>Targets</h1>
        <p>Track what changed on selected targets: who they followed/unfollowed and who followed/unfollowed them, with clear dates and history state.</p>
      </header>
      <article className="card">
        <h3>Target Focus</h3>
        <label>
          Selected target
          <select value={selectedTarget} onChange={(e) => setSelectedTarget(e.target.value)}>
            {(targetsQ.data ?? []).map((t, idx) => (
              <option key={`${t.target_username || "target"}-${idx}`} value={t.target_username || ""}>
                {t.target_username || "-"}
              </option>
            ))}
          </select>
        </label>
        <div className="card-grid compact">
          <article className="card">
            <h4>New Followers</h4>
            <p className="stat">{eventStats.followersAdded}</p>
          </article>
          <article className="card">
            <h4>Unfollowers</h4>
            <p className="stat">{eventStats.followersRemoved}</p>
          </article>
          <article className="card">
            <h4>Target Followed</h4>
            <p className="stat">{eventStats.followingAdded}</p>
          </article>
          <article className="card">
            <h4>Target Unfollowed</h4>
            <p className="stat">{eventStats.followingRemoved}</p>
          </article>
        </div>
      </article>
      <article className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Target</th>
                <th>Latest Run</th>
                <th>Followers</th>
                <th>Following</th>
                <th>Staleness</th>
              </tr>
            </thead>
            <tbody>
              {(targetsQ.data ?? []).map((target, idx) => {
                const mins = minutesSince(target.latest_run_at);
                const stale = mins !== null && mins > 180;
                return (
                  <tr key={`${target.target_username || "target"}-${idx}`}>
                    <td>{target.target_username || "-"}</td>
                    <td>{formatTime(target.latest_run_at)}</td>
                    <td>{target.latest_followers_count ?? "-"}</td>
                    <td>{target.latest_following_count ?? "-"}</td>
                    <td>
                      {mins === null ? (
                        <span className="pill neutral">unknown</span>
                      ) : (
                        <span className={`pill ${stale ? "bad" : "good"}`}>{mins}m ago</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!(targetsQ.data ?? []).length ? (
                <tr>
                  <td colSpan={5} className="hint">No target data yet.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
      <div className="split-grid">
        <article className="card">
          <h3>Recent Relationship Events</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Relation</th>
                  <th>Event</th>
                  <th>Username</th>
                </tr>
              </thead>
              <tbody>
                {(eventsQ.data ?? []).slice(0, 30).map((ev) => (
                  <tr key={String(ev.id || Math.random())}>
                    <td>{formatTime(ev.observed_at)}</td>
                    <td>{ev.relation_type || "-"}</td>
                    <td><span className={`pill ${ev.event_type === "added" ? "good" : "bad"}`}>{ev.event_type || "-"}</span></td>
                    <td>{ev.username || "-"}</td>
                  </tr>
                ))}
                {!(eventsQ.data ?? []).length ? (
                  <tr><td colSpan={4} className="hint">No relationship events for this target yet.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </article>
        <article className="card">
          <h3>Current Relationship State</h3>
          <p className="hint">
            Followers active {(followersHistoryQ.data ?? []).filter((x) => x.active === 1).length} • Following active {(followingHistoryQ.data ?? []).filter((x) => x.active === 1).length}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>First Seen</th>
                  <th>Last Seen</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(followersHistoryQ.data ?? []).slice(0, 20).map((row) => (
                  <tr key={`f-${row.username || Math.random()}`}>
                    <td>{row.username || "-"}</td>
                    <td>{formatTime(row.first_seen || undefined)}</td>
                    <td>{formatTime(row.last_seen || undefined)}</td>
                    <td><span className={`pill ${row.active === 1 ? "good" : "bad"}`}>{row.active === 1 ? "active follower" : "unfollowed"}</span></td>
                  </tr>
                ))}
                {!(followersHistoryQ.data ?? []).length ? (
                  <tr><td colSpan={4} className="hint">No follower history rows yet.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </article>
      </div>
    </section>
  );
}

function ReconPage() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<ReconMode>("username");
  const [queryValue, setQueryValue] = useState("");
  const [selectedJobId, setSelectedJobId] = useState("");
  const [aiEnabled, setAiEnabled] = useState(false);
  const [pdfEnabled, setPdfEnabled] = useState(true);

  const reconHealthQ = useQuery({ queryKey: ["recon-health"], queryFn: getReconHealth, refetchInterval: 10000 });
  const reconHistoryQ = useQuery({ queryKey: ["recon-history"], queryFn: getReconHistory, refetchInterval: 6000 });
  const reconQueueQ = useQuery({ queryKey: ["recon-queue"], queryFn: getReconQueue, refetchInterval: 4000 });

  const selectedJobQ = useQuery({
    queryKey: ["recon-job", selectedJobId],
    queryFn: () => getReconJob(selectedJobId),
    enabled: Boolean(selectedJobId),
    refetchInterval: selectedJobId ? 2000 : false,
  });

  const runMutation = useMutation({
    mutationFn: runRecon,
    onSuccess: async (res) => {
      setSelectedJobId(res.job_id);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["recon-history"] }),
        qc.invalidateQueries({ queryKey: ["recon-queue"] }),
      ]);
    },
  });

  const cancelMutation = useMutation({
    mutationFn: cancelRecon,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["recon-history"] }),
        qc.invalidateQueries({ queryKey: ["recon-queue"] }),
        qc.invalidateQueries({ queryKey: ["recon-job", selectedJobId] }),
      ]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRecon,
    onSuccess: async () => {
      setSelectedJobId("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["recon-history"] }),
        qc.invalidateQueries({ queryKey: ["recon-queue"] }),
      ]);
    },
  });

  const jobs = reconHistoryQ.data ?? [];
  const selected = selectedJobQ.data;
  const selectedMode = String(selected?.job?.mode || "").toLowerCase();

  const phoneProfile = useMemo(() => {
    const findings = selected?.findings ?? [];
    const telephony = findings.find((f: unknown) => {
      if (typeof f !== "object" || !f) return false;
      const category = (f as { category?: unknown }).category;
      return String(category || "").toLowerCase() === "telephony";
    });
    const evidence = (telephony as { evidence_json?: Record<string, unknown> } | undefined)?.evidence_json || {};
    return {
      callerId: String(evidence.caller_id || evidence.caller_name || "Not found"),
      carrier: String(evidence.carrier || "Unknown"),
      country: String(evidence.country || "Unknown"),
      code: evidence.countryCode ? `+${String(evidence.countryCode)}` : "-",
      e164: String(evidence.e164 || "Unknown"),
      valid: typeof evidence.valid === "boolean" ? (evidence.valid ? "yes" : "no") : "unknown",
    };
  }, [selected]);

  const onRun = () => {
    if (!queryValue.trim()) return;
    const blackbirdMode = mode === "username" || mode === "email";
    runMutation.mutate({
      mode,
      query_value: queryValue.trim(),
      options: {
        no_nsfw: true,
        ai: blackbirdMode ? aiEnabled : false,
        generate_pdf: blackbirdMode ? pdfEnabled : false,
      },
    });
  };

  return (
    <section>
      <header className="page-header">
        <h1>Recon Lab</h1>
        <p>Separate investigation workspace for identifiers. Recon results stay isolated from target-account relationship tracking history.</p>
      </header>

      <div className="split-grid">
        <article className="card">
          <h3>New Scan</h3>
          <label>
            Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as ReconMode)}>
              <option value="username">Username (Blackbird)</option>
              <option value="email">Email (Blackbird)</option>
              <option value="phone">Phone (PhoneInfoga)</option>
            </select>
          </label>
          <label>
            Query
            <input
              placeholder="@username / user@example.com / +15551234567"
              value={queryValue}
              onChange={(e) => setQueryValue(e.target.value)}
            />
          </label>
          <div className="row gap">
            <label className="inline-check">
              <input
                type="checkbox"
                checked={aiEnabled}
                disabled={mode === "phone" || !reconHealthQ.data?.ai?.key_configured}
                onChange={(e) => setAiEnabled(e.target.checked)}
              />
              AI analysis
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={pdfEnabled}
                disabled={mode === "phone"}
                onChange={(e) => setPdfEnabled(e.target.checked)}
              />
              PDF report
            </label>
          </div>
          <button onClick={onRun} disabled={runMutation.isPending}>{runMutation.isPending ? "Queueing..." : "Run scan"}</button>
          <p className="hint">AI key: {reconHealthQ.data?.ai?.key_configured ? "configured" : "missing"}</p>
          {runMutation.error ? <p className="error">{(runMutation.error as Error).message}</p> : null}
        </article>

        <article className="card">
          <h3>Queue</h3>
          <p className="hint">Running {reconQueueQ.data?.running ?? 0}/{reconQueueQ.data?.max_concurrency ?? 0} • Queued {reconQueueQ.data?.queued ?? 0}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>State</th>
                  <th>Query</th>
                  <th>Elapsed</th>
                </tr>
              </thead>
              <tbody>
                {(reconQueueQ.data?.queue ?? []).map((item, idx) => (
                  <tr key={`${item.job_id || "item"}-${idx}`}>
                    <td><span className={`pill ${tone(item.state)}`}>{item.state || "-"}</span></td>
                    <td>{item.query_value || "-"}</td>
                    <td>{item.elapsed_seconds ?? 0}s</td>
                  </tr>
                ))}
                {!(reconQueueQ.data?.queue ?? []).length ? (
                  <tr><td colSpan={3} className="hint">No active jobs.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </article>
      </div>

      <div className="split-grid">
        <article className="card">
          <h3>History</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Mode</th>
                  <th>Query</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job: ReconHistoryItem) => {
                  const state = String(job.status || "").toLowerCase();
                  return (
                    <tr key={job.id}>
                      <td>{formatTime(job.created_at)}</td>
                      <td>{job.mode || "-"}</td>
                      <td>{job.query_value || "-"}</td>
                      <td><span className={`pill ${tone(state)}`}>{state || "-"}</span></td>
                      <td className="row gap">
                        <button className="btn-secondary" onClick={() => setSelectedJobId(job.id)}>Open</button>
                        {state === "running" || state === "queued" ? (
                          <button className="btn-secondary" onClick={() => cancelMutation.mutate(job.id)} disabled={cancelMutation.isPending}>Cancel</button>
                        ) : (
                          <button className="btn-secondary" onClick={() => deleteMutation.mutate(job.id)} disabled={deleteMutation.isPending}>Delete</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {!jobs.length ? (
                  <tr><td colSpan={5} className="hint">No recon jobs yet.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </article>

        <article className="card">
          <h3>Job Detail</h3>
          {selected?.job ? (
            <>
              <p className="hint">
                {selected.job.mode} • {selected.job.query_value} • <span className={`pill ${tone(selected.job.status)}`}>{selected.job.status}</span>
              </p>
              {selected.job.error_message ? <p className="error">{selected.job.error_message}</p> : null}
              {selectedMode === "phone" ? (
                <div className="profile">
                  <h4>Caller Profile</h4>
                  <p>Caller ID: {phoneProfile.callerId}</p>
                  <p>Carrier: {phoneProfile.carrier}</p>
                  <p>Country: {phoneProfile.country} ({phoneProfile.code})</p>
                  <p>E.164: {phoneProfile.e164}</p>
                  <p>Valid: {phoneProfile.valid}</p>
                </div>
              ) : null}
              <p className="hint">Findings: {selected.findings?.length ?? 0}</p>
            </>
          ) : (
            <p className="hint">Select a job from history.</p>
          )}
        </article>
      </div>
    </section>
  );
}

function OperationsPage() {
  const schedulesQ = useQuery({ queryKey: ["schedules"], queryFn: getSchedules, refetchInterval: 20000 });
  const enabledCount = (schedulesQ.data ?? []).filter((x) => x.enabled).length;

  return (
    <section>
      <header className="page-header">
        <h1>Operations</h1>
        <p>Schedules and run controls for target-account monitoring jobs and relationship-change collection.</p>
      </header>
      <article className="card">
        <h3>Schedules</h3>
        <p className="hint">Enabled {enabledCount} / Total {schedulesQ.data?.length ?? 0}</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Login</th>
                <th>Target</th>
                <th>Cron</th>
                <th>Mode</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(schedulesQ.data ?? []).map((sch, idx) => (
                <tr key={`${sch.id || "s"}-${idx}`}>
                  <td>{sch.id ?? "-"}</td>
                  <td>{sch.login_username || "-"}</td>
                  <td>{sch.target_username || "-"}</td>
                  <td>{sch.cron_expr || "-"}</td>
                  <td>{sch.run_login_mode || "-"}</td>
                  <td><span className={`pill ${sch.enabled ? "good" : "bad"}`}>{sch.enabled ? "enabled" : "disabled"}</span></td>
                </tr>
              ))}
              {!(schedulesQ.data ?? []).length ? (
                <tr><td colSpan={6} className="hint">No schedules configured.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}

function AccountsPage() {
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });
  const readyCount = (loginsQ.data ?? []).filter((l) => l.private_session_exists && !l.blocked).length;

  return (
    <section>
      <header className="page-header">
        <h1>Accounts</h1>
        <p>Tracking account readiness and session state for target monitoring and timeline collection runs.</p>
      </header>
      <article className="card">
        <p className="hint">Ready sessions {readyCount} / {loginsQ.data?.length ?? 0}</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Login</th>
                <th>Password</th>
                <th>TOTP</th>
                <th>Session</th>
                <th>Blocked</th>
              </tr>
            </thead>
            <tbody>
              {(loginsQ.data ?? []).map((login, idx) => (
                <tr key={`${login.login_username || "login"}-${idx}`}>
                  <td>{login.login_username || "-"}</td>
                  <td><span className={`pill ${login.has_password ? "good" : "neutral"}`}>{login.has_password ? "yes" : "no"}</span></td>
                  <td><span className={`pill ${login.has_totp_seed ? "good" : "neutral"}`}>{login.has_totp_seed ? "yes" : "no"}</span></td>
                  <td><span className={`pill ${login.private_session_exists ? "good" : "bad"}`}>{login.private_session_exists ? "ready" : "missing"}</span></td>
                  <td><span className={`pill ${login.blocked ? "bad" : "good"}`}>{login.blocked ? "blocked" : "active"}</span></td>
                </tr>
              ))}
              {!(loginsQ.data ?? []).length ? (
                <tr><td colSpan={5} className="hint">No login accounts returned.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}

function SettingsPage() {
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: getConfig, refetchInterval: 30000 });

  return (
    <section>
      <header className="page-header">
        <h1>Settings</h1>
        <p>Runtime settings that affect target-account tracking cadence and recon behavior.</p>
      </header>
      <div className="card-grid">
        <article className="card">
          <h3>Recon Enabled</h3>
          <p className={`pill ${cfgQ.data?.recon_enabled ? "good" : "bad"}`}>{String(cfgQ.data?.recon_enabled ?? "-")}</p>
        </article>
        <article className="card">
          <h3>Concurrency</h3>
          <p className="stat">{cfgQ.data?.recon_max_concurrency ?? "-"}</p>
          <p className="hint">queue limit {cfgQ.data?.recon_queue_limit ?? "-"}</p>
        </article>
        <article className="card">
          <h3>Recon Timeout</h3>
          <p className="stat">{cfgQ.data?.recon_timeout_seconds ?? "-"}s</p>
        </article>
        <article className="card">
          <h3>Default Login Mode</h3>
          <p className="pill neutral">{cfgQ.data?.run_login_mode_default || "-"}</p>
        </article>
      </div>
    </section>
  );
}

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<CommandCenterPage />} />
        <Route path="/targets" element={<TargetsPage />} />
        <Route path="/recon" element={<ReconPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  );
}
