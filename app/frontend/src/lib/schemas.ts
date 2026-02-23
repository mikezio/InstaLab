import { z } from "zod";

export const appStatusSchema = z.object({
  status: z.string().optional(),
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
    cron_expr: z.string().optional(),
    enabled: z.boolean().optional(),
    run_login_mode: z.string().optional(),
  })
);

export const loginSchema = z.array(
  z.object({
    login_username: z.string().optional(),
    has_password: z.boolean().optional(),
    has_totp_seed: z.boolean().optional(),
    private_session_exists: z.boolean().optional(),
    blocked: z.boolean().optional(),
  })
);

export const configSchema = z.object({
  recon_enabled: z.boolean().optional(),
  recon_max_concurrency: z.number().optional(),
  recon_queue_limit: z.number().optional(),
  recon_timeout_seconds: z.number().optional(),
  run_login_mode_default: z.string().optional(),
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
export type ReconHealth = z.infer<typeof reconHealthSchema>;
export type ReconHistoryItem = z.infer<typeof reconHistoryItemSchema>;
export type ReconQueue = z.infer<typeof reconQueueSchema>;
export type ReconJobResult = z.infer<typeof reconJobResultSchema>;
export type TargetSummaryItem = z.infer<typeof targetsSummarySchema>[number];
export type ScheduleItem = z.infer<typeof scheduleSchema>[number];
export type LoginItem = z.infer<typeof loginSchema>[number];
export type ConfigPayload = z.infer<typeof configSchema>;
export type RelationshipEvent = z.infer<typeof relationshipEventSchema>[number];
export type RelationshipHistoryRow = z.infer<typeof relationshipHistorySchema>[number];
