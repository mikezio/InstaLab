import {
  appStatusSchema,
  accountCreateStatusSchema,
  runStatusSchema,
  runJobStatusSchema,
  runJobDetailSchema,
  runJobSummarySchema,
  type AppStatus,
  type AccountCreateStatus,
  type RunStatus,
  type RunJobStatus,
  type RunJobDetail,
  type RunJobSummary,
  type TargetSummaryItem,
  type ScheduleItem,
  type CountWatchSampleItem,
  type LoginItem,
  type ConfigPayload,
  type ConfigValues,
  type RelationshipEvent,
  type RelationshipHistoryRow,
  type AuthTracePayload,
  type AuthPreflightPayload,
  type RunHistoryItem,
  type RunDetail,
  type UnfollowStatus,
  type UnfollowPreview,
  type UiEvidenceItem,
  type UiNetworkItem,
  type UiTargetChangeItem,
  type UiTargetItem,
  targetsSummarySchema,
  scheduleSchema,
  countWatchSampleSchema,
  loginSchema,
  configSchema,
  uiEvidenceSchema,
  uiNetworkSchema,
  uiTargetsSchema,
  uiTargetTimelineSchema,
  uiTargetChangesSchema,
  uiSystemHealthSchema,
  uiBriefSchema,
  relationshipEventSchema,
  relationshipHistorySchema,
  authTraceSchema,
  authPreflightSchema,
  runHistorySchema,
  runDetailSchema,
  unfollowStatusSchema,
  unfollowPreviewSchema,
} from "./schemas";

const API_BASE = "/api";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    const message =
      typeof body === "object" && body && "error" in body
        ? String((body as Record<string, unknown>).error)
        : `${res.status} ${res.statusText}`;
    throw new Error(message);
  }

  return body as T;
}

export async function getAppStatus(): Promise<AppStatus> {
  const data = await fetchJson<unknown>("/status");
  return appStatusSchema.parse(data);
}

export async function getRunStatus(): Promise<RunStatus> {
  const data = await fetchJson<unknown>("/status");
  return runStatusSchema.parse(data);
}

export async function getManualActions(): Promise<{
  actions: Array<Record<string, unknown>>;
}> {
  return fetchJson("/manual/actions");
}

export async function resolveManualAction(actionId: string, note?: string): Promise<{ ok: boolean }> {
  return fetchJson(`/manual/actions/${encodeURIComponent(actionId)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export async function getTargetsSummary(): Promise<TargetSummaryItem[]> {
  const data = await fetchJson<unknown>("/targets_summary");
  return targetsSummarySchema.parse(data);
}

export async function getUiEvidence(params?: {
  target?: string;
  relation_type?: "followers" | "following" | "";
  event_type?: "added" | "removed" | "";
  limit?: number;
}): Promise<UiEvidenceItem[]> {
  const qs = new URLSearchParams();
  if (params?.target) qs.set("target", params.target);
  if (params?.relation_type) qs.set("relation_type", params.relation_type);
  if (params?.event_type) qs.set("event_type", params.event_type);
  if (typeof params?.limit === "number") qs.set("limit", String(params.limit));
  const data = await fetchJson<unknown>(`/ui/evidence${qs.toString() ? `?${qs.toString()}` : ""}`);
  return uiEvidenceSchema.parse(data).items;
}

export async function getUiTargets(): Promise<UiTargetItem[]> {
  const data = await fetchJson<unknown>("/ui/targets");
  return uiTargetsSchema.parse(data).items;
}

export async function getUiNetwork(params?: {
  target?: string;
  state?: "mutual" | "they_follow" | "subject_follows" | "disconnected" | "";
  q?: string;
  limit?: number;
}): Promise<UiNetworkItem[]> {
  const qs = new URLSearchParams();
  if (params?.target) qs.set("target", params.target);
  if (params?.state) qs.set("state", params.state);
  if (params?.q) qs.set("q", params.q);
  if (typeof params?.limit === "number") qs.set("limit", String(params.limit));
  const data = await fetchJson<unknown>(`/ui/network${qs.toString() ? `?${qs.toString()}` : ""}`);
  return uiNetworkSchema.parse(data).items;
}

export async function getUiTargetTimeline(target: string, limit = 20) {
  const data = await fetchJson<unknown>(
    `/ui/target_timeline?target=${encodeURIComponent(target)}&limit=${encodeURIComponent(String(limit))}`
  );
  return uiTargetTimelineSchema.parse(data).items;
}

export async function getUiTargetChanges(target: string, limit = 40): Promise<UiTargetChangeItem[]> {
  const data = await fetchJson<unknown>(
    `/ui/target_changes?target=${encodeURIComponent(target)}&limit=${encodeURIComponent(String(limit))}`
  );
  return uiTargetChangesSchema.parse(data).items;
}

export async function getUiSystemHealth() {
  const data = await fetchJson<unknown>("/ui/system/health");
  return uiSystemHealthSchema.parse(data);
}

export async function getUiBrief() {
  const data = await fetchJson<unknown>("/ui/brief");
  return uiBriefSchema.parse(data);
}

export async function getSchedules(): Promise<ScheduleItem[]> {
  const data = await fetchJson<unknown>("/schedules");
  return scheduleSchema.parse(data);
}

export async function getCountWatchSamples(target: string, limit = 20): Promise<CountWatchSampleItem[]> {
  const data = await fetchJson<unknown>(
    `/count_watch_samples?target=${encodeURIComponent(target)}&limit=${encodeURIComponent(String(limit))}`
  );
  return countWatchSampleSchema.parse(data);
}

export async function getLogins(): Promise<LoginItem[]> {
  const data = await fetchJson<unknown>("/logins");
  return loginSchema.parse(data);
}

export async function getConfig(): Promise<ConfigPayload> {
  const data = await fetchJson<unknown>("/config");
  return configSchema.parse(data);
}

export async function updateConfig(
  payload: Partial<ConfigValues> & { proxy_password?: string; _apply_backend_profile?: boolean }
): Promise<ConfigPayload> {
  const data = await fetchJson<unknown>("/config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  // /api/config PUT returns {updated, config}; normalize to GET schema shape for callers.
  if (typeof data === "object" && data && "config" in (data as Record<string, unknown>)) {
    return configSchema.parse({
      config: (data as Record<string, unknown>).config,
      defaults: undefined,
    });
  }
  return configSchema.parse(data);
}

export async function testProxy(): Promise<{ ok?: boolean; status?: number; latency_ms?: number; error?: string }> {
  return fetchJson("/proxy/test");
}

export async function addLogin(payload: {
  login_username: string;
  login_password?: string;
  totp_seed?: string;
}): Promise<{ added?: string; updated?: string }> {
  return fetchJson("/logins/add", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getAccountCreateStatus(): Promise<AccountCreateStatus> {
  const data = await fetchJson<unknown>("/logins/create/status");
  return accountCreateStatusSchema.parse(data);
}

export async function startAccountCreate(payload: {
  strategy: "private_api" | "guided_browser";
  email: string;
  full_name: string;
  login_username: string;
  login_password: string;
  max_wait_seconds?: number;
  auto_set_runner?: boolean;
  warmup_target_username?: string;
  queue_warmup_run?: boolean;
  schedule_interval?: string;
}): Promise<{
  started: boolean;
  strategy: string;
  login_username: string;
  max_wait_seconds: number;
  auto_set_runner?: boolean;
  warmup_target_username?: string | null;
  queue_warmup_run?: boolean;
  schedule_interval?: string | null;
}> {
  return fetchJson("/logins/create/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function cancelAccountCreate(): Promise<{ cancelled: boolean }> {
  return fetchJson("/logins/create/cancel", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function resetLogin(login_username: string): Promise<{ ok?: boolean; removed?: boolean }> {
  return fetchJson("/logins/reset", {
    method: "POST",
    body: JSON.stringify({ login_username }),
  });
}

export async function deleteLogin(login_username: string): Promise<{ deleted?: string; session_removed?: boolean }> {
  return fetchJson("/logins/delete", {
    method: "POST",
    body: JSON.stringify({ login_username, delete_session: true }),
  });
}

export async function getAuthTrace(login_username: string, limit = 30): Promise<AuthTracePayload> {
  const data = await fetchJson<unknown>(
    `/logins/auth/trace?login_username=${encodeURIComponent(login_username)}&limit=${encodeURIComponent(String(limit))}`
  );
  return authTraceSchema.parse(data);
}

export async function runAuthPreflight(login_username: string): Promise<AuthPreflightPayload> {
  const data = await fetchJson<unknown>("/logins/auth/preflight", {
    method: "POST",
    body: JSON.stringify({ login_username }),
  });
  return authPreflightSchema.parse(data);
}

export async function createSchedule(payload: {
  login_username: string;
  target_username: string;
  interval?: string;
  mode?: "full_run" | "count_watch";
  trigger_delta?: number;
  schedule_kind?: "daily" | "weekly" | "every_n_days" | "cron";
  schedule_time?: string;
  schedule_weekday?: number;
  schedule_interval_days?: number;
  schedule_start_date?: string;
}): Promise<{ id: number }> {
  return fetchJson("/schedules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function startRun(payload: {
  login_username: string;
  target_username: string;
  two_factor_code?: string;
  challenge_code?: string;
}): Promise<{ job_id: string }> {
  return fetchJson("/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getRunJobStatus(jobId: string): Promise<RunJobStatus> {
  const data = await fetchJson<unknown>(`/run/${encodeURIComponent(jobId)}`);
  return runJobStatusSchema.parse(data);
}

export async function getRunJobDetail(jobId: string): Promise<RunJobDetail> {
  const data = await fetchJson<unknown>(`/jobs/${encodeURIComponent(jobId)}/detail`);
  return runJobDetailSchema.parse(data);
}

export async function getRunJobSummary(jobId: string): Promise<RunJobSummary> {
  const data = await fetchJson<unknown>(`/jobs/${encodeURIComponent(jobId)}/summary`);
  return runJobSummarySchema.parse(data);
}

export async function submitChallengeCode(
  login_username: string,
  code: string,
  options?: { retry_run?: boolean }
): Promise<{
  ok: boolean;
  login_username: string;
  retry_queued?: boolean;
  retry_job_id?: string | null;
  retry_target_username?: string | null;
  retry_error?: string;
  cooldown_seconds?: number;
}> {
  return fetchJson("/logins/challenge", {
    method: "POST",
    body: JSON.stringify({ login_username, code, retry_run: Boolean(options?.retry_run) }),
  });
}

export async function setLoginNewPassword(login_username: string, new_password: string): Promise<{ ok: boolean; login_username: string }> {
  return fetchJson("/logins/new-password", {
    method: "POST",
    body: JSON.stringify({ login_username, new_password }),
  });
}

export async function setChallengeEmail(payload: {
  login_username: string;
  host?: string;
  port?: number;
  use_ssl?: boolean;
  username?: string;
  password?: string;
  mailbox?: string;
  test?: boolean;
  clear?: boolean;
}): Promise<{
  ok: boolean;
  login_username: string;
  configured?: boolean;
  host?: string;
  port?: number;
  use_ssl?: boolean;
  mailbox?: string;
  test?: { ok?: boolean; unseen_count?: number; error?: string };
}> {
  return fetchJson("/logins/challenge-email", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function requestPasswordReset(
  login_username: string,
  email_or_username?: string
): Promise<{
  ok: boolean;
  login_username: string;
  email_or_username?: string;
  http_status?: number;
  payload?: Record<string, unknown>;
  via_proxy?: boolean;
}> {
  return fetchJson("/logins/password/reset-request", {
    method: "POST",
    body: JSON.stringify({ login_username, email_or_username }),
  });
}

export async function deleteSchedule(scheduleId: number): Promise<{ deleted: number }> {
  return fetchJson(`/schedules/${scheduleId}`, {
    method: "DELETE",
  });
}

export async function getRelationshipEvents(
  target: string,
  options?: {
    relation_type?: "followers" | "following" | "";
    event_type?: "added" | "removed" | "";
    username?: string;
    run_id?: number;
    observed_from?: string;
    observed_to?: string;
    limit?: number;
  }
): Promise<RelationshipEvent[]> {
  const params = new URLSearchParams();
  params.set("target", target);
  params.set("limit", String(options?.limit ?? 200));
  if (options?.relation_type) params.set("relation_type", options.relation_type);
  if (options?.event_type) params.set("event_type", options.event_type);
  if (options?.username) params.set("username", options.username.trim());
  if (typeof options?.run_id === "number") params.set("run_id", String(options.run_id));
  if (options?.observed_from) params.set("observed_from", options.observed_from);
  if (options?.observed_to) params.set("observed_to", options.observed_to);
  const data = await fetchJson<unknown>(`/relationship_events?${params.toString()}`);
  return relationshipEventSchema.parse(data);
}

export async function getRelationshipHistory(
  target: string,
  relationType: "followers" | "following"
): Promise<RelationshipHistoryRow[]> {
  const data = await fetchJson<unknown>(
    `/relationship_history?target=${encodeURIComponent(target)}&relation_type=${encodeURIComponent(relationType)}&limit=100`
  );
  return relationshipHistorySchema.parse(data);
}

export async function getRuns(
  target: string,
  limit = 40,
  kind: "full" | "profile_only" | "all" = "full"
): Promise<RunHistoryItem[]> {
  const data = await fetchJson<unknown>(
    `/runs?target=${encodeURIComponent(target)}&limit=${encodeURIComponent(String(limit))}&kind=${encodeURIComponent(kind)}`
  );
  return runHistorySchema.parse(data);
}

export async function getRunDetail(runId: number): Promise<RunDetail> {
  const data = await fetchJson<unknown>(`/run/${encodeURIComponent(String(runId))}`);
  return runDetailSchema.parse(data);
}

export async function deleteRun(runId: number): Promise<{ deleted: number; target?: string; undo?: boolean }> {
  return fetchJson(`/run/${encodeURIComponent(String(runId))}`, {
    method: "DELETE",
  });
}

export async function undoRun(runId: number): Promise<{ restored: number }> {
  return fetchJson(`/run/undo/${encodeURIComponent(String(runId))}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function cancelRun(payload: {
  login_username?: string;
  target_username?: string;
  job_id?: string;
}): Promise<{ cancelled: boolean; queued_cancelled?: boolean; stale_running_cancelled?: boolean }> {
  return fetchJson("/run/cancel", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getUnfollowStatus(login_username?: string): Promise<UnfollowStatus> {
  const suffix = login_username ? `?login_username=${encodeURIComponent(login_username)}` : "";
  const data = await fetchJson<unknown>(`/unfollow/status${suffix}`);
  return unfollowStatusSchema.parse(data);
}

export async function getUnfollowPreview(login_username?: string): Promise<UnfollowPreview> {
  const suffix = login_username ? `?login_username=${encodeURIComponent(login_username)}` : "";
  const data = await fetchJson<unknown>(`/unfollow/preview${suffix}`);
  return unfollowPreviewSchema.parse(data);
}

export async function startUnfollow(payload: {
  login_username: string;
  dry_run?: boolean;
  max_actions?: number;
  delay_min?: number;
  delay_max?: number;
}): Promise<{ started: boolean; count: number }> {
  return fetchJson("/unfollow/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function cancelUnfollow(): Promise<{ cancelled: boolean }> {
  return fetchJson("/unfollow/cancel", {
    method: "POST",
    body: JSON.stringify({}),
  });
}
