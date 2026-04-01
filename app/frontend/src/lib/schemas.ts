import { z } from "zod";

export const appStatusSchema = z.object({
  status: z.string().optional(),
  state: z.string().optional(),
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
    latest: z
      .object({
        timestamp: z.string().optional(),
        followers_count: z.number().optional(),
        followees_count: z.number().optional(),
        followers_added: z.number().optional(),
        followers_removed: z.number().optional(),
        followees_added: z.number().optional(),
        followees_removed: z.number().optional(),
      })
      .partial()
      .optional(),
  })
);

export const scheduleSchema = z.array(
  z.object({
    id: z.number().optional(),
    login_username: z.string().optional(),
    target_username: z.string().optional(),
    interval: z.string().optional(),
    mode: z.enum(["full_run", "count_watch"]).optional(),
    trigger_delta: z.number().optional(),
    schedule_kind: z.enum(["cron", "daily", "weekly", "every_n_days"]).optional(),
    schedule_time: z.string().nullable().optional(),
    schedule_weekday: z.number().nullable().optional(),
    schedule_interval_days: z.number().nullable().optional(),
    schedule_start_date: z.string().nullable().optional(),
    schedule_label: z.string().optional(),
    next_run: z.string().nullable().optional(),
  })
);

export const countWatchSampleSchema = z.array(
  z.object({
    id: z.number().optional(),
    timestamp: z.string().optional(),
    followers_count: z.number().optional(),
    followees_count: z.number().optional(),
    login_username: z.string().optional(),
    triggered_full_run: z.union([z.boolean(), z.number()]).optional(),
    triggered_run_id: z.number().nullable().optional(),
    schedule_id: z.number().nullable().optional(),
    trigger_delta: z.number().nullable().optional(),
    created_at: z.string().optional(),
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
});

export const configSchema = z.object({
  config: configValuesSchema,
  defaults: configValuesSchema.optional(),
  meta: z.record(z.string(), z.object({
    source: z.string().optional(),
    updated_at: z.string().nullable().optional(),
    updated_by: z.string().nullable().optional(),
  })).optional(),
});

export const uiEvidenceItemSchema = z.object({
  id: z.number().nullable().optional(),
  observed_at: z.string().optional(),
  target_username: z.string().optional(),
  counterparty_username: z.string().optional(),
  relation_type: z.string().optional(),
  event_type: z.string().optional(),
  action: z.string().optional(),
  direction: z.string().optional(),
  actor_username: z.string().optional(),
  object_username: z.string().optional(),
  sentence: z.string().optional(),
  login_username: z.string().optional(),
  run_id: z.number().nullable().optional(),
});

export const uiEvidenceSchema = z.object({
  items: z.array(uiEvidenceItemSchema),
});

export const uiTargetItemSchema = z.object({
  target_username: z.string().optional(),
  followers_count: z.number().nullable().optional(),
  following_count: z.number().nullable().optional(),
  non_followbacks_count: z.number().nullable().optional(),
  last_full_run_at: z.string().nullable().optional(),
  last_change_at: z.string().nullable().optional(),
  next_check_at: z.string().nullable().optional(),
  latest_event: uiEvidenceItemSchema.nullable().optional(),
});

export const uiTargetsSchema = z.object({
  items: z.array(uiTargetItemSchema),
});

export const uiTargetTimelineItemSchema = z.object({
  target_username: z.string().optional(),
  observed_at: z.string().optional(),
  run_id: z.number().nullable().optional(),
  login_username: z.string().optional(),
  event_count: z.number().optional(),
  followers_added_count: z.number().optional(),
  followers_removed_count: z.number().optional(),
  following_added_count: z.number().optional(),
  following_removed_count: z.number().optional(),
  followers_added_sample: z.array(z.string()).optional(),
  followers_removed_sample: z.array(z.string()).optional(),
  following_added_sample: z.array(z.string()).optional(),
  following_removed_sample: z.array(z.string()).optional(),
});

export const uiTargetTimelineSchema = z.object({
  items: z.array(uiTargetTimelineItemSchema),
});

export const uiTargetChangeItemSchema = uiEvidenceItemSchema.extend({
  first_seen_at: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  departed_at: z.string().nullable().optional(),
});

export const uiTargetChangesSchema = z.object({
  items: z.array(uiTargetChangeItemSchema),
});

export const uiSystemHealthSchema = z.object({
  state: z.string().optional(),
  collectors: z.array(
    z.object({
      login_username: z.string().optional(),
      status: z.string().optional(),
      private_session_exists: z.boolean().optional(),
      has_password: z.boolean().optional(),
      has_totp_seed: z.boolean().optional(),
      session_fail_streak: z.number().optional(),
      session_marked_stale: z.boolean().optional(),
      two_factor_method: z.string().nullable().optional(),
      last_login_at: z.string().nullable().optional(),
      last_error: z.string().nullable().optional(),
      auth_last_event: z.string().nullable().optional(),
      auth_last_event_at: z.string().nullable().optional(),
      cooldown_seconds: z.number().nullable().optional(),
    })
  ).optional(),
  active_jobs: z.array(z.record(z.string(), z.any())).optional(),
  queued_jobs: z.array(z.record(z.string(), z.any())).optional(),
  manual_actions: z.array(z.record(z.string(), z.any())).optional(),
  cooldowns: z.array(z.record(z.string(), z.any())).optional(),
  schedules: scheduleSchema.optional(),
});

export const uiBriefSchema = z.object({
  state: z.string().optional(),
  attention_collectors: uiSystemHealthSchema.shape.collectors.optional(),
  recent_evidence: z.array(uiEvidenceItemSchema).optional(),
  active_jobs: z.array(z.record(z.string(), z.any())).optional(),
  queued_jobs: z.array(z.record(z.string(), z.any())).optional(),
  targets: z.array(uiTargetItemSchema).optional(),
  next_checks: z.array(uiTargetItemSchema).optional(),
});

export const uiNetworkItemSchema = z.object({
  target_username: z.string().optional(),
  username: z.string().optional(),
  relationship_state: z.enum(["mutual", "they_follow", "subject_follows", "disconnected"]).optional(),
  relationship_label: z.string().optional(),
  actor_follows_subject: z.boolean().optional(),
  subject_follows_actor: z.boolean().optional(),
  first_seen_at: z.string().nullable().optional(),
  latest_interaction_at: z.string().nullable().optional(),
  departed_at: z.string().nullable().optional(),
  latest_event: uiEvidenceItemSchema.nullable().optional(),
});

export const uiNetworkSchema = z.object({
  items: z.array(uiNetworkItemSchema),
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
      auto_set_runner: z.boolean().optional(),
      message: z.string().nullable().optional(),
      last_url: z.string().nullable().optional(),
      saved_login: z.boolean().optional(),
      warmup_target_username: z.string().nullable().optional(),
      queue_warmup_run: z.boolean().optional(),
      warmup_job_id: z.string().nullable().optional(),
      schedule_interval: z.string().nullable().optional(),
      schedule_id: z.number().nullable().optional(),
      warnings: z.array(z.string()).optional(),
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
export type CountWatchSampleItem = z.infer<typeof countWatchSampleSchema>[number];
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
export type UiEvidenceItem = z.infer<typeof uiEvidenceItemSchema>;
export type UiTargetItem = z.infer<typeof uiTargetItemSchema>;
export type UiTargetChangeItem = z.infer<typeof uiTargetChangeItemSchema>;
export type UiNetworkItem = z.infer<typeof uiNetworkItemSchema>;
