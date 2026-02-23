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

export type AppStatus = z.infer<typeof appStatusSchema>;
export type ReconHealth = z.infer<typeof reconHealthSchema>;
export type ReconHistoryItem = z.infer<typeof reconHistoryItemSchema>;
export type ReconQueue = z.infer<typeof reconQueueSchema>;
export type ReconJobResult = z.infer<typeof reconJobResultSchema>;
