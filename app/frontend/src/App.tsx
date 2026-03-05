import { useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addLogin,
  cancelAccountCreate,
  getAccountCreateStatus,
  createSchedule,
  deleteLogin,
  deleteRun,
  deleteSchedule,
  getAppStatus,
  getRuns,
  getRunDetail,
  getConfig,
  getLogins,
  getRelationshipEvents,
  getRelationshipHistory,
  getRunStatus,
  getSchedules,
  getTargetsSummary,
  getUnfollowStatus,
  getUnfollowPreview,
  getAuthTrace,
  getRunJobDetail,
  getRunJobStatus,
  resetLogin,
  runAuthPreflight,
  cancelRun,
  cancelUnfollow,
  requestPasswordReset,
  startRun,
  startAccountCreate,
  startUnfollow,
  setLoginNewPassword,
  submitChallengeCode,
  testProxy,
  undoRun,
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
  let normalized = value;
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$/);
  if (m) {
    normalized = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`;
  }
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

function instagramProfileUrl(username?: string): string {
  const clean = String(username || "").trim().replace(/^@+/, "");
  if (!clean) return "https://www.instagram.com/";
  return `https://www.instagram.com/${encodeURIComponent(clean)}/`;
}

function toApiTimestamp(dt: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}_${pad(dt.getHours())}-${pad(
    dt.getMinutes()
  )}-${pad(dt.getSeconds())}`;
}

function csvEscape(value: string | number | boolean | null | undefined): string {
  const text = String(value ?? "");
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function downloadCsvRows(filename: string, headers: string[], rows: Array<Array<string | number | boolean | null | undefined>>) {
  if (!rows.length) return;
  const csv = [headers.map(csvEscape).join(","), ...rows.map((row) => row.map(csvEscape).join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
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
          <NavLink to="/explorer">Explorer</NavLink>
          <NavLink to="/operations">Operations</NavLink>
          <NavLink to="/unfollow">Unfollow</NavLink>
          <NavLink to="/accounts">Accounts</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </aside>

      <main className="content">{children}</main>

      <nav className="mobile-nav">
        <NavLink to="/" end>
          Home
        </NavLink>
        <NavLink to="/targets">Targets</NavLink>
        <NavLink to="/explorer">Explore</NavLink>
        <NavLink to="/operations">Ops</NavLink>
        <NavLink to="/unfollow">Unfollow</NavLink>
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
        <article className="card explorer-panel">
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

function ExplorerPage() {
  const qc = useQueryClient();
  const targetsQ = useQuery({ queryKey: ["targets-summary"], queryFn: getTargetsSummary, refetchInterval: 30000 });
  const [selectedTarget, setSelectedTarget] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [eventsScope, setEventsScope] = useState<"target" | "run">("target");
  const [eventsRelation, setEventsRelation] = useState<"" | "followers" | "following">("");
  const [eventsType, setEventsType] = useState<"" | "added" | "removed">("");
  const [eventsUsername, setEventsUsername] = useState("");
  const [eventsWindowDays, setEventsWindowDays] = useState("7");

  useEffect(() => {
    if (!selectedTarget && (targetsQ.data?.length || 0) > 0) {
      setSelectedTarget(String(targetsQ.data?.[0]?.target_username || ""));
    }
  }, [selectedTarget, targetsQ.data]);

  const runsQ = useQuery({
    queryKey: ["runs", selectedTarget],
    queryFn: () => getRuns(selectedTarget, 60),
    enabled: Boolean(selectedTarget),
    refetchInterval: 15000,
  });

  useEffect(() => {
    const firstId = runsQ.data?.[0]?.id;
    if (typeof firstId === "number" && !selectedRunId) {
      setSelectedRunId(firstId);
    }
  }, [runsQ.data, selectedRunId]);

  const runDetailQ = useQuery({
    queryKey: ["run-detail", selectedRunId],
    queryFn: () => getRunDetail(selectedRunId as number),
    enabled: typeof selectedRunId === "number",
    refetchInterval: 12000,
  });

  const eventsQ = useQuery({
    queryKey: [
      "relationship-events",
      selectedTarget,
      eventsScope,
      eventsRelation,
      eventsType,
      eventsUsername,
      eventsWindowDays,
      selectedRunId,
    ],
    queryFn: () => {
      const days = Number(eventsWindowDays || "7");
      const observed_from =
        Number.isFinite(days) && days > 0 ? toApiTimestamp(new Date(Date.now() - days * 24 * 60 * 60 * 1000)) : undefined;
      const run_id = eventsScope === "run" && typeof selectedRunId === "number" ? selectedRunId : undefined;
      return getRelationshipEvents(selectedTarget, {
        relation_type: eventsRelation,
        event_type: eventsType,
        username: eventsUsername.trim() || undefined,
        observed_from,
        run_id,
        limit: Number.isFinite(days) && days === 0 ? 1000 : 400,
      });
    },
    enabled: Boolean(selectedTarget),
    refetchInterval: 12000,
  });

  const deleteRunMutation = useMutation({
    mutationFn: deleteRun,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["runs", selectedTarget] }),
        qc.invalidateQueries({ queryKey: ["targets-summary"] }),
      ]);
    },
  });
  const undoRunMutation = useMutation({
    mutationFn: undoRun,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["runs", selectedTarget] }),
        qc.invalidateQueries({ queryKey: ["targets-summary"] }),
      ]);
    },
  });

  const detail = runDetailQ.data;
  const targetSlug = (selectedTarget || "target").replace(/[^a-zA-Z0-9._-]+/g, "_");
  const runs = runsQ.data ?? [];
  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;
  const detailGroups = detail
    ? [
        { key: "followers-added", title: "Followers Added", items: detail.followers_added_list ?? [], tone: "good" },
        { key: "followers-removed", title: "Followers Removed", items: detail.followers_removed_list ?? [], tone: "bad" },
        { key: "following-added", title: "Following Added", items: detail.followees_added_list ?? [], tone: "info" },
        { key: "following-removed", title: "Following Removed", items: detail.followees_removed_list ?? [], tone: "neutral" },
      ]
    : [];

  return (
    <section className="explorer-shell">
      <header className="page-header">
        <h1>Explorer</h1>
        <p>Run history and run-level relationship deltas for each tracked target account.</p>
      </header>
      <div className="explorer-kpis">
        <article className="explorer-kpi">
          <span>Selected Target</span>
          <strong>@{selectedTarget || "-"}</strong>
        </article>
        <article className="explorer-kpi">
          <span>Total Runs Loaded</span>
          <strong>{runsQ.data?.length ?? 0}</strong>
        </article>
        <article className="explorer-kpi">
          <span>Events In View</span>
          <strong>{eventsQ.data?.length ?? 0}</strong>
        </article>
      </div>
      <div className="explorer-main-grid">
        <article className="card">
          <h3>Run Selector</h3>
          <div className="form-grid">
            <label>
              Target
              <select value={selectedTarget} onChange={(e) => setSelectedTarget(e.target.value)}>
                {(targetsQ.data ?? []).map((t, idx) => (
                  <option key={`${t.target_username || "target"}-${idx}`} value={t.target_username || ""}>
                    {t.target_username || "-"}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="hint">Pick a run. Details render on the right.</p>
          {runs.length ? (
            <>
              <label>
                Run
                <select
                  value={selectedRunId ?? ""}
                  onChange={(e) => setSelectedRunId(e.target.value ? Number(e.target.value) : null)}
                  className="run-selector-input"
                >
                  {runs.map((run, idx) => {
                    const followerDelta = (run.followers_added ?? 0) - (run.followers_removed ?? 0);
                    return (
                      <option key={`${run.id || "run"}-${idx}`} value={run.id ?? ""}>
                        #{run.id ?? "-"} · {formatTime(run.timestamp || undefined)} · {followerDelta >= 0 ? "+" : ""}{followerDelta}
                      </option>
                    );
                  })}
                </select>
              </label>
              {selectedRun ? (
                <div className="run-selector-summary">
                  <div className="list-title">Run #{selectedRun.id ?? "-"} · {formatTime(selectedRun.timestamp || undefined)}</div>
                  <div className="list-meta">via @{selectedRun.login_username || "-"} · duration {typeof selectedRun.duration_seconds === "number" ? `${selectedRun.duration_seconds}s` : "-"}</div>
                  <div className="list-meta">
                    followers {selectedRun.followers_count ?? "-"} · change {((selectedRun.followers_added ?? 0) - (selectedRun.followers_removed ?? 0)) >= 0 ? "+" : ""}
                    {(selectedRun.followers_added ?? 0) - (selectedRun.followers_removed ?? 0)} ({selectedRun.followers_added ?? 0} new / {selectedRun.followers_removed ?? 0} lost)
                  </div>
                  <div className="list-meta">
                    following {selectedRun.followees_count ?? "-"} · change {((selectedRun.followees_added ?? 0) - (selectedRun.followees_removed ?? 0)) >= 0 ? "+" : ""}
                    {(selectedRun.followees_added ?? 0) - (selectedRun.followees_removed ?? 0)} ({selectedRun.followees_added ?? 0} new / {selectedRun.followees_removed ?? 0} lost) · NF {selectedRun.non_followbacks_count ?? "-"}
                  </div>
                  <div className="row gap" style={{ marginTop: "0.55rem" }}>
                    <button
                      className="btn-secondary"
                      onClick={() => selectedRun.id && deleteRunMutation.mutate(selectedRun.id)}
                      disabled={deleteRunMutation.isPending || !selectedRun.id}
                    >
                      Delete
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={() => selectedRun.id && undoRunMutation.mutate(selectedRun.id)}
                      disabled={undoRunMutation.isPending || !selectedRun.id}
                    >
                      Undo
                    </button>
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="explorer-empty">
              <h4>No runs yet</h4>
              <p>Create your first run from Operations, then return to Explorer for diff insights.</p>
            </div>
          )}
          {deleteRunMutation.error ? <p className="error">{(deleteRunMutation.error as Error).message}</p> : null}
          {undoRunMutation.error ? <p className="error">{(undoRunMutation.error as Error).message}</p> : null}
        </article>
        <article className="card explorer-panel">
          <h3>Run Detail {selectedRunId ? `#${selectedRunId}` : ""}</h3>
          {!detail ? (
            <div className="explorer-empty detail-empty">
              <h4>Select a run</h4>
              <p>Pick a run from the selector to inspect follower/following deltas and exportable lists.</p>
            </div>
          ) : null}
          {detail ? (
            <>
              <p className="hint">
                @{detail.target_username || "-"} via @{detail.login_username || "-"} · {formatTime(detail.timestamp || undefined)}
              </p>
              <p className="hint">
                Followers {detail.followers_count ?? "-"} · change {(detail.followers_added ?? 0) - (detail.followers_removed ?? 0) >= 0 ? "+" : ""}
                {(detail.followers_added ?? 0) - (detail.followers_removed ?? 0)} ({detail.followers_added ?? 0} new / {detail.followers_removed ?? 0} lost)
              </p>
              <p className="hint">
                Following {detail.followees_count ?? "-"} · change {(detail.followees_added ?? 0) - (detail.followees_removed ?? 0) >= 0 ? "+" : ""}
                {(detail.followees_added ?? 0) - (detail.followees_removed ?? 0)} ({detail.followees_added ?? 0} new / {detail.followees_removed ?? 0} lost)
              </p>
              <div className="row gap">
                <button
                  className="btn-secondary"
                  onClick={() =>
                    downloadCsvRows(
                      `${targetSlug}-followers.csv`,
                      ["username"],
                      (detail.followers ?? []).map((u) => [u])
                    )
                  }
                >
                  Export followers CSV
                </button>
                <button
                  className="btn-secondary"
                  onClick={() =>
                    downloadCsvRows(
                      `${targetSlug}-following.csv`,
                      ["username"],
                      (detail.followees ?? []).map((u) => [u])
                    )
                  }
                >
                  Export following CSV
                </button>
                <button
                  className="btn-secondary"
                  onClick={() =>
                    downloadCsvRows(
                      `${targetSlug}-no-follow-back.csv`,
                      ["username"],
                      (detail.non_followbacks ?? []).map((u) => [u])
                    )
                  }
                >
                  Export non-followbacks CSV
                </button>
              </div>
              <div className="run-detail-grid">
                {detailGroups.map((group) => (
                  <section className={`run-detail-card run-detail-card--${group.key}`} key={group.key}>
                    <div className="run-detail-head">
                      <h4>{group.title}</h4>
                      <span className={`pill ${group.tone}`}>{group.items.length}</span>
                    </div>
                    <div className="run-detail-list">
                      {group.items.length ? (
                        <div className="run-detail-plain-list">
                          {group.items.slice(0, 100).map((u, idx) => (
                            <a
                              key={`${group.key}-${u}-${idx}`}
                              className="run-detail-plain-item"
                              href={instagramProfileUrl(u)}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={`Open @${u} on Instagram`}
                            >
                              @{u}
                            </a>
                          ))}
                        </div>
                      ) : (
                        <p className="hint">No changes in this group.</p>
                      )}
                      {group.items.length > 100 ? <p className="hint run-detail-truncation">Showing first 100 of {group.items.length} usernames.</p> : null}
                    </div>
                  </section>
                ))}
              </div>
            </>
          ) : null}
        </article>
      </div>
      <article className="card explorer-panel">
        <h3>Relationship Events</h3>
        <div className="form-grid">
          <label>
            Scope
            <select value={eventsScope} onChange={(e) => setEventsScope(e.target.value as "target" | "run")}>
              <option value="target">target</option>
              <option value="run">selected run only</option>
            </select>
          </label>
          <label>
            Relation
            <select value={eventsRelation} onChange={(e) => setEventsRelation(e.target.value as "" | "followers" | "following")}>
              <option value="">all</option>
              <option value="followers">followers</option>
              <option value="following">following</option>
            </select>
          </label>
          <label>
            Event type
            <select value={eventsType} onChange={(e) => setEventsType(e.target.value as "" | "added" | "removed")}>
              <option value="">all</option>
              <option value="added">added</option>
              <option value="removed">removed</option>
            </select>
          </label>
          <label>
            Window days (0=all)
            <input value={eventsWindowDays} onChange={(e) => setEventsWindowDays(e.target.value)} />
          </label>
          <label>
            Username contains
            <input value={eventsUsername} onChange={(e) => setEventsUsername(e.target.value)} placeholder="@username" />
          </label>
        </div>
        <div className="row gap">
          <span className="hint">
            {(eventsQ.data ?? []).length} events · {(eventsQ.data ?? []).filter((e) => e.event_type === "added").length} added · {(eventsQ.data ?? []).filter((e) => e.event_type === "removed").length} removed
          </span>
          <button
            className="btn-secondary"
            onClick={() =>
              downloadCsvRows(
                `${targetSlug}-relationship-events.csv`,
                ["id", "observed_at", "username", "relation_type", "event_type", "run_id", "login_username"],
                (eventsQ.data ?? []).map((e) => [
                  e.id ?? "",
                  e.observed_at ?? "",
                  e.username ?? "",
                  e.relation_type ?? "",
                  e.event_type ?? "",
                  e.run_id ?? "",
                  e.login_username ?? "",
                ])
              )
            }
          >
            Export events CSV
          </button>
        </div>
        <div className="table-wrap desktop-only">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Username</th>
                <th>Relation</th>
                <th>Type</th>
                <th>Run</th>
                <th>Collector</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {(eventsQ.data ?? []).map((ev, idx) => (
                <tr key={`ev-${ev.id || idx}`}>
                  <td>{formatTime(ev.observed_at)}</td>
                  <td>
                    {ev.username ? (
                      <a href={instagramProfileUrl(ev.username)} target="_blank" rel="noopener noreferrer" className="inline-link">
                        @{ev.username}
                      </a>
                    ) : (
                      "-"
                    )}
                  </td>
                  <td>{ev.relation_type || "-"}</td>
                  <td><span className={`pill ${ev.event_type === "added" ? "good" : "bad"}`}>{ev.event_type || "-"}</span></td>
                  <td>{ev.run_id ?? "-"}</td>
                  <td>{ev.login_username || "-"}</td>
                  <td>
                    <button className="btn-secondary" onClick={() => setSelectedRunId(ev.run_id ?? null)} disabled={!ev.run_id}>
                      Open run
                    </button>
                  </td>
                </tr>
              ))}
              {!(eventsQ.data ?? []).length ? (
                <tr>
                  <td colSpan={7} className="hint">No events matched the current filters.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="mobile-only">
          <div className="entity-list">
            {(eventsQ.data ?? []).slice(0, 60).map((ev, idx) => (
              <div key={`evm-${ev.id || idx}`} className="run-detail-card">
                <div className="run-detail-head">
                  <h4>
                    {ev.username ? (
                      <a href={instagramProfileUrl(ev.username)} target="_blank" rel="noopener noreferrer" className="inline-link">
                        @{ev.username}
                      </a>
                    ) : (
                      "-"
                    )}
                  </h4>
                  <span className={`pill ${ev.event_type === "added" ? "good" : "bad"}`}>{ev.event_type || "-"}</span>
                </div>
                <p className="hint">{ev.relation_type || "-"} · {formatTime(ev.observed_at)} · run {ev.run_id ?? "-"}</p>
                <div className="row gap" style={{ marginTop: "0.45rem" }}>
                  <button className="btn-secondary" onClick={() => setSelectedRunId(ev.run_id ?? null)} disabled={!ev.run_id}>
                    Open run
                  </button>
                </div>
              </div>
            ))}
            {!(eventsQ.data ?? []).length ? <p className="hint">No events matched the current filters.</p> : null}
          </div>
        </div>
      </article>
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
  const cancelRunMutation = useMutation({
    mutationFn: cancelRun,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["run-status"] });
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
          <button
            className="btn-secondary"
            onClick={() => cancelRunMutation.mutate({ job_id: manualJobId })}
            disabled={cancelRunMutation.isPending || !manualJobId}
          >
            {cancelRunMutation.isPending ? "Cancelling..." : "Cancel job"}
          </button>
          <span className="hint">State: {runStatusQ.data?.state || "idle"}</span>
          {manualJobId ? <span className="hint">Job: {manualJobId}</span> : null}
        </div>
        {runNowMutation.error ? <p className="error">{(runNowMutation.error as Error).message}</p> : null}
        {cancelRunMutation.error ? <p className="error">{(cancelRunMutation.error as Error).message}</p> : null}
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

function UnfollowPage() {
  const qc = useQueryClient();
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });
  const [login, setLogin] = useState("");
  const [dryRun, setDryRun] = useState(false);
  const [maxActions, setMaxActions] = useState("0");
  const [delayMin, setDelayMin] = useState("25");
  const [delayMax, setDelayMax] = useState("45");

  useEffect(() => {
    if (!login && (loginsQ.data?.length || 0) > 0) {
      setLogin(String(loginsQ.data?.[0]?.login_username || ""));
    }
  }, [login, loginsQ.data]);

  const statusQ = useQuery({
    queryKey: ["unfollow-status", login],
    queryFn: () => getUnfollowStatus(login),
    enabled: Boolean(login),
    refetchInterval: 6000,
  });
  const previewQ = useQuery({
    queryKey: ["unfollow-preview", login],
    queryFn: () => getUnfollowPreview(login),
    enabled: Boolean(login),
  });

  const previewMutation = useMutation({
    mutationFn: () => getUnfollowPreview(login),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["unfollow-preview", login] });
    },
  });
  const startMutation = useMutation({
    mutationFn: startUnfollow,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["unfollow-status", login] }),
        qc.invalidateQueries({ queryKey: ["unfollow-preview", login] }),
      ]);
    },
  });
  const cancelMutation = useMutation({
    mutationFn: cancelUnfollow,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["unfollow-status", login] });
    },
  });

  const onStart = () => {
    if (!login.trim()) return;
    const max = Number(maxActions);
    const minDelay = Number(delayMin);
    const maxDelay = Number(delayMax);
    startMutation.mutate({
      login_username: login.trim(),
      dry_run: dryRun,
      max_actions: Number.isFinite(max) && max > 0 ? max : undefined,
      delay_min: Number.isFinite(minDelay) ? minDelay : undefined,
      delay_max: Number.isFinite(maxDelay) ? maxDelay : undefined,
    });
  };

  return (
    <section>
      <header className="page-header">
        <h1>Unfollow</h1>
        <p>Manage non-followback unfollow batches for collector accounts in the new UI.</p>
      </header>
      <article className="card">
        <h3>Controls</h3>
        <div className="form-grid">
          <label>
            Collector login
            <select value={login} onChange={(e) => setLogin(e.target.value)}>
              <option value="">Select login</option>
              {(loginsQ.data ?? []).map((l, idx) => (
                <option key={`u-${l.login_username || "login"}-${idx}`} value={l.login_username || ""}>
                  {l.login_username || "-"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Max actions (0 = suggested)
            <input value={maxActions} onChange={(e) => setMaxActions(e.target.value)} />
          </label>
          <label>
            Delay min (seconds)
            <input value={delayMin} onChange={(e) => setDelayMin(e.target.value)} />
          </label>
          <label>
            Delay max (seconds)
            <input value={delayMax} onChange={(e) => setDelayMax(e.target.value)} />
          </label>
          <label>
            Dry run
            <select value={dryRun ? "true" : "false"} onChange={(e) => setDryRun(e.target.value === "true")}>
              <option value="false">false</option>
              <option value="true">true</option>
            </select>
          </label>
        </div>
        <div className="row gap">
          <button onClick={() => previewMutation.mutate()} disabled={previewMutation.isPending || !login}>
            {previewMutation.isPending ? "Refreshing..." : "Refresh preview"}
          </button>
          <button onClick={onStart} disabled={startMutation.isPending || !login}>
            {startMutation.isPending ? "Starting..." : "Start unfollow"}
          </button>
          <button className="btn-secondary" onClick={() => cancelMutation.mutate()} disabled={cancelMutation.isPending}>
            {cancelMutation.isPending ? "Cancelling..." : "Cancel unfollow job"}
          </button>
        </div>
        {previewMutation.error ? <p className="error">{(previewMutation.error as Error).message}</p> : null}
        {startMutation.error ? <p className="error">{(startMutation.error as Error).message}</p> : null}
        {cancelMutation.error ? <p className="error">{(cancelMutation.error as Error).message}</p> : null}
      </article>
      <article className="card">
        <h3>Status</h3>
        <p className="hint">Auth ready: {statusQ.data?.auth_ready ? "yes" : "no"} · Login: @{statusQ.data?.login_username || login || "-"}</p>
        <p className="hint">
          Non-followbacks: {statusQ.data?.non_followbacks_count ?? 0} · Eligible: {statusQ.data?.eligible_count ?? 0} · Already unfollowed: {statusQ.data?.already_unfollowed_count ?? 0} · Suggested: {statusQ.data?.suggested_max ?? "-"}
        </p>
        <p className="hint">
          Job: {String(statusQ.data?.job?.state || "idle")} {statusQ.data?.job?.message ? `· ${String(statusQ.data?.job?.message)}` : ""}
        </p>
        <div className="table-wrap">
          <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {(statusQ.data?.log ?? []).join("\n") || "(no unfollow log yet)"}
          </pre>
        </div>
      </article>
      <article className="card">
        <h3>Preview</h3>
        <p className="hint">
          Count: {previewQ.data?.count ?? 0} · Total non-followbacks: {previewQ.data?.total_non_followbacks ?? 0} · Already unfollowed: {previewQ.data?.already_unfollowed_count ?? 0}
        </p>
        <div className="entity-list">
          {(previewQ.data?.sample ?? []).map((username, idx) => (
            <div className="list-row" key={`ufs-${username}-${idx}`}>
              <div className="list-title">{username}</div>
            </div>
          ))}
          {!(previewQ.data?.sample ?? []).length ? <p className="hint">No preview sample yet.</p> : null}
        </div>
      </article>
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
        <Route path="/explorer" element={<ExplorerPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="/unfollow" element={<UnfollowPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </AppShell>
  );
}
