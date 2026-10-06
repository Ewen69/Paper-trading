/**
 * Runtime schemas for every backend payload. A payload that doesn't match is an error that the UI
 * shows as "No data". We never patch over it with defaults.
 */
import { z } from 'zod';

const isoDateTime = z.iso.datetime({ offset: true }).transform((s) => new Date(s));
const isoDate = z.iso.date();

export const dataTypeSchema = z.enum(['real-time', 'delayed', 'end-of-day', 'manual']);
export type DataType = z.infer<typeof dataTypeSchema>;

export const provenanceSchema = z.object({
  source: z.string(),
  as_of: isoDateTime,
  data_type: dataTypeSchema,
  stale: z.boolean(),
  stale_reason: z.string().nullable(),
});
export type Provenance = z.infer<typeof provenanceSchema>;

export const healthSchema = z.object({
  status: z.literal('ok'),
  version: z.string(),
  trading_mode: z.literal('paper'),
  broker_base_url: z.string(),
  broker_credentials_configured: z.boolean(),
  source: z.string(),
  as_of: isoDateTime,
});
export type Health = z.infer<typeof healthSchema>;

const severitySchema = z.enum(['error', 'warning', 'info']);
export type Severity = z.infer<typeof severitySchema>;
const statusSchema = z.enum(['ok', 'warning', 'error']);
export type Status = z.infer<typeof statusSchema>;

export const issueSchema = z.object({
  check: z.string(),
  severity: severitySchema,
  symbol: z.string(),
  count: z.number().int(),
  first_date: isoDate,
  last_date: isoDate,
  detail: z.string(),
});
export type Issue = z.infer<typeof issueSchema>;

const feedSchema = z.object({ name: z.string(), data_type: dataTypeSchema, note: z.string() });
export type Feed = z.infer<typeof feedSchema>;

export const dataHealthSchema = z.object({
  overall: z.enum(['ok', 'warning', 'error', 'no data']),
  as_of: isoDateTime,
  source: z.string(),
  market_open: z.boolean(),
  last_completed_session: isoDate,
  live_source: z.object({
    name: z.string(),
    status: statusSchema,
    configured: z.boolean(),
    reachable: z.boolean().nullable(),
    account_status: z.string().nullable(),
    error: z.string().nullable(),
    checked_at: isoDateTime,
    stock_feed: feedSchema,
    option_feed: feedSchema,
  }),
  datasets: z.array(
    z.object({
      id: z.number().int(),
      kind: z.enum(['equity_bars', 'option_quotes']),
      file_name: z.string(),
      row_count: z.number().int(),
      symbol_count: z.number().int(),
      coverage_start: isoDate,
      coverage_end: isoDate,
      status: statusSchema,
      issues: z.array(issueSchema),
      provenance: provenanceSchema,
    }),
  ),
});
export type DataHealth = z.infer<typeof dataHealthSchema>;
export type Dataset = DataHealth['datasets'][number];

export const quoteSchema = z.object({
  symbol: z.string(),
  bid: z.number(),
  ask: z.number(),
  spread: z.number(),
  bid_size: z.number(),
  ask_size: z.number(),
  provenance: provenanceSchema,
  issues: z.array(issueSchema),
});
export type Quote = z.infer<typeof quoteSchema>;
