import {
  appStatusSchema,
  reconHealthSchema,
  reconHistorySchema,
  reconJobResultSchema,
  reconQueueSchema,
  type AppStatus,
  type ReconHealth,
  type ReconHistoryItem,
  type ReconJobResult,
  type ReconQueue,
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
