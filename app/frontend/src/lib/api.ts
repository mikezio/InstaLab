import {
  appStatusSchema,
  accountCreateStatusSchema,
  reconHealthSchema,
  reconHistorySchema,
  reconJobResultSchema,
  reconQueueSchema,
  runStatusSchema,
  runJobStatusSchema,
  runJobDetailSchema,
  type AppStatus,
  type AccountCreateStatus,
  type ReconHealth,
  type ReconHistoryItem,
  type ReconJobResult,
  type ReconQueue,
  type RunStatus,
  type RunJobStatus,
  type RunJobDetail,
  type TargetSummaryItem,
  type ScheduleItem,
  type LoginItem,
  type ConfigPayload,
  type ConfigValues,
  type RelationshipEvent,
  type RelationshipHistoryRow,
  type AuthTracePayload,
  type AuthPreflightPayload,
  targetsSummarySchema,
  scheduleSchema,
  loginSchema,
  configSchema,
  relationshipEventSchema,
  relationshipHistorySchema,
  authTraceSchema,
  authPreflightSchema,
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

export async function getReconHealth(): Promise<ReconHealth> {
  const data = await fetchJson<unknown>("/recon/health");
  return reconHealthSchema.parse(data);
}

export async function getReconHistory(): Promise<ReconHistoryItem[]> {
  const data = await fetchJson<unknown>("/recon/history?limit=30");
  return reconHistorySchema.parse(data);
}

export async function getReconQueue(): Promise<ReconQueue> {
  const data = await fetchJson<unknown>("/recon/queue");
  return reconQueueSchema.parse(data);
}

export async function runRecon(payload: {
  mode: "username" | "email" | "phone";
  query_value: string;
  options: { ai?: boolean; generate_pdf?: boolean; no_nsfw?: boolean };
}): Promise<{ job_id: string; status: string }> {
  return fetchJson<{ job_id: string; status: string }>("/recon/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getReconJob(jobId: string): Promise<ReconJobResult> {
  const data = await fetchJson<unknown>(`/recon/run/${encodeURIComponent(jobId)}`);
  return reconJobResultSchema.parse(data);
}

export async function cancelRecon(jobId: string): Promise<void> {
  await fetchJson(`/recon/run/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function deleteRecon(jobId: string): Promise<void> {
  await fetchJson(`/recon/run/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}

export async function getTargetsSummary(): Promise<TargetSummaryItem[]> {
  const data = await fetchJson<unknown>("/targets_summary");
  return targetsSummarySchema.parse(data);
}

export async function getSchedules(): Promise<ScheduleItem[]> {
  const data = await fetchJson<unknown>("/schedules");
  return scheduleSchema.parse(data);
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
}): Promise<{ started: boolean; strategy: string; login_username: string; max_wait_seconds: number }> {
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
  interval: string;
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

export async function submitChallengeCode(login_username: string, code: string): Promise<{ ok: boolean; login_username: string }> {
  return fetchJson("/logins/challenge", {
    method: "POST",
    body: JSON.stringify({ login_username, code }),
  });
}

export async function setLoginNewPassword(login_username: string, new_password: string): Promise<{ ok: boolean; login_username: string }> {
  return fetchJson("/logins/new-password", {
    method: "POST",
    body: JSON.stringify({ login_username, new_password }),
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

export async function getRelationshipEvents(target: string): Promise<RelationshipEvent[]> {
  const data = await fetchJson<unknown>(`/relationship_events?target=${encodeURIComponent(target)}&limit=200`);
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
