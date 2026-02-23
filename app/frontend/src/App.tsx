import { useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelRecon,
  deleteRecon,
  getAppStatus,
  getReconHealth,
  getReconHistory,
  getReconJob,
  getReconQueue,
  runRecon,
} from "./lib/api";
import type { ReconHistoryItem } from "./lib/schemas";

type ReconMode = "username" | "email" | "phone";

function tone(status: string | undefined): string {
  const s = String(status || "").toLowerCase();
  if (["success", "done"].includes(s)) return "good";
  if (["running", "queued"].includes(s)) return "info";
  if (["error", "failed", "cancelled"].includes(s)) return "bad";
  return "neutral";
}

function formatTime(value?: string): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
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
  const reconHealthQ = useQuery({
    queryKey: ["recon-health"],
    queryFn: getReconHealth,
    refetchInterval: 10000,
  });
  const reconQueueQ = useQuery({ queryKey: ["recon-queue"], queryFn: getReconQueue, refetchInterval: 6000 });

  return (
    <section>
      <header className="page-header">
        <h1>Command Center</h1>
        <p>Operational overview across account tracking and Recon Lab.</p>
      </header>

      <div className="card-grid">
        <article className="card">
          <h3>API Status</h3>
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
          <p className="stat">
            {reconQueueQ.data?.running ?? 0} running / {reconQueueQ.data?.queued ?? 0} queued
          </p>
          <p className="hint">
            limit {reconQueueQ.data?.queue_limit ?? "-"}, max concurrency {reconQueueQ.data?.max_concurrency ?? "-"}
          </p>
        </article>
        <article className="card">
          <h3>Tool Health</h3>
          <p className="hint">
            Blackbird: {reconHealthQ.data?.tools?.blackbird?.executable_exists ? "ok" : "missing"}
          </p>
          <p className="hint">
            PhoneInfoga: {reconHealthQ.data?.tools?.phoneinfoga?.executable_exists ? "ok" : "missing"}
          </p>
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
    const telephony = findings.find((f: any) => String(f?.category || "").toLowerCase() === "telephony");
    const evidence = telephony?.evidence_json || {};
    return {
      callerId: evidence.caller_id || evidence.caller_name || "Not found",
      carrier: evidence.carrier || "Unknown",
      country: evidence.country || "Unknown",
      code: evidence.countryCode ? `+${evidence.countryCode}` : "-",
      e164: evidence.e164 || "Unknown",
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
        <p>Separate reconnaissance workspace with queue visibility and job lifecycle controls.</p>
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
          <button onClick={onRun} disabled={runMutation.isPending}>
            {runMutation.isPending ? "Queueing..." : "Run scan"}
          </button>
          <p className="hint">AI key: {reconHealthQ.data?.ai?.key_configured ? "configured" : "missing"}</p>
          {runMutation.error ? <p className="error">{(runMutation.error as Error).message}</p> : null}
        </article>

        <article className="card">
          <h3>Queue</h3>
          <p className="hint">
            Running {reconQueueQ.data?.running ?? 0}/{reconQueueQ.data?.max_concurrency ?? 0} • Queued {reconQueueQ.data?.queued ?? 0}
          </p>
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
                  <tr>
                    <td colSpan={3} className="hint">No active jobs.</td>
                  </tr>
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
                          <button
                            className="btn-secondary"
                            onClick={() => cancelMutation.mutate(job.id)}
                            disabled={cancelMutation.isPending}
                          >
                            Cancel
                          </button>
                        ) : (
                          <button
                            className="btn-secondary"
                            onClick={() => deleteMutation.mutate(job.id)}
                            disabled={deleteMutation.isPending}
                          >
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {!jobs.length ? (
                  <tr>
                    <td colSpan={5} className="hint">No recon jobs yet.</td>
                  </tr>
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

function PlaceholderPage({ title, body }: { title: string; body: string }) {
  return (
    <section>
      <header className="page-header">
        <h1>{title}</h1>
        <p>{body}</p>
      </header>
      <article className="card">
        <p className="hint">This module is now on the new architecture and ready for full porting in the next iteration.</p>
      </article>
    </section>
  );
}

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<CommandCenterPage />} />
        <Route path="/recon" element={<ReconPage />} />
        <Route
          path="/operations"
          element={<PlaceholderPage title="Operations" body="Schedules, retries, and maintenance actions." />}
        />
        <Route
          path="/accounts"
          element={<PlaceholderPage title="Accounts" body="Login accounts, sessions, and auth operations." />}
        />
        <Route
          path="/settings"
          element={<PlaceholderPage title="Settings" body="Feature flags and runtime configuration controls." />}
        />
      </Routes>
    </AppShell>
  );
}
