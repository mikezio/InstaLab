import { z } from "zod";

export const appStatusSchema = z.object({
  status: z.string().optional(),
});

export const runStatusSchema = z.object({
  state: z.string().optional(),
  manual_actions_open: z.number().optional(),
  manual_actions: z
    .array(
      z.object({
        action_id: z.string().optional(),
        login_username: z.string().optional(),
        target_username: z.string().optional(),
        job_id: z.string().optional(),
        action_type: z.string().optional(),
        reason: z.string().optional(),
        error_code: z.string().nullable().optional(),
        error_message: z.string().nullable().optional(),
        recommended_step: z.string().optional(),
        created_at: z.string().optional(),
        updated_at: z.string().optional(),
      })
    )
    .optional(),
  cooldowns: z
    .array(
      z.object({
        login_username: z.string().optional(),
        cooldown_seconds: z.number().optional(),
        error_code: z.string().nullable().optional(),
        error_message: z.string().nullable().optional(),
      })
    )
    .optional(),
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
  run_scraper_backend: z.string().optional(),
  run_trace_enabled: z.boolean().optional(),
  proxy_enabled: z.boolean().optional(),
  proxy_host: z.string().optional(),
  proxy_port: z.number().optional(),
  proxy_username: z.string().optional(),
  proxy_password_set: z.boolean().optional(),
  monitor_enabled: z.boolean().optional(),
  monitor_login_username: z.string().optional(),
  monitor_interval_minutes: z.number().optional(),
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

export const runHistorySchema = z.array(
  z.object({
    id: z.number().optional(),
    timestamp: z.string().optional(),
    followers_count: z.number().optional(),
    followees_count: z.number().optional(),
    followers_added: z.number().optional(),
    followers_removed: z.number().optional(),
    followees_added: z.number().optional(),
    followees_removed: z.number().optional(),
    non_followbacks_count: z.number().optional(),
    login_username: z.string().nullable().optional(),
    duration_seconds: z.number().nullable().optional(),
    confidence_score: z.number().nullable().optional(),
    confidence_flag: z.string().nullable().optional(),
  })
);

export const runDetailSchema = z
  .object({
    id: z.number().optional(),
    target_username: z.string().optional(),
    login_username: z.string().optional(),
    timestamp: z.string().optional(),
    followers_count: z.number().optional(),
    followees_count: z.number().optional(),
    non_followbacks_count: z.number().optional(),
    followers_added: z.number().optional(),
    followers_removed: z.number().optional(),
    followees_added: z.number().optional(),
    followees_removed: z.number().optional(),
    duration_seconds: z.number().nullable().optional(),
    followers: z.array(z.string()).optional(),
    followees: z.array(z.string()).optional(),
    non_followbacks: z.array(z.string()).optional(),
    followers_added_list: z.array(z.string()).optional(),
    followers_removed_list: z.array(z.string()).optional(),
    followees_added_list: z.array(z.string()).optional(),
    followees_removed_list: z.array(z.string()).optional(),
    relationship_events: z.array(z.record(z.string(), z.any())).optional(),
  })
  .passthrough();

export const unfollowStatusSchema = z
  .object({
    auth_ready: z.boolean().optional(),
    latest_run: z.record(z.string(), z.any()).nullable().optional(),
    non_followbacks_count: z.number().optional(),
    eligible_count: z.number().optional(),
    already_unfollowed_count: z.number().optional(),
    suggested_max: z.number().nullable().optional(),
    job: z.record(z.string(), z.any()).optional(),
    login_username: z.string().optional(),
    log: z.array(z.string()).optional(),
  })
  .passthrough();

export const unfollowPreviewSchema = z
  .object({
    latest_run: z.record(z.string(), z.any()).nullable().optional(),
    count: z.number().optional(),
    sample: z.array(z.string()).optional(),
    total_non_followbacks: z.number().optional(),
    already_unfollowed_count: z.number().optional(),
    suggested_max: z.number().nullable().optional(),
    login_username: z.string().optional(),
  })
  .passthrough();

export type AppStatus = z.infer<typeof appStatusSchema>;
export type RunStatus = z.infer<typeof runStatusSchema>;
export type RunJobStatus = z.infer<typeof runJobStatusSchema>;
export type RunJobDetail = z.infer<typeof runJobDetailSchema>;
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
export type RunHistoryItem = z.infer<typeof runHistorySchema>[number];
export type RunDetail = z.infer<typeof runDetailSchema>;
export type UnfollowStatus = z.infer<typeof unfollowStatusSchema>;
export type UnfollowPreview = z.infer<typeof unfollowPreviewSchema>;
