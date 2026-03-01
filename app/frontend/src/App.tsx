import { useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addLogin,
  cancelAccountCreate,
  getAccountCreateStatus,
  createSchedule,
  deleteLogin,
  deleteSchedule,
  getAppStatus,
  getConfig,
  getLogins,
  getRelationshipEvents,
  getRelationshipHistory,
  getRunStatus,
  getSchedules,
  getTargetsSummary,
  getAuthTrace,
  getRunJobDetail,
  getRunJobStatus,
  resetLogin,
  runAuthPreflight,
  requestPasswordReset,
  startRun,
  startAccountCreate,
  setLoginNewPassword,
  submitChallengeCode,
  testProxy,
  updateConfig,
} from "./lib/api";
import type { ConfigValues } from "./lib/schemas";

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
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
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
        <NavLink to="/operations">Ops</NavLink>
        <NavLink to="/accounts">Acct</NavLink>
        <NavLink to="/settings">Prefs</NavLink>
      </nav>
    </div>
  );
}

function CommandCenterPage() {
  const statusQ = useQuery({ queryKey: ["status"], queryFn: getAppStatus, refetchInterval: 10000 });
  const targetsQ = useQuery({ queryKey: ["targets-summary"], queryFn: getTargetsSummary, refetchInterval: 30000 });
  const schedulesQ = useQuery({ queryKey: ["schedules"], queryFn: getSchedules, refetchInterval: 20000 });
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });

  const upcomingSchedules = (schedulesQ.data ?? []).filter((s) => Boolean(s.next_run)).length;
  const readyLogins = (loginsQ.data ?? []).filter((l) => l.private_session_exists).length;

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
          <h3>Target Coverage</h3>
          <p className="stat">{targetsQ.data?.length ?? 0}</p>
          <p className="hint">{upcomingSchedules}/{schedulesQ.data?.length ?? 0} schedules with next run</p>
        </article>
        <article className="card">
          <h3>Account Readiness</h3>
          <p className="stat">{readyLogins}/{loginsQ.data?.length ?? 0}</p>
          <p className="hint">session-ready</p>
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

  const followerRows = followersHistoryQ.data ?? [];
  const followingRows = followingHistoryQ.data ?? [];
  const followerActive = followerRows.filter((x) => x.active === 1);
  const followerRemoved = followerRows.filter((x) => x.active !== 1);
  const followingActive = followingRows.filter((x) => x.active === 1);
  const followingRemoved = followingRows.filter((x) => x.active !== 1);
  return (
    <section>
      <header className="page-header">
        <h1>Targets</h1>
        <p>Track what changed on selected targets: who they followed/unfollowed and who followed/unfollowed them, with clear dates and history state.</p>
      </header>
      <section className="target-section">
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
        <div className="metric-inline">
          <span>New Followers: <strong>{eventStats.followersAdded}</strong></span>
          <span>Unfollowers: <strong>{eventStats.followersRemoved}</strong></span>
          <span>Target Followed: <strong>{eventStats.followingAdded}</strong></span>
          <span>Target Unfollowed: <strong>{eventStats.followingRemoved}</strong></span>
        </div>
      </section>
      <div className="split-grid">
        <section className="target-section">
          <h3>Timeline</h3>
          <p className="hint">Latest observed changes for this target. Each row is one follow/unfollow event with timestamp.</p>
          <div className="entity-list">
            {(eventsQ.data ?? []).slice(0, 20).map((ev, idx) => (
              <div className="list-row" key={`me-${ev.id || idx}`}>
                <div className="list-title">{ev.username || "-"}</div>
                <div className="list-meta">{formatTime(ev.observed_at)}</div>
                <div className="row gap">
                  <span className="pill neutral">{ev.relation_type === "followers" ? "target followers" : "target following"}</span>
                  <span className={`pill ${ev.event_type === "added" ? "good" : "bad"}`}>{ev.event_type || "-"}</span>
                </div>
              </div>
            ))}
            {!(eventsQ.data ?? []).length ? <p className="hint">No relationship events for this target yet.</p> : null}
          </div>
        </section>
        <section className="target-section">
          <h3>Followers State</h3>
          <p className="hint">Accounts that follow the target now, and accounts that unfollowed since tracking began.</p>
          <h4>Active Followers</h4>
          <div className="entity-list">
            {followerActive.slice(0, 10).map((row, idx) => (
              <div className="list-row" key={`mfa-${row.username || idx}`}>
                <div className="list-title">{row.username || "-"}</div>
                <div className="list-meta">first seen {formatTime(row.first_seen || undefined)}</div>
                <div className="list-meta">last seen {formatTime(row.last_seen || undefined)}</div>
              </div>
            ))}
            {!followerActive.length ? <p className="hint">No active followers in history.</p> : null}
          </div>
          <h4>Removed Followers</h4>
          <div className="entity-list">
            {followerRemoved.slice(0, 8).map((row, idx) => (
              <div className="list-row" key={`mfr-${row.username || idx}`}>
                <div className="list-title">{row.username || "-"}</div>
                <div className="list-meta">first seen {formatTime(row.first_seen || undefined)}</div>
                <div className="list-meta">last seen {formatTime(row.last_seen || undefined)}</div>
              </div>
            ))}
            {!followerRemoved.length ? <p className="hint">No removed followers in history.</p> : null}
          </div>
          <p className="hint">Active: {followerActive.length} • Removed: {followerRemoved.length}</p>
        </section>
      </div>
      <section className="target-section">
        <h3>Following State</h3>
        <p className="hint">Accounts the target currently follows, and accounts the target has unfollowed since tracking began.</p>
        <h4>Currently Followed By Target</h4>
        <div className="entity-list">
          {followingActive.slice(0, 10).map((row, idx) => (
            <div className="list-row" key={`mga-${row.username || idx}`}>
              <div className="list-title">{row.username || "-"}</div>
              <div className="list-meta">first seen {formatTime(row.first_seen || undefined)}</div>
              <div className="list-meta">last seen {formatTime(row.last_seen || undefined)}</div>
            </div>
          ))}
          {!followingActive.length ? <p className="hint">No active following accounts in history.</p> : null}
        </div>
        <h4>Unfollowed By Target</h4>
        <div className="entity-list">
          {followingRemoved.slice(0, 8).map((row, idx) => (
            <div className="list-row" key={`mgr-${row.username || idx}`}>
              <div className="list-title">{row.username || "-"}</div>
              <div className="list-meta">first seen {formatTime(row.first_seen || undefined)}</div>
              <div className="list-meta">last seen {formatTime(row.last_seen || undefined)}</div>
            </div>
          ))}
          {!followingRemoved.length ? <p className="hint">No removed following accounts in history.</p> : null}
        </div>
        <p className="hint">Active: {followingActive.length} • Removed: {followingRemoved.length}</p>
      </section>
    </section>
  );
}

function OperationsPage() {
  const qc = useQueryClient();
  const schedulesQ = useQuery({ queryKey: ["schedules"], queryFn: getSchedules, refetchInterval: 20000 });
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });
  const runStatusQ = useQuery({ queryKey: ["run-status"], queryFn: getRunStatus, refetchInterval: 6000 });
  const [manualLogin, setManualLogin] = useState("");
  const [manualTarget, setManualTarget] = useState("");
  const [manualJobId, setManualJobId] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [showCodeModal, setShowCodeModal] = useState(false);
  const [scheduleLogin, setScheduleLogin] = useState("");
  const [scheduleTarget, setScheduleTarget] = useState("");
  const [scheduleCron, setScheduleCron] = useState("0 11,23 * * *");

  useEffect(() => {
    if (!manualLogin && (loginsQ.data?.length || 0) > 0) {
      setManualLogin(String(loginsQ.data?.[0]?.login_username || ""));
    }
    if (!scheduleLogin && (loginsQ.data?.length || 0) > 0) {
      setScheduleLogin(String(loginsQ.data?.[0]?.login_username || ""));
    }
  }, [manualLogin, scheduleLogin, loginsQ.data]);

  const runNowMutation = useMutation({
    mutationFn: startRun,
    onSuccess: async () => {
      setManualTarget("");
      setVerificationCode("");
      setManualJobId("");
      await qc.invalidateQueries({ queryKey: ["run-status"] });
    },
  });
  const runJobQ = useQuery({
    queryKey: ["run-job-status", manualJobId],
    queryFn: () => getRunJobStatus(manualJobId),
    enabled: Boolean(manualJobId),
    refetchInterval: 3000,
  });
  const runJobDetailQ = useQuery({
    queryKey: ["run-job-detail", manualJobId],
    queryFn: () => getRunJobDetail(manualJobId),
    enabled: Boolean(manualJobId),
    refetchInterval: 4000,
  });
  const submitChallengeMutation = useMutation({
    mutationFn: ({ login, code }: { login: string; code: string }) => submitChallengeCode(login, code),
    onSuccess: () => {
      setVerificationCode("");
    },
  });

  const createScheduleMutation = useMutation({
    mutationFn: createSchedule,
    onSuccess: async () => {
      setScheduleTarget("");
      await qc.invalidateQueries({ queryKey: ["schedules"] });
    },
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["schedules"] });
    },
  });

  const onCreateSchedule = () => {
    if (!scheduleLogin || !scheduleTarget.trim() || !scheduleCron.trim()) return;
    createScheduleMutation.mutate({
      login_username: scheduleLogin.trim(),
      target_username: scheduleTarget.trim().replace(/^@+/, ""),
      interval: scheduleCron.trim(),
    });
  };

  const onRunNow = () => {
    if (!manualLogin.trim() || !manualTarget.trim()) return;
    runNowMutation.mutate({
      login_username: manualLogin.trim(),
      target_username: manualTarget.trim().replace(/^@+/, ""),
    });
  };
  useEffect(() => {
    if (runNowMutation.data?.job_id) setManualJobId(runNowMutation.data.job_id);
  }, [runNowMutation.data]);

  const runPayload = runJobQ.data?.payload;
  const runMeta = runJobQ.data?.meta;
  const runDone = Boolean(runJobQ.data?.done);
  const runErrorCode = String(runPayload?.error_code || "").toLowerCase();
  const promptHint = (runJobDetailQ.data?.worker_out_tail || "").toLowerCase().includes("waiting for code");
  const interactiveChallenge =
    runErrorCode === "two_factor_required" ||
    runErrorCode === "challenge_required" ||
    (String(runMeta?.state || "").toLowerCase() === "running" && promptHint);
  const runNeedsCode =
    !runDone && interactiveChallenge;
  useEffect(() => {
    setShowCodeModal(runNeedsCode);
    if (!runNeedsCode) setVerificationCode("");
  }, [runNeedsCode]);

  return (
    <section>
      <header className="page-header">
        <h1>Operations</h1>
        <p>Schedules and run controls for target-account monitoring jobs and relationship-change collection.</p>
      </header>
      <article className="card">
        <h3>Run Now</h3>
        <div className="form-grid">
          <label>
            Collector login
            <select value={manualLogin} onChange={(e) => setManualLogin(e.target.value)}>
              <option value="">Select login</option>
              {(loginsQ.data ?? []).map((l, idx) => (
                <option key={`manual-${l.login_username || "login"}-${idx}`} value={l.login_username || ""}>
                  {l.login_username || "-"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Target username
            <input
              placeholder="e.g. davidjones.tv"
              value={manualTarget}
              onChange={(e) => setManualTarget(e.target.value)}
            />
          </label>
        </div>
        <div className="row gap">
          <button onClick={onRunNow} disabled={runNowMutation.isPending}>
            {runNowMutation.isPending ? "Queueing..." : "Start run"}
          </button>
          <span className="hint">State: {runStatusQ.data?.state || "idle"}</span>
          {manualJobId ? <span className="hint">Job: {manualJobId}</span> : null}
        </div>
        {runNowMutation.error ? <p className="error">{(runNowMutation.error as Error).message}</p> : null}
        <p className="hint">
          Active {runStatusQ.data?.active_jobs?.length ?? 0} · Queued {runStatusQ.data?.queued_jobs?.length ?? 0}
        </p>
        {manualJobId ? (
          <>
            <p className="hint">
              Live: {runDone ? (runPayload?.status || runMeta?.state || "done") : (runMeta?.state || "running")}
              {runPayload?.error ? ` · ${runPayload.error}` : ""}
            </p>
            {runPayload?.result ? (
              <p className="hint">
                Result: followers {String(runPayload.result.followers_count ?? "-")} · following {String(runPayload.result.followees_count ?? "-")} · run_id {String(runPayload.result.run_id ?? "-")}
              </p>
            ) : null}
            {submitChallengeMutation.error ? <p className="error">{(submitChallengeMutation.error as Error).message}</p> : null}
            <div className="table-wrap">
              <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                {(runJobDetailQ.data?.worker_out_tail || runJobDetailQ.data?.worker_err_tail || "").trim() || "(waiting for run logs)"}
              </pre>
            </div>
          </>
        ) : null}
      </article>
      <article className="card">
        <h3>Schedule Setup</h3>
        <div className="form-grid">
          <label>
            Collector login
            <select value={scheduleLogin} onChange={(e) => setScheduleLogin(e.target.value)}>
              <option value="">Select login</option>
              {(loginsQ.data ?? []).map((l, idx) => (
                <option key={`${l.login_username || "login"}-${idx}`} value={l.login_username || ""}>
                  {l.login_username || "-"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Target username
            <input
              placeholder="e.g. davidjones.tv"
              value={scheduleTarget}
              onChange={(e) => setScheduleTarget(e.target.value)}
            />
          </label>
          <label>
            Cron interval
            <input
              placeholder="0 11,23 * * *"
              value={scheduleCron}
              onChange={(e) => setScheduleCron(e.target.value)}
            />
          </label>
        </div>
        <div className="row gap">
          <button onClick={onCreateSchedule} disabled={createScheduleMutation.isPending}>
            {createScheduleMutation.isPending ? "Saving..." : "Add schedule"}
          </button>
          <span className="hint">Example twice daily: `0 11,23 * * *`</span>
        </div>
        {createScheduleMutation.error ? <p className="error">{(createScheduleMutation.error as Error).message}</p> : null}
      </article>
      <article className="card">
        <h3>Schedules</h3>
        <p className="hint">Total {schedulesQ.data?.length ?? 0}</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Login</th>
                <th>Target</th>
                <th>Cron</th>
                <th>Next Run</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(schedulesQ.data ?? []).map((sch, idx) => (
                <tr key={`${sch.id || "s"}-${idx}`}>
                  <td>{sch.id ?? "-"}</td>
                  <td>{sch.login_username || "-"}</td>
                  <td>{sch.target_username || "-"}</td>
                  <td>{sch.interval || "-"}</td>
                  <td>{formatTime(sch.next_run || undefined)}</td>
                  <td>
                    <button
                      className="btn-secondary"
                      onClick={() => sch.id && deleteScheduleMutation.mutate(sch.id)}
                      disabled={deleteScheduleMutation.isPending || !sch.id}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {!(schedulesQ.data ?? []).length ? (
                <tr><td colSpan={6} className="hint">No schedules configured.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
      {runNeedsCode && showCodeModal ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setShowCodeModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>Verification Code Required</h3>
            <p className="hint">Run {manualJobId} is waiting for a 2FA/challenge code for @{manualLogin}.</p>
            <input
              placeholder="enter 6-digit code"
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value)}
              autoFocus
            />
            <div className="row gap">
              <button
                disabled={submitChallengeMutation.isPending || !manualLogin || !verificationCode.trim()}
                onClick={() => submitChallengeMutation.mutate({ login: manualLogin, code: verificationCode.trim() })}
              >
                {submitChallengeMutation.isPending ? "Submitting..." : "Submit code"}
              </button>
              <button className="btn-secondary" onClick={() => setShowCodeModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function AccountsPage() {
  const qc = useQueryClient();
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: getConfig, refetchInterval: 30000 });
  const accountCreateQ = useQuery({
    queryKey: ["account-create-status"],
    queryFn: getAccountCreateStatus,
    refetchInterval: 5000,
  });
  const readyCount = (loginsQ.data ?? []).filter((l) => l.private_session_exists).length;
  const [newLoginUsername, setNewLoginUsername] = useState("");
  const [newLoginPassword, setNewLoginPassword] = useState("");
  const [newLoginTotpSeed, setNewLoginTotpSeed] = useState("");
  const [createStrategy, setCreateStrategy] = useState<"private_api" | "guided_browser">("private_api");
  const [createEmail, setCreateEmail] = useState("");
  const [createFullName, setCreateFullName] = useState("");
  const [createUsername, setCreateUsername] = useState("");
  const [createPassword, setCreatePassword] = useState("");
  const [selectedAuthLogin, setSelectedAuthLogin] = useState("");
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [passwordModalLogin, setPasswordModalLogin] = useState("");
  const [passwordModalValue, setPasswordModalValue] = useState("");

  const addLoginMutation = useMutation({
    mutationFn: addLogin,
    onSuccess: async () => {
      setNewLoginPassword("");
      setNewLoginTotpSeed("");
      await qc.invalidateQueries({ queryKey: ["logins"] });
    },
  });
  const resetLoginMutation = useMutation({
    mutationFn: resetLogin,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["logins"] });
    },
  });
  const deleteLoginMutation = useMutation({
    mutationFn: deleteLogin,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["logins"] });
    },
  });
  const startAccountCreateMutation = useMutation({
    mutationFn: startAccountCreate,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["account-create-status"] });
    },
  });
  const cancelAccountCreateMutation = useMutation({
    mutationFn: cancelAccountCreate,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["account-create-status"] });
    },
  });
  const setNewPasswordMutation = useMutation({
    mutationFn: ({ login, password }: { login: string; password: string }) =>
      setLoginNewPassword(login, password),
    onSuccess: async () => {
      setPasswordModalValue("");
      setPasswordModalOpen(false);
      await qc.invalidateQueries({ queryKey: ["logins"] });
    },
  });
  const requestResetMutation = useMutation({
    mutationFn: ({ login, identifier }: { login: string; identifier?: string }) =>
      requestPasswordReset(login, identifier),
  });
  const authPreflightMutation = useMutation({
    mutationFn: runAuthPreflight,
  });
  const authTraceQ = useQuery({
    queryKey: ["auth-trace", selectedAuthLogin],
    queryFn: () => getAuthTrace(selectedAuthLogin, 30),
    enabled: Boolean(selectedAuthLogin),
    refetchInterval: 8000,
  });

  const onAddLogin = () => {
    if (!newLoginUsername.trim()) return;
    addLoginMutation.mutate({
      login_username: newLoginUsername.trim(),
      login_password: newLoginPassword.trim() || undefined,
      totp_seed: newLoginTotpSeed.trim() || undefined,
    });
  };

  const onStartAccountCreate = () => {
    if (!createEmail.trim() || !createFullName.trim() || !createUsername.trim() || !createPassword.trim()) return;
    startAccountCreateMutation.mutate({
      strategy: createStrategy,
      email: createEmail.trim(),
      full_name: createFullName.trim(),
      login_username: createUsername.trim(),
      login_password: createPassword.trim(),
      max_wait_seconds: 300,
    });
  };

  const onCancelAccountCreate = () => {
    cancelAccountCreateMutation.mutate();
  };
  const openPasswordModal = (login: string) => {
    setPasswordModalLogin(login);
    setPasswordModalValue("");
    setPasswordModalOpen(true);
  };

  useEffect(() => {
    const options = (loginsQ.data ?? []).map((l) => (l.login_username || "").trim()).filter(Boolean);
    if (!options.length) {
      setSelectedAuthLogin("");
      return;
    }
    if (!selectedAuthLogin || !options.includes(selectedAuthLogin)) {
      setSelectedAuthLogin(options[0]);
    }
  }, [loginsQ.data, selectedAuthLogin]);

  return (
    <section>
      <header className="page-header">
        <h1>Accounts</h1>
        <p>Tracking account readiness and session state for target monitoring and timeline collection runs.</p>
      </header>
      <article className="card">
        <h3>Account Setup Wizard</h3>
        <div className="wizard-steps">
          <div className="wizard-step">
            <strong>1. Add collector account</strong>
            <span>Store login credentials and optional TOTP seed.</span>
          </div>
          <div className="wizard-step">
            <strong>2. Run first capture</strong>
            <span>Use Operations to create a schedule for your target account.</span>
          </div>
          <div className="wizard-step">
            <strong>3. Review relationship timeline</strong>
            <span>Use Targets to inspect added/removed followers and following events over time.</span>
          </div>
        </div>
        <div className="form-grid">
          <label>
            Login username
            <input
              value={newLoginUsername}
              onChange={(e) => setNewLoginUsername(e.target.value)}
              placeholder="collector account username"
            />
          </label>
          <label>
            Login password
            <input
              type="password"
              value={newLoginPassword}
              onChange={(e) => setNewLoginPassword(e.target.value)}
              placeholder="password"
            />
          </label>
          <label>
            TOTP seed (optional)
            <input
              value={newLoginTotpSeed}
              onChange={(e) => setNewLoginTotpSeed(e.target.value)}
              placeholder="base32 seed"
            />
          </label>
        </div>
        <button onClick={onAddLogin} disabled={addLoginMutation.isPending}>
          {addLoginMutation.isPending ? "Saving..." : "Save account"}
        </button>
        {addLoginMutation.error ? <p className="error">{(addLoginMutation.error as Error).message}</p> : null}
        <p className="hint">Current default login mode: {cfgQ.data?.config?.run_login_mode || "auto"}</p>
      </article>
      <article className="card">
        <h3>Account Factory (automated signup)</h3>
        <div className="form-grid">
          <label>
            Signup mode
            <select value={createStrategy} onChange={(e) => setCreateStrategy((e.target.value as "private_api" | "guided_browser"))}>
              <option value="private_api">instagrapi private API (recommended)</option>
              <option value="guided_browser">guided browser fallback</option>
            </select>
          </label>
          <label>
            Email
            <input value={createEmail} onChange={(e) => setCreateEmail(e.target.value)} placeholder="name@example.com" />
          </label>
          <label>
            Full name
            <input value={createFullName} onChange={(e) => setCreateFullName(e.target.value)} placeholder="Display name" />
          </label>
          <label>
            Username
            <input value={createUsername} onChange={(e) => setCreateUsername(e.target.value)} placeholder="new_instagram_username" />
          </label>
          <label>
            Password
            <input type="password" value={createPassword} onChange={(e) => setCreatePassword(e.target.value)} placeholder="strong password" />
          </label>
        </div>
        <div className="row gap">
          <button onClick={onStartAccountCreate} disabled={startAccountCreateMutation.isPending}>
            {startAccountCreateMutation.isPending ? "Starting..." : "Start account creation"}
          </button>
          <button className="btn-secondary" onClick={onCancelAccountCreate} disabled={cancelAccountCreateMutation.isPending}>
            {cancelAccountCreateMutation.isPending ? "Cancelling..." : "Cancel"}
          </button>
        </div>
        {startAccountCreateMutation.error ? <p className="error">{(startAccountCreateMutation.error as Error).message}</p> : null}
        {cancelAccountCreateMutation.error ? <p className="error">{(cancelAccountCreateMutation.error as Error).message}</p> : null}
        <p className="hint">
          State: {accountCreateQ.data?.job?.state || "idle"}
          {accountCreateQ.data?.job?.login_username ? ` · @${accountCreateQ.data.job.login_username}` : ""}
          {accountCreateQ.data?.job?.message ? ` · ${accountCreateQ.data.job.message}` : ""}
        </p>
        <p className="hint">For private API mode, submit email/SMS verification code in the legacy Account Vault challenge field if prompted.</p>
        <div className="table-wrap">
          <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {(accountCreateQ.data?.log ?? []).join("\n") || "(no account factory logs yet)"}
          </pre>
        </div>
      </article>
      <article className="card">
        <p className="hint">Ready sessions {readyCount} / {loginsQ.data?.length ?? 0}</p>
        <div className="table-wrap desktop-only">
          <table>
            <thead>
              <tr>
                <th>Login</th>
                <th>Password</th>
                <th>TOTP</th>
                <th>2FA Method</th>
                <th>Session</th>
                <th>Session Streak</th>
                <th>Last Error</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {(loginsQ.data ?? []).map((login, idx) => (
                <tr key={`${login.login_username || "login"}-${idx}`}>
                  <td>{login.login_username || "-"}</td>
                  <td><span className={`pill ${login.has_password ? "good" : "neutral"}`}>{login.has_password ? "yes" : "no"}</span></td>
                  <td><span className={`pill ${login.has_totp_seed ? "good" : "neutral"}`}>{login.has_totp_seed ? "yes" : "no"}</span></td>
                  <td>{login.two_factor_method || "unknown"}</td>
                  <td><span className={`pill ${login.private_session_exists ? "good" : "bad"}`}>{login.private_session_exists ? "ready" : "missing"}</span></td>
                  <td>{login.session_fail_streak || 0}{login.session_marked_stale ? " (stale)" : ""}</td>
                  <td>{login.last_error || "-"}</td>
                  <td className="row gap">
                    <button
                      className="btn-secondary"
                      onClick={() => login.login_username && requestResetMutation.mutate({ login: login.login_username })}
                      disabled={requestResetMutation.isPending || !login.login_username}
                    >
                      Send reset link
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => login.login_username && openPasswordModal(login.login_username)}
                      disabled={!login.login_username}
                    >
                      Set new password
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => login.login_username && resetLoginMutation.mutate(login.login_username)}
                      disabled={resetLoginMutation.isPending || !login.login_username}
                    >
                      Reset Session
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => login.login_username && deleteLoginMutation.mutate(login.login_username)}
                      disabled={deleteLoginMutation.isPending || !login.login_username}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
              {!(loginsQ.data ?? []).length ? (
                <tr><td colSpan={8} className="hint">No login accounts returned.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="mobile-only mobile-login-list">
          {(loginsQ.data ?? []).map((login, idx) => (
            <article className="card mobile-login-card" key={`m-${login.login_username || "login"}-${idx}`}>
              <h4>{login.login_username || "-"}</h4>
              <p className="hint">Password: {login.has_password ? "yes" : "no"}</p>
              <p className="hint">TOTP: {login.has_totp_seed ? "yes" : "no"}</p>
              <p className="hint">2FA method: {login.two_factor_method || "unknown"}</p>
              <p className="hint">Session: {login.private_session_exists ? "ready" : "missing"}</p>
              <p className="hint">Session fail streak: {login.session_fail_streak || 0}</p>
              <p className="hint">Last error: {login.last_error || "-"}</p>
              <div className="row gap">
                <button
                  className="btn-secondary"
                  onClick={() => login.login_username && requestResetMutation.mutate({ login: login.login_username })}
                  disabled={requestResetMutation.isPending || !login.login_username}
                >
                  Send reset link
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => login.login_username && openPasswordModal(login.login_username)}
                  disabled={!login.login_username}
                >
                  Set new password
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => login.login_username && resetLoginMutation.mutate(login.login_username)}
                  disabled={resetLoginMutation.isPending || !login.login_username}
                >
                  Reset Session
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => login.login_username && deleteLoginMutation.mutate(login.login_username)}
                  disabled={deleteLoginMutation.isPending || !login.login_username}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
          {!(loginsQ.data ?? []).length ? <p className="hint">No login accounts returned.</p> : null}
        </div>
      </article>
      <article className="card">
        <h3>Auth Diagnostics</h3>
        {requestResetMutation.data ? (
          <p className="hint">
            Reset request sent for @{requestResetMutation.data.login_username} via proxy:{" "}
            {requestResetMutation.data.via_proxy ? "yes" : "no"}
          </p>
        ) : null}
        {requestResetMutation.error ? <p className="error">{(requestResetMutation.error as Error).message}</p> : null}
        <div className="form-grid">
          <label>
            Login
            <select value={selectedAuthLogin} onChange={(e) => setSelectedAuthLogin(e.target.value)}>
              {(loginsQ.data ?? []).map((login, idx) => (
                <option key={`${login.login_username || "login"}-${idx}`} value={login.login_username || ""}>
                  {login.login_username || "-"}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="row gap">
          <button
            onClick={() => selectedAuthLogin && authPreflightMutation.mutate(selectedAuthLogin)}
            disabled={authPreflightMutation.isPending || !selectedAuthLogin}
          >
            {authPreflightMutation.isPending ? "Checking..." : "Run auth preflight"}
          </button>
        </div>
        {authPreflightMutation.error ? <p className="error">{(authPreflightMutation.error as Error).message}</p> : null}
        {authPreflightMutation.data ? (
          <div>
            <p className="hint">
              2FA method: {authPreflightMutation.data.two_factor_method || "unknown"} · Session streak:{" "}
              {authPreflightMutation.data.session_fail_streak || 0}
              {authPreflightMutation.data.session_marked_stale ? " (stale)" : ""}
            </p>
            <p className="hint">
              Clock skew:{" "}
              {authPreflightMutation.data.clock_skew?.ok
                ? `${authPreflightMutation.data.clock_skew.skew_seconds ?? 0}s`
                : authPreflightMutation.data.clock_skew?.error || "unavailable"}
            </p>
            {(authPreflightMutation.data.warnings ?? []).length ? (
              <div className="table-wrap">
                <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                  {(authPreflightMutation.data.warnings ?? []).map((w) => `- ${w}`).join("\n")}
                </pre>
              </div>
            ) : (
              <p className="hint">No preflight warnings.</p>
            )}
          </div>
        ) : null}
        <p className="hint">Auth trace (latest)</p>
        <div className="table-wrap">
          <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {(authTraceQ.data?.trace ?? [])
              .map((row) => {
                const parts = [row.at || "-", row.event || "event"];
                if (row.two_factor_method) parts.push(`method=${row.two_factor_method}`);
                if (row.totp_source) parts.push(`source=${row.totp_source}`);
                if (typeof row.session_fail_streak === "number") parts.push(`streak=${row.session_fail_streak}`);
                if (row.error_code) parts.push(`code=${row.error_code}`);
                return parts.join(" · ");
              })
              .join("\n") || "(no auth trace yet)"}
          </pre>
        </div>
      </article>
      {passwordModalOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setPasswordModalOpen(false)}>
          <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>Set New Password</h3>
            <p className="hint">Store a new password for @{passwordModalLogin}. It will be used on the next login flow.</p>
            <input
              type="password"
              placeholder="new password"
              value={passwordModalValue}
              onChange={(e) => setPasswordModalValue(e.target.value)}
              autoFocus
            />
            <div className="row gap">
              <button
                disabled={setNewPasswordMutation.isPending || !passwordModalLogin || passwordModalValue.trim().length < 6}
                onClick={() =>
                  setNewPasswordMutation.mutate({
                    login: passwordModalLogin,
                    password: passwordModalValue.trim(),
                  })
                }
              >
                {setNewPasswordMutation.isPending ? "Saving..." : "Save password"}
              </button>
              <button className="btn-secondary" onClick={() => setPasswordModalOpen(false)}>
                Close
              </button>
            </div>
            {setNewPasswordMutation.error ? <p className="error">{(setNewPasswordMutation.error as Error).message}</p> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function SettingsPage() {
  const qc = useQueryClient();
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: getConfig, refetchInterval: 30000 });
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({});
  const [proxyPassword, setProxyPassword] = useState("");
  const [loadedBackend, setLoadedBackend] = useState<string>("");

  const backendProfiles: Record<string, Record<string, string | number | boolean>> = {
    private: {
      run_http_timeout_seconds: 60,
      run_request_timeout: 60,
      run_private_request_sleep_seconds: 0.8,
      run_item_delay_min: 0.6,
      run_item_delay_max: 1.4,
      run_initial_fetch_delay_seconds: 6,
      run_pause_every_min: 120,
      run_pause_every_max: 180,
      run_pause_seconds_min: 20,
      run_pause_seconds_max: 45,
      run_rate_limit_cooldown_seconds: 3600,
    },
    browser: {
      run_http_timeout_seconds: 60,
      run_request_timeout: 60,
      run_private_request_sleep_seconds: 0,
      run_item_delay_min: 0.25,
      run_item_delay_max: 0.75,
      run_initial_fetch_delay_seconds: 1,
      run_pause_every_min: 0,
      run_pause_every_max: 0,
      run_pause_seconds_min: 0,
      run_pause_seconds_max: 0,
      run_rate_limit_cooldown_seconds: 1800,
    },
  };

  useEffect(() => {
    const c = cfgQ.data?.config;
    if (!c) return;
    const nextDraft: Record<string, string | number | boolean> = {};
    for (const [key, value] of Object.entries(c)) {
      if (key.startsWith("recon_")) continue;
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        nextDraft[key] = value;
      }
    }
    setDraft(nextDraft);
    setLoadedBackend(String(c.run_scraper_backend || ""));
  }, [cfgQ.data]);

  const updateCfgMutation = useMutation({
    mutationFn: updateConfig,
    onSuccess: async () => {
      setProxyPassword("");
      await qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const proxyTestMutation = useMutation({
    mutationFn: testProxy,
  });

  const sectionForKey = (key: string): string => {
    if (key.startsWith("run_") || key.startsWith("private_")) return "Run";
    if (key.startsWith("proxy_")) return "Proxy";
    if (key.startsWith("schedule_") || key.startsWith("ui_timezone")) return "Schedule";
    if (key.startsWith("monitor_")) return "Monitor";
    if (key.startsWith("unfollow_")) return "Unfollow";
    return "Other";
  };

  const labelForKey = (key: string): string =>
    key
      .replace(/_set$/, " configured")
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");

  const onSaveConfig = () => {
    const payload: Partial<ConfigValues> & { proxy_password?: string; _apply_backend_profile?: boolean } = {};
    for (const [key, value] of Object.entries(draft)) {
      if (key.startsWith("recon_")) continue;
      if (key.endsWith("_set")) continue;
      (payload as Record<string, string | number | boolean>)[key] = value;
    }
    const selectedBackend = String(draft.run_scraper_backend || "");
    if (selectedBackend && loadedBackend && selectedBackend !== loadedBackend) {
      payload._apply_backend_profile = true;
    }
    if (proxyPassword.trim()) payload.proxy_password = proxyPassword.trim();
    updateCfgMutation.mutate(payload);
  };

  const sections = useMemo(() => {
    const grouped = new Map<string, string[]>();
    for (const key of Object.keys(draft)) {
      const section = sectionForKey(key);
      if (!grouped.has(section)) grouped.set(section, []);
      grouped.get(section)?.push(key);
    }
    const order = ["Run", "Proxy", "Schedule", "Monitor", "Unfollow", "Other"];
    return order
      .map((section) => ({ section, keys: (grouped.get(section) || []).sort() }))
      .filter((x) => x.keys.length > 0);
  }, [draft]);

  return (
    <section>
      <header className="page-header">
        <h1>Settings</h1>
        <p>Runtime settings that affect target-account tracking cadence and operations behavior.</p>
      </header>
      {!cfgQ.data?.config ? (
        <article className="card"><p className="hint">Loading settings…</p></article>
      ) : null}
      {sections.map(({ section, keys }) => (
        <article className="card settings-group" key={section}>
          <h3>{section} Settings</h3>
          <div className="settings-grid">
            {keys.map((key) => {
              const value = draft[key];
              if (typeof value === "undefined") return null;
              if (key === "run_login_mode") {
                return (
                  <label key={key}>
                    {labelForKey(key)}
                    <select value={String(value)} onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}>
                      <option value="auto">auto</option>
                      <option value="session_only">session_only</option>
                      <option value="password">password</option>
                      <option value="anonymous">anonymous</option>
                    </select>
                  </label>
                );
              }
              if (key === "run_scraper_backend") {
                return (
                  <label key={key}>
                    {labelForKey(key)}
                    <select
                      value={String(value)}
                      onChange={(e) =>
                        setDraft((prev) => {
                          const nextBackend = e.target.value === "private" ? "private" : "browser";
                          const profile = backendProfiles[nextBackend] || {};
                          return { ...prev, run_scraper_backend: nextBackend, ...profile };
                        })
                      }
                    >
                      <option value="browser">browser (web session)</option>
                      <option value="private">instagrapi (private API)</option>
                    </select>
                    <span className="hint">Switching backend auto-loads the tuned delay/rate profile.</span>
                  </label>
                );
              }
              if (typeof value === "boolean") {
                return (
                  <label key={key}>
                    {labelForKey(key)}
                    <select
                      value={value ? "true" : "false"}
                      onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value === "true" }))}
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  </label>
                );
              }
              if (typeof value === "number") {
                return (
                  <label key={key}>
                    {labelForKey(key)}
                    <input
                      type="number"
                      value={String(value)}
                      onChange={(e) => {
                        const num = e.target.value === "" ? 0 : Number(e.target.value);
                        setDraft((prev) => ({ ...prev, [key]: Number.isFinite(num) ? num : value }));
                      }}
                    />
                  </label>
                );
              }
              return (
                <label key={key}>
                  {labelForKey(key)}
                  <input
                    value={String(value)}
                    onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                  />
                </label>
              );
            })}
          </div>
        </article>
      ))}
      <article className="card">
        <h3>Secrets And Actions</h3>
        <div className="settings-grid">
          <label>
            Proxy password
            <input
              type="password"
              value={proxyPassword}
              onChange={(e) => setProxyPassword(e.target.value)}
              placeholder={cfgQ.data?.config?.proxy_password_set ? "stored (leave blank to keep)" : "set proxy password"}
            />
          </label>
        </div>
        <div className="row gap">
          <button onClick={onSaveConfig} disabled={updateCfgMutation.isPending}>
            {updateCfgMutation.isPending ? "Saving..." : "Save config"}
          </button>
          <button
            className="btn-secondary"
            onClick={() => proxyTestMutation.mutate()}
            disabled={proxyTestMutation.isPending}
          >
            {proxyTestMutation.isPending ? "Testing..." : "Test proxy"}
          </button>
        </div>
        {updateCfgMutation.error ? <p className="error">{(updateCfgMutation.error as Error).message}</p> : null}
        {proxyTestMutation.error ? <p className="error">{(proxyTestMutation.error as Error).message}</p> : null}
        {proxyTestMutation.data ? (
          <p className={`pill ${proxyTestMutation.data.ok ? "good" : "bad"}`}>
            {proxyTestMutation.data.ok
              ? `Proxy OK (${proxyTestMutation.data.status || 200}, ${proxyTestMutation.data.latency_ms || 0}ms)`
              : `Proxy failed: ${proxyTestMutation.data.error || "unknown error"}`}
          </p>
        ) : null}
      </article>
    </section>
  );
}

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<CommandCenterPage />} />
        <Route path="/targets" element={<TargetsPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  );
}
