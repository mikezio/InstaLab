import { z } from "zod";

export const appStatusSchema = z.object({
  status: z.string().optional(),
});

export const runStatusSchema = z.object({
  state: z.string().optional(),
  active_jobs: z
    .array(
      z.object({
        login_username: z.string().nullable().optional(),
        target_username: z.string().nullable().optional(),
        job_id: z.string().nullable().optional(),
        elapsed_seconds: z.number().optional(),
      })
    )
    .optional(),
  queued_jobs: z
    .array(
      z.object({
        job_id: z.string().optional(),
        meta: z
          .object({
            login_username: z.string().nullable().optional(),
            target_username: z.string().nullable().optional(),
          })
          .passthrough()
          .optional(),
      })
    )
    .optional(),
});

export const runJobStatusSchema = z.object({
  done: z.boolean().optional(),
  meta: z
    .object({
      login_username: z.string().nullable().optional(),
      target_username: z.string().nullable().optional(),
      state: z.string().optional(),
      submitted_at: z.string().optional(),
      started_at: z.string().optional(),
    })
    .passthrough()
    .optional(),
  payload: z
    .object({
      status: z.string().optional(),
      error: z.string().optional(),
      error_code: z.string().optional(),
      started_at: z.string().optional(),
      finished_at: z.string().optional(),
      result: z.record(z.string(), z.any()).optional(),
    })
    .passthrough()
    .optional(),
});

export const runJobDetailSchema = z.object({
  job_id: z.string().optional(),
  progress: z.record(z.string(), z.any()).nullable().optional(),
  result: z.record(z.string(), z.any()).nullable().optional(),
  worker_out_tail: z.string().optional(),
  worker_err_tail: z.string().optional(),
  trace_tail: z.string().optional(),
});

export const reconHealthSchema = z.object({
  enabled: z.boolean().optional(),
  ai: z
    .object({
      enabled: z.boolean().optional(),
      key_configured: z.boolean().optional(),
    })
    .optional(),
  queue: z
    .object({
      running: z.number().optional(),
      queued: z.number().optional(),
      max_concurrency: z.number().optional(),
      queue_limit: z.number().optional(),
    })
    .optional(),
  tools: z
    .object({
      blackbird: z.object({ executable_exists: z.boolean().optional() }).optional(),
      phoneinfoga: z.object({ executable_exists: z.boolean().optional() }).optional(),
    })
    .optional(),
});

export const reconHistoryItemSchema = z.object({
  id: z.string(),
  created_at: z.string().optional(),
  mode: z.string().optional(),
  query_value: z.string().optional(),
  status: z.string().optional(),
  findings_count: z.number().optional(),
  report_count: z.number().optional(),
});

export const reconHistorySchema = z.array(reconHistoryItemSchema);

export const reconQueueItemSchema = z.object({
  job_id: z.string().optional(),
  state: z.string().optional(),
  query_value: z.string().optional(),
  elapsed_seconds: z.number().optional(),
  queue_position: z.number().optional(),
  timeout_remaining_seconds: z.number().optional(),
});

export const reconQueueSchema = z.object({
  queue: z.array(reconQueueItemSchema).optional(),
  running: z.number().optional(),
  queued: z.number().optional(),
  max_concurrency: z.number().optional(),
  queue_limit: z.number().optional(),
});

export const reconJobResultSchema = z.object({
  job: z
    .object({
      id: z.string().optional(),
      created_at: z.string().optional(),
      mode: z.string().optional(),
      query_value: z.string().optional(),
      status: z.string().optional(),
      error_message: z.string().nullable().optional(),
    })
    .optional(),
  findings: z.array(z.any()).optional(),
  artifacts: z.array(z.any()).optional(),
});

export const targetsSummarySchema = z.array(
  z.object({
    target_username: z.string().optional(),
    latest_run_at: z.string().optional(),
    latest_followers_count: z.number().optional(),
    latest_following_count: z.number().optional(),
  })
);

export const scheduleSchema = z.array(
  z.object({
    id: z.number().optional(),
    login_username: z.string().optional(),
    target_username: z.string().optional(),
    interval: z.string().optional(),
    next_run: z.string().nullable().optional(),
  })
);

export const loginSchema = z.array(
  z.object({
    login_username: z.string().optional(),
    cookie_file: z.string().optional(),
    source: z.string().optional(),
    private_session_mtime: z.number().nullable().optional(),
    has_password: z.boolean().optional(),
    has_totp_seed: z.boolean().optional(),
    private_session_exists: z.boolean().optional(),
    last_login_at: z.string().nullable().optional(),
    last_error: z.string().nullable().optional(),
    session_fail_streak: z.number().optional(),
    session_last_fail_at: z.string().nullable().optional(),
    session_marked_stale: z.boolean().optional(),
    two_factor_method: z.string().nullable().optional(),
    auth_last_event: z.string().nullable().optional(),
    auth_last_event_at: z.string().nullable().optional(),
  })
);

export const authTraceRowSchema = z.object({
  at: z.string().optional(),
  login_username: z.string().optional(),
  event: z.string().optional(),
  two_factor_method: z.string().optional(),
  totp_source: z.string().optional(),
  error_code: z.string().optional(),
  error: z.string().optional(),
  session_fail_streak: z.number().optional(),
  session_marked_stale: z.boolean().optional(),
  session_validation_ok: z.boolean().optional(),
});

export const authTraceSchema = z.object({
  login_username: z.string().optional(),
  trace: z.array(authTraceRowSchema).optional(),
});

export const authPreflightSchema = z.object({
  ok: z.boolean().optional(),
  login_username: z.string().optional(),
  private_session_exists: z.boolean().optional(),
  has_password: z.boolean().optional(),
  has_totp_seed: z.boolean().optional(),
  session_fail_streak: z.number().optional(),
  session_marked_stale: z.boolean().optional(),
  session_last_fail_at: z.string().nullable().optional(),
  two_factor_method: z.string().nullable().optional(),
  clock_skew: z
    .object({
      ok: z.boolean().optional(),
      source: z.string().optional(),
      date_header: z.string().optional(),
      skew_seconds: z.number().optional(),
      abs_skew_seconds: z.number().optional(),
      error: z.string().optional(),
    })
    .passthrough()
    .optional(),
  warnings: z.array(z.string()).optional(),
  trace: z.array(authTraceRowSchema).optional(),
});

export const configValuesSchema = z.object({
  run_stall_seconds: z.number().optional(),
  run_max_seconds: z.number().optional(),
  run_http_timeout_seconds: z.number().optional(),
  run_request_timeout: z.number().optional(),
  run_item_delay_min: z.number().optional(),
  run_item_delay_max: z.number().optional(),
  run_login_mode: z.string().optional(),
  run_trace_enabled: z.boolean().optional(),
  proxy_enabled: z.boolean().optional(),
  proxy_host: z.string().optional(),
  proxy_port: z.number().optional(),
  proxy_username: z.string().optional(),
  proxy_password_set: z.boolean().optional(),
  monitor_enabled: z.boolean().optional(),
  monitor_login_username: z.string().optional(),
  monitor_interval_minutes: z.number().optional(),
  recon_enabled: z.boolean().optional(),
  recon_max_concurrency: z.number().optional(),
  recon_queue_limit: z.number().optional(),
  recon_timeout_seconds: z.number().optional(),
  recon_artifact_retention_days: z.number().optional(),
  recon_blackbird_ai_enabled: z.boolean().optional(),
  recon_blackbird_no_nsfw: z.boolean().optional(),
  recon_phoneinfoga_enabled: z.boolean().optional(),
});

export const configSchema = z.object({
  config: configValuesSchema,
  defaults: configValuesSchema.optional(),
});

export const accountCreateStatusSchema = z.object({
  job: z
    .object({
      state: z.string().optional(),
      started_at: z.string().nullable().optional(),
      finished_at: z.string().nullable().optional(),
      strategy: z.string().optional(),
      email: z.string().nullable().optional(),
      full_name: z.string().nullable().optional(),
      login_username: z.string().nullable().optional(),
      max_wait_seconds: z.number().optional(),
      message: z.string().nullable().optional(),
      last_url: z.string().nullable().optional(),
      saved_login: z.boolean().optional(),
      elapsed_seconds: z.number().nullable().optional(),
    })
    .optional(),
  log: z.array(z.string()).optional(),
});

export const relationshipEventSchema = z.array(
  z.object({
    id: z.number().optional(),
    target_username: z.string().optional(),
    login_username: z.string().optional(),
    username: z.string().optional(),
    relation_type: z.string().optional(),
    event_type: z.string().optional(),
    observed_at: z.string().optional(),
    run_id: z.number().optional(),
    prev_run_id: z.number().optional(),
  })
);

export const relationshipHistorySchema = z.array(
  z.object({
    target_username: z.string().optional(),
    username: z.string().optional(),
    active: z.number().optional(),
    first_seen: z.string().optional(),
    last_seen: z.string().optional(),
    unfollowed_at: z.string().nullable().optional(),
  })
);

export type AppStatus = z.infer<typeof appStatusSchema>;
export type RunStatus = z.infer<typeof runStatusSchema>;
export type RunJobStatus = z.infer<typeof runJobStatusSchema>;
export type RunJobDetail = z.infer<typeof runJobDetailSchema>;
export type ReconHealth = z.infer<typeof reconHealthSchema>;
export type ReconHistoryItem = z.infer<typeof reconHistoryItemSchema>;
export type ReconQueue = z.infer<typeof reconQueueSchema>;
export type ReconJobResult = z.infer<typeof reconJobResultSchema>;
export type TargetSummaryItem = z.infer<typeof targetsSummarySchema>[number];
export type ScheduleItem = z.infer<typeof scheduleSchema>[number];
export type LoginItem = z.infer<typeof loginSchema>[number];
export type ConfigPayload = z.infer<typeof configSchema>;
export type ConfigValues = z.infer<typeof configValuesSchema>;
export type AccountCreateStatus = z.infer<typeof accountCreateStatusSchema>;
export type RelationshipEvent = z.infer<typeof relationshipEventSchema>[number];
export type RelationshipHistoryRow = z.infer<typeof relationshipHistorySchema>[number];
export type AuthTraceRow = z.infer<typeof authTraceRowSchema>;
export type AuthTracePayload = z.infer<typeof authTraceSchema>;
export type AuthPreflightPayload = z.infer<typeof authPreflightSchema>;
