export type JobStatus = "new" | "ranked" | "skipped" | "expired" | "evaluated" | string;

export type Job = {
  key: string;
  title?: string;
  company?: string;
  url?: string;
  location?: string;
  first_seen?: string;
  posting_date?: string | null;
  status?: JobStatus;
  fit?: string;
  portal?: string;
  rank_score?: number;
  rank_verdict?: string;
  note?: string;
  eligibility?: string;
  strengths?: string[];
  gaps?: string[];
  application_status?: string;
  application_folder?: string;
  cv_pdf?: string | null;
  cover_pdf?: string | null;
  queued?: boolean;
  tracker_status?: string | null;
  deadline?: string | null;
  [extra: string]: unknown;
};

export type TrackerRow = {
  index?: number;
  date?: string;
  company?: string;
  sector?: string;
  role?: string;
  role_type?: string;
  channel?: string;
  status?: string;
  contact_person?: string;
  fit_rating?: string;
  notes?: string;
  cv_file?: string;
  cover_letter_file?: string;
  source?: string;
  days_quiet?: number | null;
  followups_sent?: number;
  followup_due?: boolean;
  stage?: string;
  last_contact?: string | null;
  next_followup?: string | null;
  followup_in_days?: number | null;
  folder?: string | null;
  outcome_status?: string | null;
};

export type QueueItem = {
  url: string;
  company: string;
  title: string;
  queued_at: string;
};

export type AppFile = {
  name: string;
  path: string;
  kind: string;
};

export type AppFolder = {
  slug: string;
  path?: string | null;
  files: AppFile[];
  outcome_status?: string | null;
  kind?: string;
  company?: string;
  title?: string;
  key?: string | null;
};

export type TodayPick = {
  key: string;
  title?: string;
  company?: string;
  location?: string;
  url?: string;
  fit?: string;
  rank_score?: number | null;
  first_seen?: string;
};

export type TodaySummary = {
  followups: TrackerRow[];
  ready: AppFolder[];
  queued: QueueItem[];
  picks: TodayPick[];
  counts: { followups: number; ready: number; queued: number; badge: number };
};

export type ApplicationsPayload = {
  folders: AppFolder[];
  drafts: AppFile[];
  open: TrackerRow[];
  prepared: AppFolder[];
  followup_due: number;
};

export type ScrapeEvent = {
  type: string;
  message?: string;
  portal?: string;
  query?: string;
  added?: number;
  existing?: number;
  errors?: number;
  calls?: number;
  error?: string | null;
};

export type ScrapePlan = {
  jobage: number;
  limit: number;
  portals: string[];
  calls: { portal: string; query: string }[];
};

export type QueueRunEvent = { id: number; ts: string; message: string };

export type QueueRunResult = {
  url: string;
  company?: string | null;
  title?: string | null;
  status: string;
  score?: number | null;
  eligibility?: string | null;
  reason?: string;
  warnings?: string[];
  cost_usd?: number;
  /** Set when the run also fills forms: what happened to this job's employer form. */
  form?: { status: string; reason?: string };
};

export type QueueRunStatus = {
  running: boolean;
  stop_requested: boolean;
  fill_forms?: boolean;
  started_at: string | null;
  finished_at: string | null;
  current: { company?: string | null; title?: string | null; url: string } | null;
  results: QueueRunResult[];
  message: string;
  events: QueueRunEvent[];
  last_id: number;
};

export type QueueConfig = {
  score_floor: number;
  skip_rules: boolean;
  per_job_budget_usd: number;
  evaluation_budget_usd: number;
  per_job_timeout_minutes: number;
  max_consecutive_failures: number;
  auto_queue: {
    enabled: boolean;
    per_run_limit: number;
    max_queue_size: number;
    min_fit: string;
    retry_after_days: number;
    verified_eligibility_only: boolean;
  };
};
