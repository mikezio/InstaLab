import { useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search, UserMinus, UserPlus, Users, UserCheck, ArrowRightLeft } from "lucide-react";
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
  getManualActions,
  getRunStatus,
  getSchedules,
  getTargetsSummary,
  getUiBrief,
  getUiTargetChanges,
  getUiNetwork,
  getUiTargets,
  getUiTargetTimeline,
  getUiSystemHealth,
  getUnfollowStatus,
  getUnfollowPreview,
  getAuthTrace,
  getRunJobSummary,
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
  setChallengeEmail,
  submitChallengeCode,
  testProxy,
  undoRun,
  updateConfig,
  resolveManualAction,
} from "./lib/api";
import type { ConfigValues } from "./lib/schemas";

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

function formatEpochTime(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return formatTime(new Date(value * 1000).toISOString());
}

function formatSecondsAge(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${Math.max(0, Math.round(value))}s ago`;
}

function formatDateSpan(start?: string | null, end?: string | null): string {
  if (!start && !end) return "-";
  if (start && end) return `${formatTime(start)} -> ${formatTime(end)}`;
  if (start) return `Since ${formatTime(start)}`;
  return `Until ${formatTime(end || undefined)}`;
}

function formatRelativeAge(value?: string | null): string {
  if (!value) return "No full run yet";
  let normalized = value;
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$/);
  if (m) {
    normalized = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`;
  }
  const then = new Date(normalized);
  if (Number.isNaN(then.getTime())) return formatTime(value || undefined);
  const diffMs = Date.now() - then.getTime();
  if (diffMs < 0) return "Just now";
  const hours = Math.floor(diffMs / (1000 * 60 * 60));
  if (hours < 1) {
    const minutes = Math.max(1, Math.floor(diffMs / (1000 * 60)));
    return `${minutes}m ago`;
  }
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function instagramProfileUrl(username?: string): string {
  const clean = String(username || "").trim().replace(/^@+/, "");
  if (!clean) return "https://www.instagram.com/";
  return `https://www.instagram.com/${encodeURIComponent(clean)}/`;
}

function normalizeUsername(value?: string): string {
  return String(value || "").trim().replace(/^@+/, "");
}

function usernameKey(value?: string): string {
  return normalizeUsername(value).toLowerCase();
}

const targetHandleInputProps = {
  type: "text" as const,
  inputMode: "text" as const,
  autoComplete: "new-password",
  autoCorrect: "off" as const,
  autoCapitalize: "none" as const,
  spellCheck: false,
  name: "target_handle",
  enterKeyHint: "go" as const,
  "data-1p-ignore": "true",
  "data-lpignore": "true",
};

type LaunchSchedulePreset = "once_daily" | "twice_daily" | "weekly" | "every_n_days";
type RunChangeDetail = {
  username?: string;
  full_name?: string | null;
  profile_pic_url?: string | null;
  profile_pic_url_hd?: string | null;
  avatar_url?: string | null;
  account_status?: string | null;
  account_status_checked_at?: string | null;
  account_status_error?: string | null;
  relation_type?: string;
  event_type?: string;
  observed_at?: string;
  first_seen?: string | null;
  last_seen?: string | null;
  first_seen_run_id?: number | null;
  last_seen_run_id?: number | null;
  first_seen_known?: number | boolean | null;
  active?: number | boolean | null;
  unfollowed_at?: string | null;
};

const WEEKDAY_OPTIONS = [
  { value: "0", label: "Sunday" },
  { value: "1", label: "Monday" },
  { value: "2", label: "Tuesday" },
  { value: "3", label: "Wednesday" },
  { value: "4", label: "Thursday" },
  { value: "5", label: "Friday" },
  { value: "6", label: "Saturday" },
] as const;

function isClockValue(value?: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(String(value || ""));
}

function formatClockLabel(value?: string): string {
  if (!isClockValue(value)) return value || "--:--";
  const [hour, minute] = String(value).split(":").map((part) => Number(part));
  const stamp = new Date(2000, 0, 1, hour, minute);
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(stamp);
}

function changeHistoryLabel(item: RunChangeDetail): string {
  if (item.event_type === "removed") {
    if (item.first_seen && item.first_seen_known) return `First seen ${formatTime(item.first_seen)}`;
    if (item.first_seen) return `Seen since first saved run`;
    return "First seen unavailable";
  }
  if (item.first_seen && item.first_seen === item.observed_at) return "First seen in this run";
  if (item.first_seen) return `First seen ${formatTime(item.first_seen)}`;
  return "History unavailable";
}

function changeAvatarUrl(item: RunChangeDetail): string | null {
  return item.profile_pic_url_hd || item.profile_pic_url || item.avatar_url || null;
}

function accountStatusLabel(item: RunChangeDetail): string {
  if (item.event_type !== "removed") return "Present in this run";
  switch (item.account_status) {
    case "active":
      return "Account active; true list removal";
    case "not_found":
      return "Account unavailable";
    case "check_failed_auth":
      return "Status check needs login";
    case "check_failed_rate_limited":
      return "Status check rate limited";
    case "check_failed":
      return "Status check failed";
    default:
      return "Account status not checked";
  }
}

function parseLegacyCronSchedule(interval?: string): Partial<{
  preset: LaunchSchedulePreset;
  time1: string;
  time2: string;
  weekday: string;
  intervalDays: string;
}> | null {
  const cron = String(interval || "").trim();
  if (!cron) return null;
  const parts = cron.split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, dayOfMonth, , dayOfWeek] = parts;
  if (dayOfMonth === "*/2" && !minute.includes(",") && !hour.includes(",")) {
    return { preset: "every_n_days", time1: `${hour}:${minute}`, intervalDays: "2" };
  }
  if (dayOfWeek !== "*" && dayOfMonth === "*" && !minute.includes(",") && !hour.includes(",")) {
    return { preset: "weekly", time1: `${hour}:${minute}`, weekday: dayOfWeek };
  }
  if (minute.includes(",") && hour.includes(",")) {
    const minuteParts = minute.split(",");
    const hourParts = hour.split(",");
    if (minuteParts.length === 2 && hourParts.length === 2) {
      return {
        preset: "twice_daily",
        time1: `${hourParts[0]}:${minuteParts[0]}`,
        time2: `${hourParts[1]}:${minuteParts[1]}`,
      };
    }
  }
  if (dayOfMonth === "*" && dayOfWeek === "*" && !minute.includes(",") && !hour.includes(",")) {
    return { preset: "once_daily", time1: `${hour}:${minute}` };
  }
  return null;
}

function scheduleDefaultsFromItem(
  schedule?:
    | {
        interval?: string;
        schedule_kind?: "cron" | "daily" | "weekly" | "every_n_days";
        schedule_time?: string | null;
        schedule_weekday?: number | null;
        schedule_interval_days?: number | null;
      }
    | null
): {
  preset: LaunchSchedulePreset;
  time1: string;
  time2: string;
  weekday: string;
  intervalDays: string;
} {
  const fallback = {
    preset: "once_daily" as LaunchSchedulePreset,
    time1: "09:00",
    time2: "21:00",
    weekday: "1",
    intervalDays: "2",
  };
  if (!schedule) return fallback;
  if (schedule.schedule_kind === "daily") {
    return {
      ...fallback,
      preset: "once_daily",
      time1: isClockValue(schedule.schedule_time || "") ? String(schedule.schedule_time) : fallback.time1,
    };
  }
  if (schedule.schedule_kind === "weekly") {
    return {
      ...fallback,
      preset: "weekly",
      time1: isClockValue(schedule.schedule_time || "") ? String(schedule.schedule_time) : fallback.time1,
      weekday:
        schedule.schedule_weekday != null && schedule.schedule_weekday >= 0 && schedule.schedule_weekday <= 6
          ? String(schedule.schedule_weekday)
          : fallback.weekday,
    };
  }
  if (schedule.schedule_kind === "every_n_days") {
    return {
      ...fallback,
      preset: "every_n_days",
      time1: isClockValue(schedule.schedule_time || "") ? String(schedule.schedule_time) : fallback.time1,
      intervalDays: String(Math.max(1, Number(schedule.schedule_interval_days || 2) || 2)),
    };
  }
  const parsedCron = parseLegacyCronSchedule(schedule.interval);
  return parsedCron ? { ...fallback, ...parsedCron } : fallback;
}

function buildLaunchSchedulePlan(input: {
  preset: LaunchSchedulePreset;
  time1: string;
  time2: string;
  weekday: string;
  intervalDays: string;
}):
  | {
      payload: {
        interval?: string;
        schedule_kind?: "daily" | "weekly" | "every_n_days" | "cron";
        schedule_time?: string;
        schedule_weekday?: number;
        schedule_interval_days?: number;
      };
      summary: string;
    }
  | null {
  if (!isClockValue(input.time1)) return null;
  if (input.preset === "once_daily") {
    return {
      payload: { schedule_kind: "daily", schedule_time: input.time1 },
      summary: `Every day at ${formatClockLabel(input.time1)}`,
    };
  }
  if (input.preset === "twice_daily") {
    if (!isClockValue(input.time2)) return null;
    const [hour1, minute1] = input.time1.split(":");
    const [hour2, minute2] = input.time2.split(":");
    return {
      payload: {
        schedule_kind: "cron",
        interval: `${Number(minute1)},${Number(minute2)} ${Number(hour1)},${Number(hour2)} * * *`,
      },
      summary: `Twice daily at ${formatClockLabel(input.time1)} and ${formatClockLabel(input.time2)}`,
    };
  }
  if (input.preset === "weekly") {
    const weekday = Number(input.weekday);
    if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6) return null;
    return {
      payload: {
        schedule_kind: "weekly",
        schedule_time: input.time1,
        schedule_weekday: weekday,
      },
      summary: `Every ${WEEKDAY_OPTIONS.find((option) => option.value === String(weekday))?.label || "week"} at ${formatClockLabel(input.time1)}`,
    };
  }
  const intervalDays = Math.max(1, Number.parseInt(input.intervalDays, 10) || 1);
  return {
    payload: {
      schedule_kind: "every_n_days",
      schedule_time: input.time1,
      schedule_interval_days: intervalDays,
    },
    summary: `Every ${intervalDays} ${intervalDays === 1 ? "day" : "days"} at ${formatClockLabel(input.time1)}`,
  };
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

function collectorState(login: {
  private_session_exists?: boolean;
  has_password?: boolean;
  has_totp_seed?: boolean;
  session_marked_stale?: boolean;
  last_error?: string | null;
}) {
  if (login.private_session_exists && !login.session_marked_stale) {
    return { label: "Active session", tone: "good", detail: "ready for collection" };
  }
  if (login.private_session_exists && login.session_marked_stale) {
    return { label: "Stale session", tone: "info", detail: "refresh before heavy use" };
  }
  if (login.has_password) {
    return { label: "Needs refresh", tone: "neutral", detail: login.last_error || "password available" };
  }
  return { label: "Needs repair", tone: "bad", detail: login.last_error || "missing password or session" };
}

function scheduleSummary(schedule: {
  schedule_label?: string;
  mode?: string;
  target_username?: string;
  next_run?: string | null;
}) {
  const label = schedule.schedule_label || "Custom";
  const mode = schedule.mode === "count_watch" ? "count watch" : "full run";
  const nextRun = formatTime(schedule.next_run || undefined);
  return `${label} · ${mode} · next ${nextRun}`;
}

function sectionSummary(section: string, keys: string[]): string {
  switch (section) {
    case "Run":
      return "Collector family, collection method, pacing, and timeout controls.";
    case "Proxy":
      return "Proxy reachability and credential handling.";
    case "Schedule":
      return "Default cadence and timezone behavior.";
    case "Monitor":
      return "Health checks and watcher behavior.";
    case "Unfollow":
      return "Safety rails for unfollow batches.";
    default:
      return `${keys.length} additional runtime setting${keys.length === 1 ? "" : "s"}.`;
  }
}

function collectorFamilyLabel(value: string | undefined): string {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "browser") return "browser web session";
  if (normalized === "private" || normalized === "private_api") return "private API (instagrapi)";
  return normalized || "-";
}

function browserCollectionMethodLabel(value: string | undefined): string {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "browser_native") return "browser_native - live browser collection";
  if (normalized === "instaloader_session") return "instaloader_session - dedicated session";
  return normalized || "-";
}

function AppShell({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof window === "undefined") return "dark";
    return (window.localStorage.getItem("instalab-theme") as "dark" | "light") ?? "dark";
  });

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", theme);
    window.localStorage.setItem("instalab-theme", theme);
  }, [theme]);

  const navItems = [
    { to: "/", label: "Home", helper: "Overview", end: true },
    { to: "/targets", label: "Targets", helper: "Tracked profiles" },
    { to: "/activity", label: "Activity", helper: "Run history" },
    { to: "/operations", label: "Operations", helper: "Launch queue" },
    { to: "/accounts", label: "Accounts", helper: "Collectors" },
    { to: "/settings", label: "Settings", helper: "Runtime" },
  ];

  return (
    <div className="app-shell">
      <aside className="shell-header" aria-label="Primary navigation">
        <div className="shell-brand-block">
          <div className="brand">InstaLab</div>
          <div className="shell-kicker">Operations console</div>
        </div>
        <nav className="shell-nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              <span>{item.label}</span>
              <small>{item.helper}</small>
            </NavLink>
          ))}
        </nav>
        <button
          className="theme-toggle theme-toggle--rail"
          type="button"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          <span aria-hidden="true">{theme === "dark" ? "Light" : "Dark"}</span>
          <span className="sr-only">
            {theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          </span>
        </button>
      </aside>

      <main className="content">{children}</main>

      <nav className="mobile-nav">
        {navItems.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end}>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <button
        className="theme-toggle"
        type="button"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        <span aria-hidden="true">{theme === "dark" ? "☀" : "🌙"}</span>
        <span className="sr-only">
          {theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        </span>
      </button>
    </div>
  );
}

function CommandCenterPage() {
  const statusQ = useQuery({ queryKey: ["status"], queryFn: getAppStatus, refetchInterval: 10000 });
  const briefQ = useQuery({ queryKey: ["ui-brief"], queryFn: getUiBrief, refetchInterval: 12000 });

  const targetRows = (briefQ.data?.targets ?? []).slice(0, 6);
  const nextChecks = (briefQ.data?.next_checks ?? []).slice(0, 6);
  const attentionCollectors = (briefQ.data?.attention_collectors ?? []).slice(0, 6);
  const totalCollectors = targetRows.length || (briefQ.data?.attention_collectors?.length ?? 0);
  const healthyCollectors = Math.max(totalCollectors - attentionCollectors.length, 0);

  return (
    <section>
      <header className="page-header">
        <h1>Home</h1>
        <p>The product is target-first again. Use this page to orient quickly, then step into the active workspace.</p>
      </header>

      <div className="dossier-strip">
        <article className="dossier-cell">
          <span>Status</span>
          <strong>{statusQ.data?.status || statusQ.data?.state || "loading"}</strong>
        </article>
        <article className="dossier-cell">
          <span>Ready accounts</span>
          <strong>
            {healthyCollectors}/{totalCollectors}
          </strong>
        </article>
        <article className="dossier-cell">
          <span>Jobs</span>
          <strong>
            {(briefQ.data?.active_jobs?.length ?? 0) + (briefQ.data?.queued_jobs?.length ?? 0)}
          </strong>
        </article>
        <article className="dossier-cell">
          <span>Open actions</span>
          <strong>{briefQ.data?.attention_collectors?.length ?? 0}</strong>
        </article>
      </div>

      <div className="action-strip">
        <NavLink to="/targets" className="action-link">
          <span>Main workspace</span>
          <strong>Open targets</strong>
          <em>Launch runs, adjust schedules, and inspect the active watchlist.</em>
        </NavLink>
        <NavLink to="/accounts" className="action-link">
          <span>Collector readiness</span>
          <strong>Open accounts</strong>
          <em>Repair sessions, add logins, or start account creation.</em>
        </NavLink>
        <NavLink to="/operations" className="action-link">
          <span>Recovery queue</span>
          <strong>Open operations</strong>
          <em>Resolve manual actions, watch live jobs, and prune schedules.</em>
        </NavLink>
      </div>

      <div className="brief-grid">
        <article className="card panel-flat">
            <div className="panel-head">
              <h3>Targets</h3>
            <NavLink to="/targets" className="text-link">
              Open targets
            </NavLink>
          </div>
          <div className="ledger-list">
            {targetRows.map((target, idx) => (
              <div className="ledger-row" key={`${target.target_username || "target"}-${idx}`}>
                <div>
                  <div className="ledger-title">@{target.target_username || "-"}</div>
                  <div className="ledger-meta">last change {formatTime(target.last_change_at || undefined)}</div>
                </div>
                <div className="ledger-side">
                  {target.followers_count ?? "-"} / {target.following_count ?? "-"}
                </div>
              </div>
            ))}
            {!targetRows.length ? <p className="hint">No tracked targets.</p> : null}
          </div>
        </article>

        <div className="brief-side-stack">
          <article className="card panel-flat">
            <div className="panel-head">
              <h3>Needs review</h3>
              <NavLink to="/accounts" className="text-link">
                Open accounts
              </NavLink>
            </div>
            <div className="stack-list">
              {attentionCollectors.map((item, idx) => (
                <div className="stack-row" key={`${String(item.login_username || "manual")}-${idx}`}>
                  <div>
                    <div className="ledger-title">@{String(item.login_username || "-")}</div>
                    <div className="ledger-meta">{String(item.last_error || item.auth_last_event || "-")}</div>
                  </div>
                  <span>{String(item.status || "attention")}</span>
                </div>
              ))}
              {!attentionCollectors.length ? (
                <div className="stack-row">
                  <div>
                    <div className="ledger-title">No open actions</div>
                    <div className="ledger-meta">nothing needs attention</div>
                  </div>
                  <span>{briefQ.data?.state || "idle"}</span>
                </div>
              ) : null}
            </div>
          </article>

          <article className="card panel-flat">
            <div className="panel-head">
              <h3>Upcoming checks</h3>
              <NavLink to="/targets" className="text-link">
                Open targets
              </NavLink>
            </div>
            <div className="stack-list">
              {nextChecks.map((item, idx) => (
                <div className="stack-row" key={`${item.target_username || "schedule"}-${idx}`}>
                  <div>
                    <div className="ledger-title">@{item.target_username || "-"}</div>
                    <div className="ledger-meta">last full read {formatTime(item.last_full_run_at || undefined)}</div>
                  </div>
                  <span>{formatTime(item.next_check_at || undefined)}</span>
                </div>
              ))}
              {!nextChecks.length ? <p className="hint">No checks scheduled.</p> : null}
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}

function TargetsPage({ initialView = "overview" }: { initialView?: "overview" | "people" }) {
  const qc = useQueryClient();
  const targetsQ = useQuery({ queryKey: ["ui-targets"], queryFn: getUiTargets, refetchInterval: 30000 });
  const loginsQ = useQuery({ queryKey: ["logins"], queryFn: getLogins, refetchInterval: 20000 });
  const schedulesQ = useQuery({ queryKey: ["schedules"], queryFn: getSchedules, refetchInterval: 20000 });
  const runStatusQ = useQuery({ queryKey: ["run-status"], queryFn: getRunStatus, refetchInterval: 6000 });
  const [selectedTarget, setSelectedTarget] = useState("");
  const [viewMode, setViewMode] = useState<"overview" | "people">(initialView);
  const [stateFilter, setStateFilter] = useState<"" | "mutual" | "they_follow" | "subject_follows" | "disconnected">(
    ""
  );
  const [search, setSearch] = useState("");
  const [selectedUsername, setSelectedUsername] = useState("");
  const [launchLogin, setLaunchLogin] = useState("");
  const [launchJobId, setLaunchJobId] = useState("");
  const [launchSchedulePreset, setLaunchSchedulePreset] = useState<LaunchSchedulePreset>("once_daily");
  const [launchScheduleTime1, setLaunchScheduleTime1] = useState("09:00");
  const [launchScheduleTime2, setLaunchScheduleTime2] = useState("21:00");
  const [launchScheduleWeekday, setLaunchScheduleWeekday] = useState("1");
  const [launchScheduleIntervalDays, setLaunchScheduleIntervalDays] = useState("2");
  const [verificationCode, setVerificationCode] = useState("");
  const [showCodeModal, setShowCodeModal] = useState(false);
  const [showAddTargetModal, setShowAddTargetModal] = useState(false);
  const [draftTargetUsername, setDraftTargetUsername] = useState("");
  const [showLaunchPanel, setShowLaunchPanel] = useState(false);
  const [launchMode, setLaunchMode] = useState<"manual" | "schedule">("manual");

  useEffect(() => {
    setViewMode(initialView);
  }, [initialView]);

  const timelineQ = useQuery({
    queryKey: ["ui-target-timeline", selectedTarget, 10],
    queryFn: () => getUiTargetTimeline(selectedTarget, 10),
    enabled: Boolean(selectedTarget),
    refetchInterval: 30000,
  });

  const selectedSummary =
    (targetsQ.data ?? []).find((target) => usernameKey(target.target_username) === usernameKey(selectedTarget)) || null;
  const hasTrackedTarget = Boolean(selectedSummary);
  const hasTargetContext = Boolean(selectedTarget);
  const timeline = timelineQ.data ?? [];
  const latestBatch = timeline[0] ?? null;
  const matchingSchedules = (schedulesQ.data ?? []).filter(
    (schedule) => usernameKey(schedule.target_username) === usernameKey(selectedTarget)
  );
  const preferredLogin = useMemo(() => {
    return (
      matchingSchedules[0]?.login_username ||
      latestBatch?.login_username ||
      loginsQ.data?.[0]?.login_username ||
      ""
    );
  }, [latestBatch?.login_username, loginsQ.data, matchingSchedules]);

  useEffect(() => {
    const validLogins = (loginsQ.data ?? []).map((login) => String(login.login_username || ""));
    if (!validLogins.length) {
      setLaunchLogin("");
      return;
    }
    if (!launchLogin || !validLogins.includes(launchLogin)) {
      setLaunchLogin(String(preferredLogin || validLogins[0] || ""));
    }
  }, [launchLogin, loginsQ.data, preferredLogin, selectedTarget]);

  const primarySchedule = matchingSchedules[0] ?? null;

  useEffect(() => {
    const nextDefaults = scheduleDefaultsFromItem(primarySchedule);
    setLaunchSchedulePreset(nextDefaults.preset);
    setLaunchScheduleTime1(nextDefaults.time1);
    setLaunchScheduleTime2(nextDefaults.time2);
    setLaunchScheduleWeekday(nextDefaults.weekday);
    setLaunchScheduleIntervalDays(nextDefaults.intervalDays);
  }, [
    selectedTarget,
    primarySchedule?.id,
    primarySchedule?.interval,
    primarySchedule?.schedule_kind,
    primarySchedule?.schedule_time,
    primarySchedule?.schedule_weekday,
    primarySchedule?.schedule_interval_days,
  ]);

  const changesQ = useQuery({
    queryKey: ["ui-target-changes", selectedTarget, 20],
    queryFn: () => getUiTargetChanges(selectedTarget, 20),
    enabled: Boolean(selectedTarget),
    refetchInterval: 30000,
  });
  const peopleQ = useQuery({
    queryKey: ["ui-network", selectedTarget, stateFilter, search],
    queryFn: () =>
      getUiNetwork({
        target: selectedTarget,
        state: stateFilter,
        q: search.trim() || undefined,
        limit: 300,
      }),
    enabled: Boolean(selectedTarget),
    refetchInterval: 30000,
  });

  useEffect(() => {
    const firstUsername = peopleQ.data?.[0]?.username;
    if (!selectedUsername && firstUsername) {
      setSelectedUsername(String(firstUsername));
    }
    if (selectedUsername && !(peopleQ.data ?? []).some((item) => item.username === selectedUsername)) {
      setSelectedUsername(String(firstUsername || ""));
    }
  }, [peopleQ.data, selectedUsername]);

  const actorEventsQ = useQuery({
    queryKey: ["relationship-events", selectedTarget, selectedUsername, "targets-people"],
    queryFn: () =>
      getRelationshipEvents(selectedTarget, {
        username: selectedUsername,
        limit: 30,
      }),
    enabled: Boolean(selectedTarget && selectedUsername && viewMode === "people"),
    refetchInterval: 30000,
  });
  const runNowMutation = useMutation({
    mutationFn: startRun,
    onSuccess: async (data) => {
      setLaunchJobId(data.job_id);
      setVerificationCode("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["run-status"] }),
        qc.invalidateQueries({ queryKey: ["ui-targets"] }),
        qc.invalidateQueries({ queryKey: ["ui-target-timeline", selectedTarget] }),
        qc.invalidateQueries({ queryKey: ["ui-target-changes", selectedTarget] }),
      ]);
    },
  });
  const createScheduleMutation = useMutation({
    mutationFn: createSchedule,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["schedules"] }),
        qc.invalidateQueries({ queryKey: ["ui-targets"] }),
      ]);
    },
  });
  const cancelRunMutation = useMutation({
    mutationFn: cancelRun,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["run-status"] });
    },
  });
  const submitChallengeMutation = useMutation({
    mutationFn: ({ login, code }: { login: string; code: string }) => submitChallengeCode(login, code),
    onSuccess: () => {
      setVerificationCode("");
    },
  });
  const runJobQ = useQuery({
    queryKey: ["run-job-status", launchJobId],
    queryFn: () => getRunJobStatus(launchJobId),
    enabled: Boolean(launchJobId),
    refetchInterval: 3000,
  });
  const runJobSummaryQ = useQuery({
    queryKey: ["run-job-summary", launchJobId],
    queryFn: () => getRunJobSummary(launchJobId),
    enabled: Boolean(launchJobId),
    refetchInterval: 4000,
  });

  const peopleRows = peopleQ.data ?? [];
  const selectedActor = peopleRows.find((item) => item.username === selectedUsername) ?? peopleRows[0] ?? null;
  const launchSchedulePlan = useMemo(
    () =>
      buildLaunchSchedulePlan({
        preset: launchSchedulePreset,
        time1: launchScheduleTime1,
        time2: launchScheduleTime2,
        weekday: launchScheduleWeekday,
        intervalDays: launchScheduleIntervalDays,
      }),
    [launchScheduleIntervalDays, launchSchedulePreset, launchScheduleTime1, launchScheduleTime2, launchScheduleWeekday]
  );
  const peopleCounts = peopleRows.reduce(
    (acc, row) => {
      const key = row.relationship_state || "disconnected";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const stateTone = (state?: string) => {
    if (state === "mutual") return "good";
    if (state === "they_follow") return "info";
    if (state === "subject_follows") return "neutral";
    return "bad";
  };
  const eventSentence = (event?: {
    relation_type?: string;
    event_type?: string;
    username?: string;
    target_username?: string;
    observed_at?: string;
  }) => {
    if (!event?.username) return "No recorded activity yet.";
    const actor = `@${event.username}`;
    const subject = `@${event.target_username || selectedTarget || "-"}`;
    if (event.relation_type === "followers" && event.event_type === "added") return `${actor} started following ${subject}`;
    if (event.relation_type === "followers" && event.event_type === "removed") return `${actor} unfollowed ${subject}`;
    if (event.relation_type === "following" && event.event_type === "added") return `${subject} started following ${actor}`;
    if (event.relation_type === "following" && event.event_type === "removed") return `${subject} unfollowed ${actor}`;
    return `${actor} had a recorded change`;
  };
  const describeConnectionSpan = (item?: { first_seen_at?: string | null; departed_at?: string | null; observed_at?: string | null }) => {
    if (!item?.first_seen_at) return "";
    if (item.departed_at) return `Connection span ${formatDateSpan(item.first_seen_at, item.departed_at)}`;
    return `Connected ${formatDateSpan(item.first_seen_at, undefined)}`;
  };
  const activeTargetJobs = [...(runStatusQ.data?.active_jobs ?? []), ...(runStatusQ.data?.queued_jobs ?? [])].filter((job) => {
    const record = job as Record<string, unknown>;
    const meta =
      typeof record.meta === "object" && record.meta ? (record.meta as Record<string, unknown>) : ({} as Record<string, unknown>);
    return usernameKey(String(record.target_username || meta.target_username || "")) === usernameKey(selectedTarget);
  });
  const launchCooldown = (runStatusQ.data?.cooldowns ?? []).find(
    (cooldown) => usernameKey(String(cooldown.login_username || "")) === usernameKey(launchLogin)
  );
  const runPayload = runJobQ.data?.payload;
  const runMeta = runJobQ.data?.meta;
  const runDone = Boolean(runJobQ.data?.done);
  const runErrorCode = String(runPayload?.error_code || "").toLowerCase();
  const promptHint = (runJobSummaryQ.data?.last_worker_message || "").toLowerCase().includes("waiting for code");
  const interactiveChallenge =
    runErrorCode === "two_factor_required" ||
    runErrorCode === "challenge_required" ||
    (String(runMeta?.state || "").toLowerCase() === "running" && promptHint);
  const runNeedsCode = !runDone && interactiveChallenge;

  useEffect(() => {
    setShowCodeModal(runNeedsCode);
    if (!runNeedsCode) setVerificationCode("");
  }, [runNeedsCode]);

  const summaryMetrics = (
    <div className="summary-lineup targets-kpis">
      <div className="summary-pill">
        <span>Followers</span>
        <strong>{selectedSummary?.followers_count ?? "-"}</strong>
      </div>
      <div className="summary-pill">
        <span>Following</span>
        <strong>{selectedSummary?.following_count ?? "-"}</strong>
      </div>
      <div className="summary-pill">
        <span>Latest update</span>
        <strong>
          {latestBatch
            ? `${latestBatch.event_count ?? 0} change${latestBatch.event_count === 1 ? "" : "s"}`
            : "No changes yet"}
        </strong>
      </div>
      <div className="summary-pill">
        <span>Freshness</span>
        <strong>{formatRelativeAge(selectedSummary?.last_full_run_at || undefined)}</strong>
      </div>
    </div>
  );

  const overviewBlocks = (
    <>
      <div className="split-grid">
        <section className="card panel-flat">
          <div className="panel-head">
            <h3>Current counts</h3>
          </div>
          <div className="targets-history-summary">
            <div className="targets-history-row">
              <span>Followers</span>
              <strong>{selectedSummary?.followers_count ?? "-"}</strong>
            </div>
            <div className="targets-history-row">
              <span>Following</span>
              <strong>{selectedSummary?.following_count ?? "-"}</strong>
            </div>
            <div className="targets-history-row">
              <span>Last follower change</span>
              <strong>
                {latestBatch ? `${latestBatch.followers_added_count || 0} in / ${latestBatch.followers_removed_count || 0} out` : "-"}
              </strong>
            </div>
            <div className="targets-history-row">
              <span>Last following change</span>
              <strong>
                {latestBatch ? `${latestBatch.following_added_count || 0} in / ${latestBatch.following_removed_count || 0} out` : "-"}
              </strong>
            </div>
          </div>
        </section>
        <section className="card panel-flat">
          <div className="panel-head">
            <h3>What changed</h3>
            <NavLink to="/operations" className="text-link">
              Queue details
            </NavLink>
          </div>
          <div className="targets-attention-box">
            <strong>
              {latestBatch?.event_count
                ? `${latestBatch.event_count} recorded changes in the latest update`
                : "Quiet right now"}
            </strong>
            <span>
              {selectedSummary?.latest_event?.sentence
                ? `Latest recorded change: ${selectedSummary.latest_event.sentence}`
                : "No recorded activity yet for this target."}
            </span>
          </div>
        </section>
      </div>
      <div className="split-grid">
        <section className="card panel-flat">
          <div className="panel-head">
            <h3>History</h3>
            <span className="count-chip">{timeline.length}</span>
          </div>
          <div className="sample-ledger">
            {timeline.slice(0, 6).map((sample, idx) => (
              <div className="sample-ledger-row" key={`${sample.run_id || "sample"}-${idx}`}>
                <div>
                  <div className="ledger-title">{formatTime(sample.observed_at || undefined)}</div>
                  <div className="ledger-meta">
                    {sample.event_count ?? 0} change{sample.event_count === 1 ? "" : "s"}
                  </div>
                </div>
                <div className="ledger-side">
                  {formatTime(sample.observed_at || undefined)}
                </div>
              </div>
            ))}
            {!timeline.length ? <p className="hint">No history recorded yet.</p> : null}
          </div>
        </section>
        <section className="card panel-flat">
          <div className="panel-head">
            <h3>Latest changes</h3>
            <NavLink to="/activity" className="text-link">
              Open activity
            </NavLink>
          </div>
          <div className="ledger-list">
            {(changesQ.data ?? []).slice(0, 6).map((event, idx) => (
              <div className="ledger-row" key={`${event.id || "event"}-${idx}`}>
                <div>
                  <div className="ledger-title">{event.sentence || "Recorded change"}</div>
                  <div className="ledger-meta">{formatTime(event.observed_at || undefined)}</div>
                  {describeConnectionSpan(event) ? (
                    <div className="ledger-meta">{describeConnectionSpan(event)}</div>
                  ) : null}
                </div>
                <span className={`pill ${event.event_type === "removed" ? "bad" : "good"}`}>
                  {event.event_type || "change"}
                </span>
              </div>
            ))}
            {!changesQ.data?.length ? <p className="hint">No recent changes for this target.</p> : null}
          </div>
        </section>
      </div>
    </>
  );

  const onCreateTargetDraft = () => {
    const nextTarget = normalizeUsername(draftTargetUsername);
    if (!nextTarget) return;
    setSelectedTarget(nextTarget);
    setViewMode("overview");
    setShowLaunchPanel(true);
    setLaunchMode("manual");
    setDraftTargetUsername("");
    setShowAddTargetModal(false);
  };

  const onStartTargetRun = () => {
    const nextTarget = normalizeUsername(selectedTarget);
    if (!launchLogin.trim() || !nextTarget) return;
    setSelectedTarget(nextTarget);
    runNowMutation.mutate({
      login_username: launchLogin.trim(),
      target_username: nextTarget,
    });
  };

  const onCreateTargetSchedule = () => {
    const nextTarget = normalizeUsername(selectedTarget);
    if (!launchLogin.trim() || !nextTarget || !launchSchedulePlan) return;
    setSelectedTarget(nextTarget);
    createScheduleMutation.mutate({
      login_username: launchLogin.trim(),
      target_username: nextTarget,
      ...launchSchedulePlan.payload,
    });
  };

  return (
    <section className="targets-shell">
      <header className="page-header">
        <h1>Targets</h1>
      </header>

      <div className="targets-layout">
        <aside className="card targets-sidebar">
          <div className="targets-sidebar-head">
            <div>
              <h3>Watchlist</h3>
              <p className="hint">Tracked targets and draft launch contexts.</p>
            </div>
            <div className="targets-sidebar-actions">
              <span className="count-chip">{targetsQ.data?.length ?? 0}</span>
              <button className="btn-secondary targets-add-button" onClick={() => setShowAddTargetModal(true)}>
                Add target
              </button>
            </div>
          </div>
          <div className="targets-sidebar-list">
            {(targetsQ.data ?? []).map((target, idx) => (
              <button
                key={`${target.target_username || "target"}-${idx}`}
                className={`targets-sidebar-row ${usernameKey(selectedTarget) === usernameKey(target.target_username) ? "active" : ""}`}
                onClick={() => {
                  setSelectedTarget(String(target.target_username || ""));
                  setShowLaunchPanel(false);
                  setLaunchMode("manual");
                }}
              >
                <div className="ledger-title">@{target.target_username || "-"}</div>
                <div className="ledger-meta">
                  {target.followers_count ?? "-"} followers · {target.following_count ?? "-"} following
                </div>
              </button>
            ))}
            {selectedTarget && !selectedSummary ? (
              <button className="targets-sidebar-row active targets-sidebar-row--draft" onClick={() => setViewMode("overview")}>
                <div className="ledger-title">@{selectedTarget}</div>
                <div className="ledger-meta">Draft target · launch or schedule first run</div>
              </button>
            ) : null}
          </div>
        </aside>

        <div className="targets-main">
          <article className="card targets-summary-card">
            <div className="targets-hero-grid">
              <div className="targets-summary-hero">
                <div className="targets-summary-head">
                  <div>
                    <div className="dossier-headline">
                      <div>
                        <h2>{selectedTarget ? `@${selectedTarget}` : "Choose a target"}</h2>
                      </div>
                      {selectedTarget ? (
                        <a
                          href={instagramProfileUrl(selectedTarget)}
                          className="text-link"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open profile
                        </a>
                      ) : null}
                    </div>
                    <p className="hint targets-summary-subtitle">
                      {!selectedTarget
                        ? "Pick a target from the watchlist or create a new one before opening target detail views."
                        : selectedSummary
                        ? "Inspect recent activity here. Open run controls only when you want to start or schedule collection."
                        : "This target is not on the watchlist yet. Start a run or add a schedule to begin tracking it."}
                    </p>
                  </div>
                  <div className="targets-summary-note">
                    <strong>{matchingSchedules.length ? `${matchingSchedules.length} schedule${matchingSchedules.length === 1 ? "" : "s"}` : "No schedules yet"}</strong>
                    <span>
                      {matchingSchedules[0]?.next_run
                        ? `Next check ${formatTime(matchingSchedules[0].next_run || undefined)}`
                        : "Open run controls when you want a one-off run or recurring checks."}
                    </span>
                  </div>
                </div>
                {hasTrackedTarget ? <div className="desktop-only">{summaryMetrics}</div> : null}
                {hasTrackedTarget ? (
                  <details className="mobile-collapsible mobile-only" open>
                    <summary>Key metrics</summary>
                    {summaryMetrics}
                  </details>
                ) : null}
              </div>
              <section className="card panel-flat target-launch-card">
                <div className="panel-head">
                  <div>
                    <h3>Run controls</h3>
                    <p className="hint">
                      Keep target detail focused on inspection. Open this only when you want to launch or schedule work.
                    </p>
                  </div>
                    <div className="row gap">
                      <button
                        className="btn-secondary"
                        type="button"
                        onClick={() => setShowLaunchPanel((value) => !value)}
                      >
                        {showLaunchPanel ? "Hide controls" : hasTargetContext ? "Open actions" : "New target / run"}
                      </button>
                      <NavLink to="/operations" className="text-link">
                        Advanced queue
                    </NavLink>
                  </div>
                </div>
                {showLaunchPanel || !hasTrackedTarget ? (
                  <>
                    <div className="system-tabs" style={{ marginBottom: "0.9rem" }}>
                      <button
                        className={`system-tab ${launchMode === "manual" ? "active" : ""}`}
                        type="button"
                        onClick={() => setLaunchMode("manual")}
                      >
                        Run now
                      </button>
                      <button
                        className={`system-tab ${launchMode === "schedule" ? "active" : ""}`}
                        type="button"
                        onClick={() => setLaunchMode("schedule")}
                      >
                        Schedule
                      </button>
                    </div>
                    {!hasTrackedTarget ? (
                      <label style={{ display: "block", marginBottom: "0.9rem" }}>
                        Target handle
                        <input
                          {...targetHandleInputProps}
                          value={selectedTarget}
                          onChange={(e) => setSelectedTarget(normalizeUsername(e.target.value))}
                          placeholder="e.g. davidjones.tv"
                        />
                      </label>
                    ) : (
                      <div className="target-launch-strip" style={{ marginBottom: "0.9rem" }}>
                        <div className="target-launch-stat">
                          <span>Target</span>
                          <strong>@{selectedTarget}</strong>
                        </div>
                        <div className="target-launch-stat">
                          <span>Last full run</span>
                          <strong>{formatTime(selectedSummary?.last_full_run_at || undefined)}</strong>
                        </div>
                      </div>
                    )}
                    <div className="target-launch-grid">
                      <label>
                        Collector login
                        <select value={launchLogin} onChange={(e) => setLaunchLogin(e.target.value)}>
                          <option value="">Select login</option>
                          {(loginsQ.data ?? []).map((login, idx) => (
                            <option key={`${login.login_username || "login"}-${idx}`} value={login.login_username || ""}>
                              {login.login_username || "-"}
                            </option>
                          ))}
                        </select>
                      </label>
                      {launchMode === "schedule" ? (
                        <>
                          <label>
                            Schedule
                            <select value={launchSchedulePreset} onChange={(e) => setLaunchSchedulePreset(e.target.value as LaunchSchedulePreset)}>
                              <option value="once_daily">Once daily</option>
                              <option value="twice_daily">Twice daily</option>
                              <option value="weekly">Weekly</option>
                              <option value="every_n_days">Every few days</option>
                            </select>
                          </label>
                          <label>
                            {launchSchedulePreset === "twice_daily" ? "First run" : "Run time"}
                            <input
                              type="time"
                              value={launchScheduleTime1}
                              onChange={(e) => setLaunchScheduleTime1(e.target.value)}
                            />
                          </label>
                          {launchSchedulePreset === "twice_daily" ? (
                            <label>
                              Second run
                              <input type="time" value={launchScheduleTime2} onChange={(e) => setLaunchScheduleTime2(e.target.value)} />
                            </label>
                          ) : null}
                          {launchSchedulePreset === "weekly" ? (
                            <label>
                              Day
                              <select value={launchScheduleWeekday} onChange={(e) => setLaunchScheduleWeekday(e.target.value)}>
                                {WEEKDAY_OPTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </select>
                            </label>
                          ) : null}
                          {launchSchedulePreset === "every_n_days" ? (
                            <label>
                              Repeat every
                              <input
                                type="number"
                                min={1}
                                step={1}
                                value={launchScheduleIntervalDays}
                                onChange={(e) => setLaunchScheduleIntervalDays(e.target.value)}
                              />
                            </label>
                          ) : null}
                        </>
                      ) : null}
                    </div>
                    <p className="hint target-launch-plan">
                      {launchMode === "schedule"
                        ? launchSchedulePlan?.summary || "Choose a valid time to create a schedule."
                        : "Manual runs use the selected collector immediately and do not change the recurring schedule."}
                    </p>
                    {!loginsQ.data?.length ? (
                      <div className="target-launch-help">
                        <p className="hint">Add or refresh a collector account before starting runs from this target.</p>
                        <NavLink to="/accounts" className="text-link">
                          Open accounts
                        </NavLink>
                      </div>
                    ) : null}
                    <div className="target-launch-strip">
                      <div className="target-launch-stat">
                        <span>Schedules</span>
                        <strong>{matchingSchedules.length}</strong>
                      </div>
                      <div className="target-launch-stat">
                        <span>Queue state</span>
                        <strong>{activeTargetJobs.length ? `${activeTargetJobs.length} active` : "Idle"}</strong>
                      </div>
                      <div className="target-launch-stat">
                        <span>Collector</span>
                        <strong>{launchLogin ? `@${launchLogin}` : "Select login"}</strong>
                      </div>
                      <div className="target-launch-stat">
                        <span>Cooldown</span>
                        <strong>{launchCooldown?.cooldown_seconds ? `${launchCooldown.cooldown_seconds}s` : "Ready"}</strong>
                      </div>
                    </div>
                    <div className="row gap">
                      {launchMode === "manual" ? (
                        <button onClick={onStartTargetRun} disabled={runNowMutation.isPending || !selectedTarget || !launchLogin}>
                          {runNowMutation.isPending ? "Queueing..." : selectedSummary ? "Start manual run" : "Start first run"}
                        </button>
                      ) : (
                        <button
                          onClick={onCreateTargetSchedule}
                          disabled={createScheduleMutation.isPending || !selectedTarget || !launchLogin || !launchSchedulePlan}
                        >
                          {createScheduleMutation.isPending ? "Saving..." : matchingSchedules.length ? "Save schedule" : "Create schedule"}
                        </button>
                      )}
                      <button
                        className="btn-secondary"
                        onClick={() => cancelRunMutation.mutate({ job_id: launchJobId })}
                        disabled={cancelRunMutation.isPending || !launchJobId}
                      >
                        {cancelRunMutation.isPending ? "Cancelling..." : "Cancel job"}
                      </button>
                    </div>
                    {runNowMutation.error ? <p className="error">{(runNowMutation.error as Error).message}</p> : null}
                    {createScheduleMutation.error ? <p className="error">{(createScheduleMutation.error as Error).message}</p> : null}
                    {cancelRunMutation.error ? <p className="error">{(cancelRunMutation.error as Error).message}</p> : null}
                    {launchJobId ? (
                      <div className="target-launch-live">
                        <p className="hint">
                          Job {launchJobId} · {runDone ? (runPayload?.status || runMeta?.state || "done") : (runMeta?.state || "running")}
                          {runPayload?.error ? ` · ${runPayload.error}` : ""}
                          {" · "}
                          <NavLink to={`/jobs/${encodeURIComponent(launchJobId)}`} className="text-link">
                            clean status
                          </NavLink>
                        </p>
                        {runPayload?.result ? (
                          <p className="hint">
                            Result: followers {String(runPayload.result.followers_count ?? "-")} · following {String(runPayload.result.followees_count ?? "-")} · run_id {String(runPayload.result.run_id ?? "-")}
                          </p>
                        ) : null}
                        {submitChallengeMutation.error ? <p className="error">{(submitChallengeMutation.error as Error).message}</p> : null}
                        <div className="table-wrap">
                          <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                            {runJobSummaryQ.data?.last_worker_message || "(waiting for run logs)"}
                          </pre>
                        </div>
                      </div>
                    ) : null}
                    {launchMode === "schedule" && matchingSchedules.length ? (
                      <div className="target-schedule-list">
                        {matchingSchedules.map((schedule, idx) => (
                          <div className="ledger-row" key={`${schedule.id || "schedule"}-${idx}`}>
                            <div>
                              <div className="ledger-title">@{schedule.login_username || "-"}</div>
                              <div className="ledger-meta">{schedule.schedule_label || schedule.interval || "-"}</div>
                            </div>
                            <div className="ledger-side">{formatTime(schedule.next_run || undefined)}</div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="target-launch-strip">
                    <div className="target-launch-stat">
                      <span>Schedules</span>
                      <strong>{matchingSchedules.length}</strong>
                    </div>
                    <div className="target-launch-stat">
                      <span>Queue state</span>
                      <strong>{activeTargetJobs.length ? `${activeTargetJobs.length} active` : "Idle"}</strong>
                    </div>
                    <div className="target-launch-stat">
                      <span>Collector</span>
                      <strong>{launchLogin ? `@${launchLogin}` : "Select login"}</strong>
                    </div>
                    <div className="target-launch-stat">
                      <span>Cooldown</span>
                      <strong>{launchCooldown?.cooldown_seconds ? `${launchCooldown.cooldown_seconds}s` : "Ready"}</strong>
                    </div>
                  </div>
                )}
            </section>

            </div>

            {hasTrackedTarget ? (
              <div className="system-tabs">
                <button className={`system-tab ${viewMode === "overview" ? "active" : ""}`} onClick={() => setViewMode("overview")}>
                  Overview
                </button>
                <button className={`system-tab ${viewMode === "people" ? "active" : ""}`} onClick={() => setViewMode("people")}>
                  People
                </button>
              </div>
            ) : null}

            {!hasTrackedTarget ? (
              <section className="card panel-flat">
                <div className="panel-head">
                  <h3>Target detail</h3>
                </div>
                <p className="hint">
                  {!selectedTarget
                    ? "Select a target from the watchlist to open Overview and People."
                    : "This target is still a draft. Start the first run or create a schedule to turn it into tracked history."}
                </p>
              </section>
            ) : viewMode === "overview" ? (
              <>
                <div className="desktop-only">{overviewBlocks}</div>
                <details className="mobile-collapsible mobile-only" open>
                  <summary>Activity & history</summary>
                  {overviewBlocks}
                </details>
              </>
            ) : (
              <>
                <div className="dossier-strip">
                  <article className="dossier-cell">
                    <span>Mutual</span>
                    <strong>{peopleCounts.mutual || 0}</strong>
                  </article>
                  <article className="dossier-cell">
                    <span>They follow</span>
                    <strong>{peopleCounts.they_follow || 0}</strong>
                  </article>
                  <article className="dossier-cell">
                    <span>Target follows</span>
                    <strong>{peopleCounts.subject_follows || 0}</strong>
                  </article>
                  <article className="dossier-cell">
                    <span>Disconnected</span>
                    <strong>{peopleCounts.disconnected || 0}</strong>
                  </article>
                </div>

                <div className="split-grid split-grid-wide">
                  <section className="card panel-flat">
                    <div className="panel-head">
                      <h3>People</h3>
                      <span className="count-chip">{peopleRows.length}</span>
                    </div>
                    <p className="hint">
                      Showing up to 300 matching people from the current network view.
                    </p>
                    <div className="form-grid" style={{ marginBottom: "0.9rem" }}>
                      <label>
                        Relationship
                        <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value as typeof stateFilter)}>
                          <option value="">All people</option>
                          <option value="mutual">Mutual</option>
                          <option value="they_follow">They follow</option>
                          <option value="subject_follows">Target follows</option>
                          <option value="disconnected">Disconnected</option>
                        </select>
                      </label>
                      <label>
                        Search
                        <input
                          type="search"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                          placeholder="Find a username"
                        />
                      </label>
                    </div>
                    <div className="targets-sidebar-list">
                      {peopleQ.error ? <p className="error">{(peopleQ.error as Error).message}</p> : null}
                      {peopleRows.map((item, idx) => (
                        <button
                          key={`${item.target_username || "target"}-${item.username || "actor"}-${idx}`}
                          className={`targets-sidebar-row ${selectedUsername === item.username ? "active" : ""}`}
                          onClick={() => setSelectedUsername(String(item.username || ""))}
                        >
                          <div className="ledger-title">@{item.username || "-"}</div>
                          <div className="ledger-meta">
                            {item.relationship_label || "-"} · {formatTime(item.latest_interaction_at || undefined)}
                          </div>
                        </button>
                      ))}
                      {!peopleRows.length ? <p className="hint">No matching people.</p> : null}
                    </div>
                  </section>

                  <section className="card panel-flat">
                    <div className="panel-head">
                      <h3>{selectedActor?.username ? `@${selectedActor.username}` : "Details"}</h3>
                      {selectedActor?.username ? (
                        <a
                          href={instagramProfileUrl(selectedActor.username)}
                          className="text-link"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open profile
                        </a>
                      ) : null}
                    </div>
                    <div className="summary-lineup targets-kpis">
                      <div className="summary-pill">
                        <span>Relationship</span>
                        <strong>{selectedActor?.relationship_label || "-"}</strong>
                      </div>
                      <div className="summary-pill">
                        <span>First seen</span>
                        <strong>{formatTime(selectedActor?.first_seen_at || undefined)}</strong>
                      </div>
                      <div className="summary-pill">
                        <span>Latest interaction</span>
                        <strong>{formatTime(selectedActor?.latest_interaction_at || undefined)}</strong>
                      </div>
                    </div>

                    <div className="split-grid">
                      <section className="card panel-flat">
                        <div className="panel-head">
                          <h3>Current relationship</h3>
                          <span className={`pill ${stateTone(selectedActor?.relationship_state)}`}>
                            {selectedActor?.relationship_label || "Unknown"}
                          </span>
                        </div>
                        <div className="targets-history-summary">
                          <div className="targets-history-row">
                            <span>They follow the target</span>
                            <strong>{selectedActor?.actor_follows_subject ? "Yes" : "No"}</strong>
                          </div>
                          <div className="targets-history-row">
                            <span>Target follows them</span>
                            <strong>{selectedActor?.subject_follows_actor ? "Yes" : "No"}</strong>
                          </div>
                          <div className="targets-history-row">
                            <span>First seen</span>
                            <strong>{formatTime(selectedActor?.first_seen_at || undefined)}</strong>
                          </div>
                          <div className="targets-history-row">
                            <span>Connection span</span>
                            <strong>{formatDateSpan(selectedActor?.first_seen_at, selectedActor?.departed_at)}</strong>
                          </div>
                          <div className="targets-history-row">
                            <span>Departed</span>
                            <strong>{formatTime(selectedActor?.departed_at || undefined)}</strong>
                          </div>
                        </div>
                      </section>

                      <section className="card panel-flat">
                        <div className="panel-head">
                          <h3>Latest activity</h3>
                          <NavLink to="/activity" className="text-link">
                            Open activity
                          </NavLink>
                        </div>
                        <div className="targets-attention-box">
                          <strong>{eventSentence(selectedActor?.latest_event || undefined)}</strong>
                          <span>{formatTime(selectedActor?.latest_event?.observed_at || undefined)}</span>
                        </div>
                      </section>
                    </div>

                    <section className="card panel-flat">
                      <div className="panel-head">
                        <h3>History</h3>
                        <span className="count-chip">{actorEventsQ.data?.length ?? 0}</span>
                      </div>
                      <div className="ledger-list">
                        {(actorEventsQ.data ?? []).map((event, idx) => (
                          <div className="ledger-row" key={`${event.id || "event"}-${idx}`}>
                            <div>
                              <div className="ledger-title">{eventSentence(event)}</div>
                              <div className="ledger-meta">{formatTime(event.observed_at || undefined)}</div>
                            </div>
                            <span className={`pill ${event.event_type === "removed" ? "bad" : "good"}`}>
                              {event.event_type || "change"}
                            </span>
                          </div>
                        ))}
                        {!actorEventsQ.data?.length ? <p className="hint">No recorded history for this person yet.</p> : null}
                      </div>
                    </section>
                  </section>
                </div>
              </>
            )}
          </article>
        </div>
      </div>
      {showAddTargetModal ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setShowAddTargetModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>Add Target</h3>
            <p className="hint">Create a target context first, then launch its first run or schedule from the target panel.</p>
            <input
              {...targetHandleInputProps}
              value={draftTargetUsername}
              onChange={(e) => setDraftTargetUsername(e.target.value)}
              placeholder="e.g. davidjones.tv"
              autoFocus
            />
            <div className="row gap">
              <button disabled={!normalizeUsername(draftTargetUsername)} onClick={onCreateTargetDraft}>
                Open target
              </button>
              <button className="btn-secondary" onClick={() => setShowAddTargetModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {runNeedsCode && showCodeModal ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setShowCodeModal(false)}>
          <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <h3>Verification Code Required</h3>
            <p className="hint">Run {launchJobId} is waiting for a 2FA or challenge code for @{launchLogin}.</p>
            <input
              placeholder="enter 6-digit code"
              value={verificationCode}
              onChange={(e) => setVerificationCode(e.target.value)}
              autoFocus
            />
            <div className="row gap">
              <button
                disabled={submitChallengeMutation.isPending || !launchLogin || !verificationCode.trim()}
                onClick={() => submitChallengeMutation.mutate({ login: launchLogin, code: verificationCode.trim() })}
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

function SystemPage() {
  const [tab, setTab] = useState<"collectors" | "queue" | "preferences">("collectors");
  const systemQ = useQuery({ queryKey: ["ui-system-health"], queryFn: getUiSystemHealth, refetchInterval: 10000 });
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: getConfig, refetchInterval: 30000 });

  const collectors = systemQ.data?.collectors ?? [];
  const schedules = systemQ.data?.schedules ?? [];
  const manualActions = systemQ.data?.manual_actions ?? [];
  const config = cfgQ.data?.config;

  return (
    <section>
      <header className="page-header">
        <h1>Settings</h1>
      </header>

      <div className="dossier-strip">
        <article className="dossier-cell">
          <span>Accounts</span>
          <strong>{collectors.length}</strong>
        </article>
        <article className="dossier-cell">
          <span>Ready sessions</span>
          <strong>{collectors.filter((login) => login.private_session_exists).length}</strong>
        </article>
        <article className="dossier-cell">
          <span>Jobs</span>
          <strong>{(systemQ.data?.active_jobs?.length ?? 0) + (systemQ.data?.queued_jobs?.length ?? 0)}</strong>
        </article>
        <article className="dossier-cell">
          <span>Schedules</span>
          <strong>{schedules.length}</strong>
        </article>
      </div>

      <div className="system-tabs">
        <button className={`system-tab ${tab === "collectors" ? "active" : ""}`} onClick={() => setTab("collectors")}>
          Accounts
        </button>
        <button className={`system-tab ${tab === "queue" ? "active" : ""}`} onClick={() => setTab("queue")}>
          Jobs
        </button>
        <button className={`system-tab ${tab === "preferences" ? "active" : ""}`} onClick={() => setTab("preferences")}>
          Defaults
        </button>
      </div>

      <div className="system-workspace">
        {tab === "collectors" ? (
          <article className="card">
            <div className="panel-head">
              <h3>Accounts</h3>
              <NavLink to="/accounts" className="text-link">
                Open accounts
              </NavLink>
            </div>
            <div className="collector-list">
              {collectors.map((login, idx) => {
                const state = collectorState(login);
                return (
                  <div className="collector-row" key={`${login.login_username || "collector"}-${idx}`}>
                    <div className="collector-main">
                      <div className="account-card-head">
                        <div className="ledger-title">@{login.login_username || "-"}</div>
                        <span className={`pill ${state.tone}`}>{state.label}</span>
                      </div>
                      <div className="collector-meta">
                        <span>{state.detail}</span>
                        <span>{login.has_totp_seed ? "TOTP stored" : "No TOTP seed"}</span>
                        <span>{login.two_factor_method ? `2FA ${login.two_factor_method}` : "2FA method unknown"}</span>
                        <span>Last auth {formatTime(login.auth_last_event_at || undefined)}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
              {!collectors.length ? <p className="hint">No collectors stored.</p> : null}
            </div>
          </article>
        ) : null}

        {tab === "queue" ? (
          <div className="settings-stack">
            <article className="card">
              <div className="panel-head">
                <h3>Queue state</h3>
                <NavLink to="/operations" className="text-link">
                  Open jobs
                </NavLink>
              </div>
              <div className="summary-lineup">
                <div className="summary-pill">
                  <span>Active</span>
                  <strong>{systemQ.data?.active_jobs?.length ?? 0}</strong>
                </div>
                <div className="summary-pill">
                  <span>Waiting</span>
                  <strong>{systemQ.data?.queued_jobs?.length ?? 0}</strong>
                </div>
                <div className="summary-pill">
                  <span>Open actions</span>
                  <strong>{manualActions.length}</strong>
                </div>
              </div>
            </article>

            <article className="card">
              <div className="panel-head">
                <h3>Open actions</h3>
                <span className="count-chip">{manualActions.length}</span>
              </div>
              <div className="ledger-list">
                {manualActions.slice(0, 8).map((item, idx) => (
                  <div className="ledger-row" key={`${String(item.action_id || "manual")}-${idx}`}>
                    <div>
                      <div className="ledger-title">
                        @{String(item.login_username || "-")} → @{String(item.target_username || "-")}
                      </div>
                      <div className="ledger-meta">{formatTime(String(item.updated_at || item.created_at || ""))}</div>
                    </div>
                    <div className="ledger-side">{String(item.error_code || item.reason || item.action_type || "action")}</div>
                  </div>
                ))}
                {!manualActions.length ? <p className="hint">No open actions.</p> : null}
              </div>
            </article>

            <article className="card">
              <div className="panel-head">
                <h3>Schedules</h3>
                <span className="count-chip">{schedules.length}</span>
              </div>
              <div className="ledger-list">
                {schedules.slice(0, 8).map((schedule, idx) => (
                  <div className="ledger-row" key={`${schedule.id || "schedule"}-${idx}`}>
                    <div>
                      <div className="ledger-title">@{schedule.target_username || "-"}</div>
                      <div className="ledger-meta">@{schedule.login_username || "-"}</div>
                    </div>
                    <div className="ledger-side">{scheduleSummary(schedule)}</div>
                  </div>
                ))}
                {!schedules.length ? <p className="hint">No schedules configured.</p> : null}
              </div>
            </article>
          </div>
        ) : null}

        {tab === "preferences" ? (
          <div className="settings-stack">
            <article className="card">
              <div className="panel-head">
                <h3>Collection defaults</h3>
                <NavLink to="/settings" className="text-link">
                  Open preferences
                </NavLink>
              </div>
              <div className="summary-lineup">
                <div className="summary-pill">
                  <span>Collector family</span>
                  <strong>{collectorFamilyLabel(config?.run_scraper_backend)}</strong>
                </div>
                <div className="summary-pill">
                  <span>Browser method</span>
                  <strong>{browserCollectionMethodLabel(config?.run_browser_collection_method)}</strong>
                </div>
                <div className="summary-pill">
                  <span>Login mode</span>
                  <strong>{config?.run_login_mode || "-"}</strong>
                </div>
                <div className="summary-pill">
                  <span>Proxy</span>
                  <strong>{config?.proxy_enabled ? "enabled" : "off"}</strong>
                </div>
              </div>
            </article>
            <article className="card">
              <div className="panel-head">
                <h3>Proxy</h3>
              </div>
              {config?.proxy_enabled ? (
                <div className="ledger-list">
                  <div className="ledger-row">
                    <div>
                      <div className="ledger-title">{config.proxy_host || "-"}</div>
                      <div className="ledger-meta">host</div>
                    </div>
                    <div className="ledger-side">{config.proxy_port ?? "-"}</div>
                  </div>
                  <div className="ledger-row">
                    <div>
                      <div className="ledger-title">{config.proxy_username || "-"}</div>
                      <div className="ledger-meta">username</div>
                    </div>
                    <div className="ledger-side">
                      <span className={`pill ${config.proxy_password_set ? "good" : "neutral"}`}>
                        {config.proxy_password_set ? "password stored" : "no password"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="hint">Proxy is disabled. Proxy settings stay hidden until enabled.</p>
              )}
            </article>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function NetworkPage() {
  return <TargetsPage initialView="people" />;
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
  const [activeChangeGroup, setActiveChangeGroup] = useState("followers-added");
  const [changeSearch, setChangeSearch] = useState("");

  useEffect(() => {
    if (!selectedTarget && (targetsQ.data?.length || 0) > 0) {
      setSelectedTarget(String(targetsQ.data?.[0]?.target_username || ""));
    }
  }, [selectedTarget, targetsQ.data]);

  const runsQ = useQuery({
    queryKey: ["runs", selectedTarget],
    queryFn: () => getRuns(selectedTarget, 60, "full"),
    enabled: Boolean(selectedTarget),
    refetchInterval: 15000,
  });

  useEffect(() => {
    const firstId = runsQ.data?.[0]?.id;
    const stillPresent = (runsQ.data ?? []).some((run) => run.id === selectedRunId);
    if (typeof firstId === "number" && (!selectedRunId || !stillPresent)) {
      setSelectedRunId(firstId);
      return;
    }
    if (!firstId) setSelectedRunId(null);
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
  const previousRun = selectedRun ? runs.find((run) => (run.id ?? 0) < (selectedRun.id ?? 0)) ?? null : null;
  const changeGroups = detail
    ? [
        {
          key: "followers-added",
          label: "Started following",
          description: "Accounts that newly follow the target.",
          items: detail.followers_added_list ?? [],
          details: ((detail.followers_added_details as RunChangeDetail[] | undefined) ?? []),
          relation: "followers",
          eventType: "added",
          tone: "good",
        },
        {
          key: "followers-removed",
          label: "Stopped following",
          description: "Accounts that no longer follow the target.",
          items: detail.followers_removed_list ?? [],
          details: ((detail.followers_removed_details as RunChangeDetail[] | undefined) ?? []),
          relation: "followers",
          eventType: "removed",
          tone: "bad",
        },
        {
          key: "following-added",
          label: "Target started following",
          description: "Accounts the target started following.",
          items: detail.followees_added_list ?? [],
          details: ((detail.followees_added_details as RunChangeDetail[] | undefined) ?? []),
          relation: "following",
          eventType: "added",
          tone: "info",
        },
        {
          key: "following-removed",
          label: "Target stopped following",
          description: "Accounts the target stopped following.",
          items: detail.followees_removed_list ?? [],
          details: ((detail.followees_removed_details as RunChangeDetail[] | undefined) ?? []),
          relation: "following",
          eventType: "removed",
          tone: "neutral",
        },
      ]
    : [];
  const activeGroup = changeGroups.find((group) => group.key === activeChangeGroup) ?? changeGroups[0] ?? null;
  const activeChangeRows: RunChangeDetail[] = activeGroup
    ? activeGroup.details.length
      ? activeGroup.details
      : activeGroup.items.map((username) => ({
          username,
          relation_type: activeGroup.relation,
          event_type: activeGroup.eventType,
          observed_at: detail?.timestamp,
        }))
    : [];
  const filteredChangeRows = activeChangeRows.filter((item) =>
    `${item.username || ""} ${item.full_name || ""}`.toLowerCase().includes(changeSearch.trim().toLowerCase())
  );

  return (
    <section className="explorer-shell">
      <header className="page-header">
        <h1>Activity</h1>
      </header>
      <div className="activity-dashboard">
        <section className="activity-controls">
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
          <label>
            Complete run
            <select
              value={selectedRunId ?? ""}
              onChange={(e) => setSelectedRunId(e.target.value ? Number(e.target.value) : null)}
              className="run-selector-input"
            >
              {runs.map((run, idx) => {
                const followerDelta = (run.followers_added ?? 0) - (run.followers_removed ?? 0);
                const followingDelta = (run.followees_added ?? 0) - (run.followees_removed ?? 0);
                return (
                  <option key={`${run.id || "run"}-${idx}`} value={run.id ?? ""}>
                    #{run.id ?? "-"} · {formatTime(run.timestamp || undefined)} · followers {followerDelta >= 0 ? "+" : ""}{followerDelta} · following {followingDelta >= 0 ? "+" : ""}{followingDelta}
                  </option>
                );
              })}
            </select>
          </label>
        </section>

        {!detail ? (
          <section className="activity-empty-state">
            <Users size={28} aria-hidden="true" />
            <h3>Select a complete run</h3>
            <p>Complete target runs will show follower and following additions/removals here.</p>
          </section>
        ) : (
          <>
            <section className="activity-hero">
              <div>
                <div className="activity-eyebrow">Complete run #{detail.id ?? selectedRunId ?? "-"}</div>
                <h2>@{detail.target_username || selectedTarget || "-"}</h2>
                <p>
                  Changed on {formatTime(detail.timestamp || undefined)} · compared with {previousRun ? `run #${previousRun.id}` : "the previous complete run"}.
                </p>
              </div>
              <div className="activity-actions">
                <button
                  className="btn-secondary icon-button-text"
                  onClick={() =>
                    downloadCsvRows(
                      `${targetSlug}-run-${detail.id || selectedRunId || "selected"}-changes.csv`,
                      ["change_group", "username", "changed_on", "first_seen", "first_seen_known", "account_status"],
                      changeGroups.flatMap((group) => {
                        const rows: RunChangeDetail[] = group.details.length
                          ? group.details
                          : group.items.map((username) => ({
                              username,
                              observed_at: detail.timestamp,
                              event_type: group.eventType,
                            }));
                        return rows.map((item) => [
                          group.label,
                          item.username || "",
                          item.observed_at || "",
                          item.first_seen || "",
                          item.first_seen_known ? "yes" : "no",
                          item.event_type === "removed" ? "not checked" : "active in snapshot",
                        ]);
                      })
                    )
                  }
                >
                  <Download size={16} aria-hidden="true" />
                  Export changes
                </button>
              </div>
            </section>

            <section className="activity-metrics">
              <div className="activity-metric">
                <span>Started following</span>
                <strong>{detail.followers_added ?? 0}</strong>
                <em>new followers</em>
              </div>
              <div className="activity-metric">
                <span>Stopped following</span>
                <strong>{detail.followers_removed ?? 0}</strong>
                <em>removed followers</em>
              </div>
              <div className="activity-metric">
                <span>Target started following</span>
                <strong>{detail.followees_added ?? 0}</strong>
                <em>new following</em>
              </div>
              <div className="activity-metric">
                <span>Target stopped following</span>
                <strong>{detail.followees_removed ?? 0}</strong>
                <em>removed following</em>
              </div>
            </section>

            <section className="activity-change-board">
              <div className="activity-change-tabs" role="tablist" aria-label="Run change groups">
                {changeGroups.map((group) => {
                  const Icon = group.key.includes("followers")
                    ? group.eventType === "added"
                      ? UserPlus
                      : UserMinus
                    : group.eventType === "added"
                      ? UserCheck
                      : ArrowRightLeft;
                  return (
                    <button
                      key={group.key}
                      className={`activity-change-tab ${activeGroup?.key === group.key ? "active" : ""}`}
                      onClick={() => {
                        setActiveChangeGroup(group.key);
                        setChangeSearch("");
                        setEventsScope("run");
                        setEventsRelation(group.relation as "followers" | "following");
                        setEventsType(group.eventType as "added" | "removed");
                      }}
                    >
                      <Icon size={18} aria-hidden="true" />
                      <span>{group.label}</span>
                      <strong>{group.items.length}</strong>
                    </button>
                  );
                })}
              </div>

              <div className="activity-review-panel">
                <div className="activity-review-head">
                  <div>
                    <h3>{activeGroup?.label || "Changes"}</h3>
                    <p>{activeGroup?.description || "Select a change group."}</p>
                  </div>
                  <button
                    className="btn-secondary icon-button-text"
                    disabled={!activeGroup}
                    onClick={() =>
                      activeGroup &&
                      downloadCsvRows(
                      `${targetSlug}-run-${detail.id || selectedRunId || "selected"}-${activeGroup.key}.csv`,
                        ["username", "full_name", "change", "changed_on", "first_seen", "first_seen_known", "account_status"],
                        activeChangeRows.map((item) => [
                          item.username || "",
                          item.full_name || "",
                          activeGroup.label,
                          item.observed_at || "",
                          item.first_seen || "",
                          item.first_seen_known ? "yes" : "no",
                          accountStatusLabel(item),
                        ])
                      )
                    }
                  >
                    <Download size={16} aria-hidden="true" />
                    Export list
                  </button>
                </div>
                <label className="activity-search">
                  <Search size={16} aria-hidden="true" />
                  <input value={changeSearch} onChange={(e) => setChangeSearch(e.target.value)} placeholder="Search usernames" />
                </label>
                <div className="activity-change-list">
                  {filteredChangeRows.slice(0, 240).map((item, idx) => {
                    const avatarUrl = changeAvatarUrl(item);
                    return (
                    <div key={`${activeGroup?.key || "change"}-${item.username || idx}-${idx}`} className="activity-change-row">
                      <div className={`activity-change-person${avatarUrl ? " has-avatar" : ""}`}>
                        {avatarUrl ? (
                          <span className="activity-avatar" aria-hidden="true">
                            <img src={avatarUrl} alt="" loading="lazy" referrerPolicy="no-referrer" />
                          </span>
                        ) : null}
                        <span className="activity-person-copy">
                        {item.username ? (
                          <a
                            className="activity-username"
                            href={instagramProfileUrl(item.username)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={`Open @${item.username} on Instagram`}
                          >
                            @{item.username}
                          </a>
                        ) : (
                          <span className="activity-username">-</span>
                        )}
                        {item.full_name ? <span className="activity-full-name">{item.full_name}</span> : null}
                        <span className="activity-change-context">{changeHistoryLabel(item)}</span>
                        </span>
                      </div>
                      <div className="activity-change-meta">
                        <span>Changed {formatTime(item.observed_at || detail.timestamp || undefined)}</span>
                        <span>{accountStatusLabel(item)}</span>
                      </div>
                    </div>
                    );
                  })}
                  {!filteredChangeRows.length ? <p className="hint">No usernames match this filter.</p> : null}
                </div>
                {filteredChangeRows.length > 240 ? (
                  <p className="hint run-detail-truncation">Showing first 240 of {filteredChangeRows.length} usernames.</p>
                ) : null}
              </div>
            </section>
          </>
        )}
        {deleteRunMutation.error ? <p className="error">{(deleteRunMutation.error as Error).message}</p> : null}
        {undoRunMutation.error ? <p className="error">{(undoRunMutation.error as Error).message}</p> : null}
      </div>
      <article className="card explorer-panel">
        <h3>Event log</h3>
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
  const manualActionsQ = useQuery({ queryKey: ["manual-actions"], queryFn: getManualActions, refetchInterval: 6000 });
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
  const runJobSummaryQ = useQuery({
    queryKey: ["run-job-summary", manualJobId],
    queryFn: () => getRunJobSummary(manualJobId),
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
  const resolveManualMutation = useMutation({
    mutationFn: ({ actionId, note }: { actionId: string; note?: string }) => resolveManualAction(actionId, note),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["manual-actions"] });
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
  const promptHint = (runJobSummaryQ.data?.last_worker_message || "").toLowerCase().includes("waiting for code");
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

  const activeJobs = runStatusQ.data?.active_jobs?.length ?? 0;
  const queuedJobs = runStatusQ.data?.queued_jobs?.length ?? 0;
  const liveJobs = runStatusQ.data?.active_jobs ?? [];
  const controllableJobs = [
    ...(runStatusQ.data?.active_jobs ?? []).map((job) => ({
      state: "running",
      job_id: String(job.job_id || ""),
      login_username: String(job.login_username || ""),
      target_username: String(job.target_username || ""),
      elapsed_seconds: job.elapsed_seconds,
    })),
    ...(runStatusQ.data?.queued_jobs ?? []).map((job) => ({
      state: "queued",
      job_id: String(job.job_id || ""),
      login_username: String(job.meta?.login_username || ""),
      target_username: String(job.meta?.target_username || ""),
      elapsed_seconds: undefined,
    })),
  ].filter((job) => job.job_id || (job.login_username && job.target_username));
  const manualActionCount = manualActionsQ.data?.actions?.length ?? 0;
  const totalSchedules = schedulesQ.data?.length ?? 0;

  return (
    <section>
      <header className="page-header">
        <h1>Operations</h1>
        <p>Queue state, recovery work, and operator-only controls. Primary launch lives in Targets.</p>
      </header>
      <div className="dossier-strip operations-kpis">
        <article className="dossier-cell">
          <span>Active jobs</span>
          <strong>{activeJobs}</strong>
        </article>
        <article className="dossier-cell">
          <span>Queued</span>
          <strong>{queuedJobs}</strong>
        </article>
        <article className="dossier-cell">
          <span>Manual actions</span>
          <strong>{manualActionCount}</strong>
        </article>
        <article className="dossier-cell">
          <span>Schedules</span>
          <strong>{totalSchedules}</strong>
        </article>
      </div>
      <article className="card panel-flat operations-callout">
        <div>
          <h3>Operator view</h3>
          <p className="hint">Use this page when a run is already in motion, blocked on verification, or needs manual cleanup.</p>
        </div>
        <NavLink to="/targets" className="text-link">
          Return to targets
        </NavLink>
      </article>
      <article className="card">
        <h3>Live Progress Telemetry</h3>
        <p className="hint">Page-level collection status for active runs.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Phase</th>
                <th>Followers</th>
                <th>Following</th>
                <th>Pages</th>
                <th>Last page</th>
                <th>Duplicates</th>
                <th>Retry</th>
                <th>Worker</th>
              </tr>
            </thead>
            <tbody>
              {liveJobs.map((job, idx) => {
                const detail = job.progress_detail || {};
                const phase = String(job.phase || detail.phase || "-");
                const lastPageAt =
                  typeof detail.last_page_at === "number"
                    ? detail.last_page_at
                    : phase === "followers"
                      ? job.followers_last_page_at
                      : job.following_last_page_at;
                const lastPageAge =
                  typeof lastPageAt === "number"
                    ? `${Math.max(0, Math.round(Date.now() / 1000 - lastPageAt))}s ago`
                    : "-";
                const pages =
                  phase === "followers"
                    ? job.followers_pages
                    : phase === "following"
                      ? job.following_pages
                      : detail.page_index;
                const duplicates =
                  phase === "followers"
                    ? job.followers_duplicates_total
                    : phase === "following"
                      ? job.following_duplicates_total
                      : detail.duplicates_total;
                const retryText = detail.retrying
                  ? `attempt ${String(detail.attempt || "-")} · missing ${String(detail.missing_count ?? "-")}`
                  : detail.alternate_endpoint
                    ? `gql added ${String(detail.alternate_added ?? "-")} · missing ${String(detail.missing_count ?? "-")}`
                  : detail.retry_complete
                    ? `added ${String(detail.retry_added ?? 0)} · missing ${String(detail.missing_count ?? "-")}`
                    : "-";
                return (
                  <tr key={`${job.job_id || job.target_username || "live"}-${idx}`}>
                    <td>
                      {job.job_id ? (
                        <NavLink to={`/jobs/${encodeURIComponent(String(job.job_id))}`} className="text-link">
                          {String(job.job_id).slice(0, 8)}
                        </NavLink>
                      ) : "-"}
                    </td>
                    <td>{phase}</td>
                    <td>{String(job.followers_progress ?? "-")} / {String(job.followers_total ?? "-")}</td>
                    <td>{String(job.following_progress ?? "-")} / {String(job.following_total ?? "-")}</td>
                    <td>{String(pages ?? "-")}</td>
                    <td>{lastPageAge}</td>
                    <td>{String(duplicates ?? "-")}</td>
                    <td>{retryText}</td>
                    <td>{String(job.worker_pid ?? "-")}</td>
                  </tr>
                );
              })}
              {!liveJobs.length ? (
                <tr><td colSpan={9} className="hint">No active run telemetry.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </article>
      <article className="card">
        <h3>Run Control</h3>
        <p className="hint">Cancel any active or queued collector job.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>State</th>
                <th>Login</th>
                <th>Target</th>
                <th>Job</th>
                <th>Elapsed</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {controllableJobs.map((job, idx) => (
                <tr key={`${job.job_id || job.login_username}-${idx}`}>
                  <td>{job.state}</td>
                  <td>{job.login_username || "-"}</td>
                  <td>{job.target_username || "-"}</td>
                  <td>{job.job_id || "-"}</td>
                  <td>{typeof job.elapsed_seconds === "number" ? `${job.elapsed_seconds}s` : "-"}</td>
                  <td>
                    <button
                      className="btn-secondary"
                      onClick={() =>
                        cancelRunMutation.mutate({
                          job_id: job.job_id || undefined,
                          login_username: job.login_username || undefined,
                          target_username: job.target_username || undefined,
                        })
                      }
                      disabled={cancelRunMutation.isPending}
                    >
                      {cancelRunMutation.isPending ? "Cancelling..." : "Cancel"}
                    </button>
                  </td>
                </tr>
              ))}
              {!controllableJobs.length ? (
                <tr><td colSpan={6} className="hint">No active or queued jobs.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {cancelRunMutation.error ? <p className="error">{(cancelRunMutation.error as Error).message}</p> : null}
      </article>
      <article className="card">
        <h3>Manual Action Queue</h3>
        <p className="hint">Items that require human action before runs can continue safely.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Login</th>
                <th>Target</th>
                <th>Type</th>
                <th>Reason</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {(manualActionsQ.data?.actions ?? []).map((item, idx) => (
                <tr key={`${String(item.action_id || "manual")}-${idx}`}>
                  <td>{formatTime(String(item.updated_at || item.created_at || ""))}</td>
                  <td>{String(item.login_username || "-")}</td>
                  <td>{String(item.target_username || "-")}</td>
                  <td>{String(item.action_type || "-")}</td>
                  <td>
                    {String(item.error_code || item.reason || "-")}
                    {item.error_message ? ` · ${String(item.error_message).slice(0, 90)}` : ""}
                  </td>
                  <td>
                    <button
                      className="btn-secondary"
                      disabled={resolveManualMutation.isPending || !item.action_id}
                      onClick={() => resolveManualMutation.mutate({ actionId: String(item.action_id), note: "resolved from operations ui" })}
                    >
                      Resolve
                    </button>
                  </td>
                </tr>
              ))}
              {!((manualActionsQ.data?.actions ?? []).length) ? (
                <tr><td colSpan={6} className="hint">No open manual actions.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {resolveManualMutation.error ? <p className="error">{(resolveManualMutation.error as Error).message}</p> : null}
      </article>
      <article className="card">
        <h3>Schedule Setup</h3>
        <details className="settings-detail">
          <summary>Override from operations</summary>
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
              Target handle
              <input
                {...targetHandleInputProps}
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
        </details>
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
                <th>Schedule</th>
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
                  <td>{sch.schedule_label || sch.interval || "-"}</td>
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
      <article className="card">
        <h3>Emergency Run Control</h3>
        <details className="settings-detail">
          <summary>Open manual launch controls</summary>
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
              Target handle
              <input
                {...targetHandleInputProps}
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
            Active {activeJobs} · Queued {queuedJobs}
          </p>
          {runStatusQ.data?.cooldowns?.length ? (
            <p className="hint">
              Cooldowns: {(runStatusQ.data.cooldowns ?? []).map((c) => `@${c.login_username} (${c.cooldown_seconds}s)`).join(" · ")}
            </p>
          ) : null}
          {manualJobId ? (
            <>
              <p className="hint">
                Live: {runDone ? (runPayload?.status || runMeta?.state || "done") : (runMeta?.state || "running")}
                {runPayload?.error ? ` · ${runPayload.error}` : ""}
                {manualJobId ? (
                  <>
                    {" · "}
                    <NavLink to={`/jobs/${encodeURIComponent(manualJobId)}`} className="text-link">
                      clean status
                    </NavLink>
                  </>
                ) : null}
              </p>
              {runPayload?.result ? (
                <p className="hint">
                  Result: followers {String(runPayload.result.followers_count ?? "-")} · following {String(runPayload.result.followees_count ?? "-")} · run_id {String(runPayload.result.run_id ?? "-")}
                </p>
              ) : null}
              {submitChallengeMutation.error ? <p className="error">{(submitChallengeMutation.error as Error).message}</p> : null}
              <div className="table-wrap">
                <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                  {runJobSummaryQ.data?.last_worker_message || "(waiting for run logs)"}
                </pre>
              </div>
            </>
          ) : null}
        </details>
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
  const [autoSetRunner, setAutoSetRunner] = useState(true);
  const [warmupTargetUsername, setWarmupTargetUsername] = useState("");
  const [queueWarmupRun, setQueueWarmupRun] = useState(false);
  const [scheduleInterval, setScheduleInterval] = useState("");
  const [selectedAuthLogin, setSelectedAuthLogin] = useState("");
  const [accountVerificationCode, setAccountVerificationCode] = useState("");
  const [challengeEmailHost, setChallengeEmailHost] = useState("imap.gmail.com");
  const [challengeEmailPort, setChallengeEmailPort] = useState("993");
  const [challengeEmailUseSsl, setChallengeEmailUseSsl] = useState(true);
  const [challengeEmailUsername, setChallengeEmailUsername] = useState("");
  const [challengeEmailPassword, setChallengeEmailPassword] = useState("");
  const [challengeEmailMailbox, setChallengeEmailMailbox] = useState("INBOX");
  const [activePanel, setActivePanel] = useState<"setup" | "factory" | "maintenance" | "diagnostics">("setup");
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
  const submitAccountChallengeMutation = useMutation({
    mutationFn: ({ login, code, retry }: { login: string; code: string; retry?: boolean }) =>
      submitChallengeCode(login, code, { retry_run: Boolean(retry) }),
    onSuccess: async () => {
      setAccountVerificationCode("");
      await qc.invalidateQueries({ queryKey: ["auth-trace", selectedAuthLogin] });
      await qc.invalidateQueries({ queryKey: ["logins"] });
      if (selectedAuthLogin) authPreflightMutation.mutate(selectedAuthLogin);
    },
  });
  const setChallengeEmailMutation = useMutation({
    mutationFn: (payload: Parameters<typeof setChallengeEmail>[0]) => setChallengeEmail(payload),
    onSuccess: async () => {
      setChallengeEmailPassword("");
      await qc.invalidateQueries({ queryKey: ["logins"] });
      if (selectedAuthLogin) authPreflightMutation.mutate(selectedAuthLogin);
    },
  });
  const authTraceQ = useQuery({
    queryKey: ["auth-trace", selectedAuthLogin],
    queryFn: () => getAuthTrace(selectedAuthLogin, 30),
    enabled: Boolean(selectedAuthLogin),
    refetchInterval: 8000,
  });
  const manualActionsQ = useQuery({
    queryKey: ["manual-actions"],
    queryFn: getManualActions,
    refetchInterval: 8000,
  });
  const selectedVerificationActions = useMemo(
    () =>
      (manualActionsQ.data?.actions ?? []).filter(
        (action) =>
          String(action.login_username || "") === selectedAuthLogin &&
          ["challenge_required", "two_factor_required"].includes(String(action.action_type || ""))
      ),
    [manualActionsQ.data, selectedAuthLogin]
  );
  const selectedLogin = useMemo(
    () => (loginsQ.data ?? []).find((login) => String(login.login_username || "") === selectedAuthLogin),
    [loginsQ.data, selectedAuthLogin]
  );
  useEffect(() => {
    if (!selectedLogin) return;
    setChallengeEmailHost(selectedLogin.challenge_email_host || "imap.gmail.com");
    setChallengeEmailPort("993");
    setChallengeEmailUseSsl(true);
    setChallengeEmailUsername(selectedLogin.challenge_email_username || "");
    setChallengeEmailMailbox(selectedLogin.challenge_email_mailbox || "INBOX");
    setChallengeEmailPassword("");
  }, [selectedLogin]);
  const accountSections = [
    { id: "setup" as const, label: "Setup", helper: "Add collector credentials" },
    { id: "factory" as const, label: "Factory", helper: "Create and warm up accounts" },
    { id: "maintenance" as const, label: "Maintenance", helper: "Sessions and password actions" },
    { id: "diagnostics" as const, label: "Diagnostics", helper: "Preflight and challenges" },
  ];

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
    const warmupTarget = warmupTargetUsername.trim().replace(/^@+/, "");
    const cron = scheduleInterval.trim();
    const shouldQueueWarmup = queueWarmupRun && Boolean(warmupTarget);
    startAccountCreateMutation.mutate({
      strategy: createStrategy,
      email: createEmail.trim(),
      full_name: createFullName.trim(),
      login_username: createUsername.trim(),
      login_password: createPassword.trim(),
      max_wait_seconds: 300,
      auto_set_runner: autoSetRunner,
      warmup_target_username: warmupTarget || undefined,
      queue_warmup_run: shouldQueueWarmup,
      schedule_interval: cron || undefined,
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

  useEffect(() => {
    if (!cfgQ.data?.config) return;
    const proxyEnabled = Boolean(cfgQ.data.config.proxy_enabled);
    if (!proxyEnabled && createStrategy === "private_api") {
      setCreateStrategy("guided_browser");
    }
  }, [cfgQ.data, createStrategy]);

  return (
    <section>
      <header className="page-header">
        <h1>Accounts</h1>
        <p>Tracking account readiness and session state for target monitoring and timeline collection runs.</p>
      </header>
      <div className="dossier-strip accounts-kpis">
        <article className="dossier-cell">
          <span>Ready sessions</span>
          <strong>{readyCount}/{loginsQ.data?.length ?? 0}</strong>
        </article>
        <article className="dossier-cell">
          <span>Factory state</span>
          <strong>{accountCreateQ.data?.job?.state || "idle"}</strong>
        </article>
        <article className="dossier-cell">
          <span>Runner mode</span>
          <strong>{String(cfgQ.data?.config?.run_login_mode || "auto")}</strong>
        </article>
        <article className="dossier-cell">
          <span>Diagnostics</span>
          <strong>{selectedAuthLogin ? `@${selectedAuthLogin}` : "-"}</strong>
        </article>
      </div>
      <div className="console-layout accounts-console">
        <aside className="console-rail">
          <div className="console-rail-title">Account workspace</div>
          {accountSections.map((item) => (
            <button
              key={item.id}
              className={`console-rail-item ${activePanel === item.id ? "active" : ""}`}
              onClick={() => setActivePanel(item.id)}
            >
              <strong>{item.label}</strong>
              <span>{item.helper}</span>
            </button>
          ))}
        </aside>
        <div className="console-main">
      {activePanel === "setup" ? (
      <article className="card panel-flat">
        <div className="panel-head">
          <div>
            <h3>Add Collector Account</h3>
            <p className="hint">Store the minimum credentials needed for session creation and target runs.</p>
          </div>
        </div>
        <div className="wizard-steps">
          <div className="wizard-step">
            <strong>1. Add collector account</strong>
            <span>Store login credentials and optional TOTP seed.</span>
          </div>
          <div className="wizard-step">
            <strong>2. Run first capture</strong>
            <span>Use Targets to launch the first run or create a schedule for your target account.</span>
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
      ) : null}
      {activePanel === "factory" ? (
      <article className="card panel-flat">
        <div className="panel-head">
          <div>
            <h3>Account Factory</h3>
            <p className="hint">Create a collector and optionally queue first-run warmup after signup.</p>
          </div>
        </div>
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
          <label>
            Auto-set as runner login
            <select value={autoSetRunner ? "true" : "false"} onChange={(e) => setAutoSetRunner(e.target.value === "true")}>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>
        </div>
        <details className="settings-detail advanced-detail">
          <summary>Warmup and schedule options</summary>
          <div className="form-grid">
          <label>
            Warmup target (optional)
            <input
              value={warmupTargetUsername}
              onChange={(e) => setWarmupTargetUsername(e.target.value)}
              placeholder="target username for first run"
            />
          </label>
          <label>
            Queue warmup run
            <select value={queueWarmupRun ? "true" : "false"} onChange={(e) => setQueueWarmupRun(e.target.value === "true")}>
              <option value="false">false</option>
              <option value="true">true</option>
            </select>
          </label>
          <label>
            Schedule cron (optional)
            <input
              value={scheduleInterval}
              onChange={(e) => setScheduleInterval(e.target.value)}
              placeholder="e.g. 0 */6 * * *"
            />
          </label>
          </div>
        </details>
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
        <p className="hint">
          Runner set: {accountCreateQ.data?.job?.auto_set_runner ? "yes" : "no"}
          {accountCreateQ.data?.job?.warmup_job_id ? ` · warmup job ${accountCreateQ.data.job.warmup_job_id}` : ""}
          {accountCreateQ.data?.job?.schedule_id ? ` · schedule #${accountCreateQ.data.job.schedule_id}` : ""}
        </p>
        {(accountCreateQ.data?.job?.warnings ?? []).length ? (
          <div className="table-wrap">
            <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
              {(accountCreateQ.data?.job?.warnings ?? []).map((w) => `- ${w}`).join("\n")}
            </pre>
          </div>
        ) : null}
        <p className="hint">For private API mode, use Diagnostics to submit email, SMS, challenge, or backup codes when prompted.</p>
        <div className="table-wrap">
          <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {(accountCreateQ.data?.log ?? []).join("\n") || "(no account factory logs yet)"}
          </pre>
        </div>
      </article>
      ) : null}
      {activePanel === "maintenance" ? (
      <article className="card panel-flat">
        <div className="panel-head">
          <div>
            <h3>Session Maintenance</h3>
            <p className="hint">Review collector readiness and run focused account actions.</p>
          </div>
        </div>
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
      ) : null}
      {activePanel === "diagnostics" ? (
      <article className="card panel-flat">
        <div className="panel-head">
          <div>
            <h3>Auth Diagnostics</h3>
            <p className="hint">Select one collector for preflight checks, challenge codes, and trace review.</p>
          </div>
        </div>
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
        <div className="form-grid">
          <label>
            Verification or challenge code
            <input
              value={accountVerificationCode}
              onChange={(e) => setAccountVerificationCode(e.target.value)}
              placeholder="6-digit code or backup code"
              inputMode="numeric"
              autoComplete="one-time-code"
            />
          </label>
        </div>
        <div className="row gap">
          <button
            className="btn-secondary"
            disabled={submitAccountChallengeMutation.isPending || !selectedAuthLogin || !accountVerificationCode.trim()}
            onClick={() =>
              submitAccountChallengeMutation.mutate({
                login: selectedAuthLogin,
                code: accountVerificationCode.trim(),
              })
            }
          >
            {submitAccountChallengeMutation.isPending ? "Submitting..." : "Submit code only"}
          </button>
          <button
            disabled={submitAccountChallengeMutation.isPending || !selectedAuthLogin || !accountVerificationCode.trim()}
            onClick={() =>
              submitAccountChallengeMutation.mutate({
                login: selectedAuthLogin,
                code: accountVerificationCode.trim(),
                retry: true,
              })
            }
          >
            {submitAccountChallengeMutation.isPending ? "Submitting..." : "Submit code and retry"}
          </button>
        </div>
        {submitAccountChallengeMutation.error ? <p className="error">{(submitAccountChallengeMutation.error as Error).message}</p> : null}
        {submitAccountChallengeMutation.data ? (
          <p className="hint">
            Code submitted for @{submitAccountChallengeMutation.data.login_username}
            {submitAccountChallengeMutation.data.retry_queued
              ? ` · retry queued for @${submitAccountChallengeMutation.data.retry_target_username} (${submitAccountChallengeMutation.data.retry_job_id})`
              : submitAccountChallengeMutation.data.retry_error
                ? ` · retry not queued: ${submitAccountChallengeMutation.data.retry_error}`
                : ""}
          </p>
        ) : null}
        <div className="panel-head compact-head">
          <div>
            <h3>Email code retrieval</h3>
            <p className="hint">
              {selectedLogin?.challenge_email_configured
                ? `Configured for ${selectedLogin.challenge_email_host || "IMAP"}`
                : "Configure IMAP so email challenge codes can be pulled automatically."}
            </p>
          </div>
        </div>
        <div className="form-grid">
          <label>
            IMAP host
            <input value={challengeEmailHost} onChange={(e) => setChallengeEmailHost(e.target.value)} placeholder="imap.gmail.com" />
          </label>
          <label>
            Port
            <input value={challengeEmailPort} onChange={(e) => setChallengeEmailPort(e.target.value)} placeholder="993" inputMode="numeric" />
          </label>
          <label>
            Mailbox
            <input value={challengeEmailMailbox} onChange={(e) => setChallengeEmailMailbox(e.target.value)} placeholder="INBOX" />
          </label>
          <label>
            Email username
            <input value={challengeEmailUsername} onChange={(e) => setChallengeEmailUsername(e.target.value)} placeholder="account@gmail.com" />
          </label>
          <label>
            Email app password
            <input
              type="password"
              value={challengeEmailPassword}
              onChange={(e) => setChallengeEmailPassword(e.target.value)}
              placeholder={selectedLogin?.challenge_email_configured ? "stored (leave blank to keep)" : "app password"}
              autoComplete="new-password"
            />
          </label>
          <label className="checkbox-line">
            <input type="checkbox" checked={challengeEmailUseSsl} onChange={(e) => setChallengeEmailUseSsl(e.target.checked)} />
            Use SSL
          </label>
        </div>
        <div className="row gap">
          <button
            disabled={
              setChallengeEmailMutation.isPending ||
              !selectedAuthLogin ||
              !challengeEmailHost.trim() ||
              !challengeEmailUsername.trim() ||
              (!selectedLogin?.challenge_email_configured && !challengeEmailPassword.trim())
            }
            onClick={() =>
              setChallengeEmailMutation.mutate({
                login_username: selectedAuthLogin,
                host: challengeEmailHost.trim(),
                port: Number(challengeEmailPort || (challengeEmailUseSsl ? 993 : 143)),
                use_ssl: challengeEmailUseSsl,
                username: challengeEmailUsername.trim(),
                password: challengeEmailPassword.trim() || undefined,
                mailbox: challengeEmailMailbox.trim() || "INBOX",
                test: true,
              })
            }
          >
            {setChallengeEmailMutation.isPending ? "Testing..." : "Save and test email"}
          </button>
          <button
            className="btn-secondary"
            disabled={setChallengeEmailMutation.isPending || !selectedAuthLogin || !selectedLogin?.challenge_email_configured}
            onClick={() => setChallengeEmailMutation.mutate({ login_username: selectedAuthLogin, clear: true })}
          >
            Clear email settings
          </button>
        </div>
        {setChallengeEmailMutation.error ? <p className="error">{(setChallengeEmailMutation.error as Error).message}</p> : null}
        {setChallengeEmailMutation.data ? (
          <p className="hint">
            Email challenge settings {setChallengeEmailMutation.data.configured ? "saved" : "cleared"}
            {setChallengeEmailMutation.data.test?.ok
              ? ` · IMAP ok · ${setChallengeEmailMutation.data.test.unseen_count ?? 0} unread`
              : ""}
          </p>
        ) : null}
        {selectedVerificationActions.length ? (
          <div className="table-wrap">
            <pre className="hint" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
              {selectedVerificationActions
                .map((action) => {
                  const target = action.target_username ? `@${String(action.target_username)}` : "unknown target";
                  const reason = action.error_code || action.reason || action.action_type || "verification";
                  return `${target} · ${reason} · job ${action.job_id || "-"}`;
                })
                .join("\n")}
            </pre>
          </div>
        ) : (
          <p className="hint">No open verification retry actions for this login.</p>
        )}
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
      ) : null}
        </div>
        <aside className="console-inspector">
          <h3>Selected Collector</h3>
          <label>
            Login
            <select value={selectedAuthLogin} onChange={(e) => setSelectedAuthLogin(e.target.value)}>
              {(loginsQ.data ?? []).map((login, idx) => (
                <option key={`inspector-${login.login_username || "login"}-${idx}`} value={login.login_username || ""}>
                  {login.login_username || "-"}
                </option>
              ))}
            </select>
          </label>
          <div className="inspector-facts">
            <div>
              <span>Session</span>
              <strong>{selectedLogin?.private_session_exists ? "ready" : "missing"}</strong>
            </div>
            <div>
              <span>Password</span>
              <strong>{selectedLogin?.has_password ? "stored" : "missing"}</strong>
            </div>
            <div>
              <span>2FA</span>
              <strong>{selectedLogin?.two_factor_method || "unknown"}</strong>
            </div>
            <div>
              <span>Open challenges</span>
              <strong>{selectedVerificationActions.length}</strong>
            </div>
          </div>
          {selectedLogin?.last_error ? <p className="error">{selectedLogin.last_error}</p> : <p className="hint">No current account error selected.</p>}
          <div className="row gap inspector-actions">
            <button
              className="btn-secondary"
              onClick={() => selectedAuthLogin && authPreflightMutation.mutate(selectedAuthLogin)}
              disabled={authPreflightMutation.isPending || !selectedAuthLogin}
            >
              {authPreflightMutation.isPending ? "Checking..." : "Preflight"}
            </button>
            <button
              className="btn-secondary"
              onClick={() => selectedAuthLogin && openPasswordModal(selectedAuthLogin)}
              disabled={!selectedAuthLogin}
            >
              Password
            </button>
          </div>
        </aside>
      </div>
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
  const [activeSettingsSection, setActiveSettingsSection] = useState("Run");

  const backendProfiles: Record<string, Record<string, string | number | boolean>> = {
    private: {
      run_http_timeout_seconds: 60,
      run_request_timeout: 60,
      run_private_request_sleep_seconds: 0,
      run_item_delay_min: 0.45,
      run_item_delay_max: 1.15,
      run_initial_fetch_delay_seconds: 2,
      run_pause_every_min: 0,
      run_pause_every_max: 0,
      run_pause_seconds_min: 0,
      run_pause_seconds_max: 0,
      run_completeness_retry_max: 2,
      run_completeness_retry_delay_seconds: 8,
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
    {
      const customLabels: Record<string, string> = {
        run_scraper_backend: "Collector Family",
        run_browser_collection_method: "Browser Collection Method",
        run_login_mode: "Login Mode",
        run_fetch_order: "Collection Order",
        run_followers_order: "Follower Sort Order",
        run_private_request_sleep_seconds: "Private Request Sleep",
        run_initial_fetch_delay_seconds: "Initial Fetch Delay",
        run_pre_login_flow: "Pre-login Warmup",
        run_post_login_flow: "Post-login Warmup",
        private_device_settings_json: "Private API Device Profile",
        private_user_agent: "Private API User Agent",
      };
      if (customLabels[key]) return customLabels[key];
      return key
        .replace(/_set$/, " configured")
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    };

  const onSaveConfig = () => {
    const payload: Partial<ConfigValues> & { proxy_password?: string; _apply_backend_profile?: boolean } = {};
    for (const [key, value] of Object.entries(draft)) {
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
  const activeSettings = sections.find((item) => item.section === activeSettingsSection) || sections[0];
  const booleanCount = Object.values(draft).filter((value) => typeof value === "boolean").length;
  const numericCount = Object.values(draft).filter((value) => typeof value === "number").length;

  useEffect(() => {
    if (sections.length && !sections.some((item) => item.section === activeSettingsSection)) {
      setActiveSettingsSection(sections[0].section);
    }
  }, [activeSettingsSection, sections]);

  const renderSettingControl = (key: string) => {
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
            <option value="browser">browser - web session collectors</option>
            <option value="private">private_api - instagrapi</option>
          </select>
          <span className="hint">Switching families auto-loads the tuned delay and rate profile.</span>
        </label>
      );
    }
    if (key === "run_browser_collection_method") {
      return (
        <label key={key}>
          {labelForKey(key)}
          <select
            value={String(value)}
            onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}
          >
            <option value="browser_native">browser_native - live browser collection</option>
            <option value="instaloader_session">instaloader_session - dedicated session</option>
          </select>
          <span className="hint">Used only when Collector Family is set to browser.</span>
        </label>
      );
    }
    if (key === "run_fetch_order") {
      return (
        <label key={key}>
          {labelForKey(key)}
          <select
            value={String(value)}
            onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}
          >
            <option value="followers_first">followers_first</option>
            <option value="following_first">following_first</option>
          </select>
        </label>
      );
    }
    if (key === "run_followers_order") {
      return (
        <label key={key}>
          {labelForKey(key)}
          <select
            value={String(value)}
            onChange={(e) => setDraft((prev) => ({ ...prev, [key]: e.target.value }))}
          >
            <option value="">default</option>
            <option value="date_followed_latest">date_followed_latest</option>
            <option value="date_followed_earliest">date_followed_earliest</option>
          </select>
          <span className="hint">Used by the private_api collector when fetching followers through instagrapi.</span>
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
  };

  return (
    <section>
      <header className="page-header">
        <h1>Settings</h1>
        <p>Runtime settings that affect target-account tracking cadence and operations behavior.</p>
      </header>
      <div className="dossier-strip settings-kpi-strip">
        <article className="dossier-cell">
          <span>Sections</span>
          <strong>{sections.length}</strong>
        </article>
        <article className="dossier-cell">
          <span>Booleans</span>
          <strong>{booleanCount}</strong>
        </article>
        <article className="dossier-cell">
          <span>Numeric controls</span>
          <strong>{numericCount}</strong>
        </article>
        <article className="dossier-cell">
          <span>Collector Family</span>
          <strong>{collectorFamilyLabel(String(draft.run_scraper_backend || "-"))}</strong>
        </article>
      </div>
      {!cfgQ.data?.config ? (
        <article className="card"><p className="hint">Loading settings…</p></article>
      ) : null}
      <div className="console-layout settings-console">
        <aside className="console-rail">
          <div className="console-rail-title">Settings groups</div>
          {sections.map(({ section, keys }) => (
            <button
              key={section}
              className={`console-rail-item ${activeSettings?.section === section ? "active" : ""}`}
              onClick={() => setActiveSettingsSection(section)}
            >
              <strong>{section}</strong>
              <span>{keys.length} setting{keys.length === 1 ? "" : "s"}</span>
            </button>
          ))}
        </aside>
        <article className="card panel-flat console-main">
          <div className="panel-head">
            <div>
              <h3>{activeSettings?.section || "Settings"} Settings</h3>
              <p className="hint settings-summary">
                {activeSettings ? sectionSummary(activeSettings.section, activeSettings.keys) : "Loading settings."}
              </p>
            </div>
            <span className="pill neutral">{activeSettings?.keys.length || 0} controls</span>
          </div>
          <div className="settings-grid">{(activeSettings?.keys || []).slice(0, 8).map(renderSettingControl)}</div>
          {(activeSettings?.keys.length || 0) > 8 ? (
            <details className="settings-detail advanced-detail">
              <summary>Advanced {activeSettings?.section} controls</summary>
              <div className="settings-grid">{(activeSettings?.keys || []).slice(8).map(renderSettingControl)}</div>
            </details>
          ) : null}
        </article>
        <aside className="console-inspector">
          <h3>Save And Proxy</h3>
          <label>
            Proxy password
            <input
              type="password"
              value={proxyPassword}
              onChange={(e) => setProxyPassword(e.target.value)}
              placeholder={cfgQ.data?.config?.proxy_password_set ? "stored (leave blank to keep)" : "set proxy password"}
            />
          </label>
          <div className="inspector-facts">
            <div>
              <span>Collector</span>
              <strong>{collectorFamilyLabel(String(draft.run_scraper_backend || "-"))}</strong>
            </div>
            <div>
              <span>Login mode</span>
              <strong>{String(draft.run_login_mode || "auto")}</strong>
            </div>
            <div>
              <span>Proxy</span>
              <strong>{draft.proxy_enabled ? "enabled" : "disabled"}</strong>
            </div>
          </div>
          <div className="row gap inspector-actions">
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
        </aside>
      </div>
    </section>
  );
}

function JobStatusPage() {
  const { jobId = "" } = useParams();
  const jobQ = useQuery({
    queryKey: ["run-job-summary", jobId],
    queryFn: () => getRunJobSummary(jobId),
    enabled: Boolean(jobId),
    refetchInterval: 4000,
  });
  const job = jobQ.data;
  const alternate = (job?.alternate || {}) as Record<string, unknown>;
  const retry = (job?.retry || {}) as Record<string, unknown>;
  const result = (job?.result || {}) as Record<string, unknown>;
  const followers = (job?.followers || {}) as Record<string, unknown>;
  const following = (job?.following || {}) as Record<string, unknown>;
  const activeRecovery = Boolean(alternate.running || retry.running);

  return (
    <section>
      <header className="page-header">
        <h1>Job Status</h1>
        <p>{jobId || "No job selected"}</p>
      </header>
      {jobQ.error ? <p className="error">{(jobQ.error as Error).message}</p> : null}
      <article className="card job-status-card">
        <div className="job-status-head">
          <div>
            <h3>
              @{job?.target_username || "-"} <span className="hint">via @{job?.login_username || "-"}</span>
            </h3>
            <p className="hint">
              {job?.state || (jobQ.isLoading ? "loading" : "-")} · {job?.phase || "no active phase"}
              {activeRecovery ? " · recovery pass active" : ""}
            </p>
          </div>
          <NavLink to="/operations" className="text-link">
            Operations
          </NavLink>
        </div>
        <div className="dossier-strip job-status-kpis">
          <article className="dossier-cell">
            <span>Collected</span>
            <strong>{String(job?.count ?? "-")}</strong>
          </article>
          <article className="dossier-cell">
            <span>Expected</span>
            <strong>{String(job?.expected_total ?? "-")}</strong>
          </article>
          <article className="dossier-cell">
            <span>Missing</span>
            <strong>{String(job?.missing_count ?? "-")}</strong>
          </article>
          <article className="dossier-cell">
            <span>Last update</span>
            <strong>{formatSecondsAge(job?.seconds_since_update)}</strong>
          </article>
        </div>
        <div className="job-status-grid">
          <div>
            <span>Following</span>
            <strong>{String(following.count ?? "-")} / {String(following.expected_total ?? "-")}</strong>
          </div>
          <div>
            <span>Followers</span>
            <strong>{String(followers.count ?? "-")} / {String(followers.expected_total ?? "-")}</strong>
          </div>
          <div>
            <span>Page</span>
            <strong>{String(job?.page_index ?? "-")}</strong>
          </div>
          <div>
            <span>Page yield</span>
            <strong>{String(job?.page_unique_new ?? job?.page_unique_count ?? "-")} new</strong>
          </div>
          <div>
            <span>Duplicates</span>
            <strong>{String(job?.duplicates_total ?? "-")}</strong>
          </div>
          <div>
            <span>Last page</span>
            <strong>{formatSecondsAge(job?.seconds_since_last_page)}</strong>
          </div>
          <div>
            <span>Alternate pass</span>
            <strong>
              {alternate.running
                ? `${String(alternate.endpoint || "-")} page ${String(alternate.page_index ?? "-")} · added ${String(alternate.added ?? "-")}`
                : "-"}
            </strong>
          </div>
          <div>
            <span>Retry pass</span>
            <strong>
              {retry.running || retry.complete
                ? `attempt ${String(retry.attempt ?? "-")} · added ${String(retry.added ?? "-")} · missing ${String(retry.missing_count ?? "-")}`
                : "-"}
            </strong>
          </div>
          <div>
            <span>Last page time</span>
            <strong>{formatEpochTime(job?.last_page_at)}</strong>
          </div>
          <div>
            <span>Worker</span>
            <strong>{job?.last_worker_message || "-"}</strong>
          </div>
        </div>
        {job?.error ? <p className="error">{job.error}</p> : null}
        {result.run_id || result.followers_count || result.followees_count ? (
          <p className="hint">
            Result: run {String(result.run_id ?? "-")} · followers {String(result.followers_count ?? "-")} · following {String(result.followees_count ?? "-")}
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
        <Route path="/home" element={<CommandCenterPage />} />
        <Route path="/brief" element={<CommandCenterPage />} />
        <Route path="/digest" element={<CommandCenterPage />} />
        <Route path="/targets" element={<TargetsPage />} />
        <Route path="/subjects" element={<TargetsPage />} />
        <Route path="/network" element={<NetworkPage />} />
        <Route path="/people" element={<NetworkPage />} />
        <Route path="/activity" element={<ExplorerPage />} />
        <Route path="/explorer" element={<ExplorerPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="/machinery" element={<SystemPage />} />
        <Route path="/operations" element={<OperationsPage />} />
        <Route path="/unfollow" element={<UnfollowPage />} />
        <Route path="/accounts" element={<AccountsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/jobs/:jobId" element={<JobStatusPage />} />
      </Routes>
    </AppShell>
  );
}
