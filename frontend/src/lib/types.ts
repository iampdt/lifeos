// Shapes returned by the LifeOS API (backend/app/api).

export type Task = {
  id: string;
  url: string;
  title: string;
  status: string | null;
  priority: string | null;
  area: string | null;
  due: string | null;
  completed: string | null;
  recurring: boolean;
};

export type ProposalCard = {
  id: string;
  kind: string;
  status: string;
  importance: "normal" | "important";
  target: { data_source: string; page_id: string | null; title: string };
  before: Record<string, unknown> | null;
  payload: Record<string, unknown>;
  reason: string;
  rule_ids: string[];
  depends_on: string[];
  expires_at: string;
  /** Scorecard proposals: the workspace's names and units for the habit and metric keys. */
  labels?: Record<string, string>;
  units?: Record<string, string>;
};

export type QuestionRequest = {
  type: "question";
  workflow: string;
  prompt: string;
  tasks: { ref: string; id: string; title: string; status: string | null; priority: string | null; due: string | null }[];
  books: { ref: string; id: string; title: string }[];
  /** The workspace's habits (checkboxes) and metrics (numbers); null without a habits module.
   * `names` is the shape of runs started before habits were generic. */
  habits: {
    fields?: { key: string; name: string; description: string | null }[];
    metrics?: { key: string; name: string; unit: string | null }[];
    names?: string[];
    logged_today: { checks: Record<string, boolean>; metrics?: Record<string, number> } | null;
  } | null;
};

export type OverdueRow = {
  ref: string;
  id: string;
  title: string;
  status: string | null;
  priority: string | null;
  due: string | null;
  recommended: "carry_forward" | "cancel";
  reason: string;
  suggested_due: string | null;
};

export type WeekMetrics = {
  planned: number;
  done: number;
  cancelled: number;
  still_open: number;
  completion_rate: number | null;
  by_area: Record<string, { planned: number; done: number }>;
};

export type HabitSummary = {
  days_logged: number;
  checks: Record<string, number>;
  averages?: Record<string, number>;
};

export type ReviewDraft = {
  summary?: string;
  wins?: string[];
  misses?: string[];
  bottlenecks?: string[];
  priorities?: string[];
  source?: "ai" | "rules";
};

export type DecisionsRequest = {
  type: "decisions";
  workflow: string;
  week: { start: string; end: string; label: string };
  metrics: WeekMetrics;
  habits: { this_week: HabitSummary; last_week: HabitSummary };
  review: ReviewDraft;
  overdue: OverdueRow[];
  inbox: { ref: string; id: string; title: string }[];
};

export type ApprovalRequest = {
  type: "approval";
  workflow: string;
  proposals: ProposalCard[];
  writes_enabled: boolean;
};

export type MonthReviewRequest = {
  type: "month_review";
  workflow: string;
  month: { start: string; end: string; label: string; days: number };
  metrics: WeekMetrics;
  habits: { this_month: HabitSummary; last_month: HabitSummary };
  review: {
    summary?: string;
    wins?: string[];
    misses?: string[];
    patterns?: string[];
    focus?: string[];
    source?: string;
    goals: { title: string; note: string }[];
  };
  closes_page: boolean;
};

export type WaitingFor = QuestionRequest | DecisionsRequest | ApprovalRequest | MonthReviewRequest;

export type RunView = {
  id: string;
  workflow: string;
  status: "running" | "waiting_input" | "waiting_approval" | "completed" | "failed";
  waiting_for: WaitingFor | null;
  summary: Record<string, unknown> | null;
  error: { type: string; message?: string } | null;
  usage: { model: string | null; input_tokens: number; output_tokens: number; llm_latency_ms: number; llm_calls: number };
  trigger: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Approvals = {
  runs: { run_id: string; workflow: string; started_at: string; proposals: ProposalCard[] }[];
  standalone: ProposalCard[];
};

export type DecisionResult = {
  approved: string[];
  rejected: string[];
  errors: Record<string, string>;
  results: { proposal_id: string; outcome: string; detail: string }[];
  writes_enabled: boolean;
};

export type SettingsView = {
  demo: boolean;
  notion_profile: string;
  writes_enabled: boolean;
  timezone: string;
  max_major_per_day: number;
  today: string;
  llm: {
    configured: boolean;
    provider: string | null;
    chains: Record<string, string[]>;
    daily_token_budget: number;
    tokens_today: number;
  };
  modes: Record<string, string>;
};

export type NotionStatus = {
  bot: string | null;
  workspace: string | null;
  data_sources: {
    name: string;
    configured: boolean;
    reachable?: boolean;
    ok?: boolean;
    title?: string;
    error?: string;
    drift?: { field: string; property: string; problem: string; detail: string; blocking: boolean }[];
  }[];
};

export type NotionConnectionView = {
  available: boolean;
  connection: {
    workspace_id: string;
    workspace_name: string | null;
    workspace_icon: string | null;
    status: "active" | "needs_reconnect";
    connected_at: string;
    from_template: boolean;
  } | null;
};

export type DataSourceList = {
  data_sources: { id: string; title: string; database_id: string | null; url: string | null }[];
  truncated: boolean;
};

// --- workspace setup (onboarding) ---------------------------------------------------------

export type FieldRule = { types: string[]; required: boolean; needs: string[]; allows: string[] };
export type ModuleCatalogue = Record<
  string,
  { label: string; required: boolean; free_fields: boolean; fields: Record<string, FieldRule> }
>;
export type SchemaProperty = { name: string; type: string; options: string[] };
export type DataSourceSchema = { title: string; properties: SchemaProperty[] };
export type PropertyMapping = {
  name: string;
  type: string;
  values?: Record<string, string>;
  description?: string | null;
  unit?: string | null;
};
export type ModuleMapping = {
  data_source_id: string;
  database_id?: string | null;
  properties: Record<string, PropertyMapping>;
  date_format?: string | null;
};
export type WorkspaceSettingsView = {
  timezone: string;
  max_major_per_day: number;
  review_weekday: number;
  review_time: string;
  morning_time: string;
  evening_time: string;
  schedule_enabled: boolean;
};
export type WorkspaceView = {
  source: "environment" | "account" | null;
  editable: boolean;
  modules: Record<string, ModuleMapping>;
  settings: WorkspaceSettingsView | null;
  /** Modules the daily drift check left out until the setup is saved again, and why. */
  paused: string[];
  drift: Record<string, string[]> | null;
  checked_at: string | null;
  /** The owner only: its Notion connection could take over the environment's workspace. */
  can_import: boolean;
};
export type WorkspaceSuggestion = {
  data_sources: { id: string; title: string }[];
  modules: Record<string, ModuleMapping>;
  schemas: Record<string, DataSourceSchema>;
  from_template: boolean;
};

/** An account's AI access; the deployment owner gets `{mode: "deployment"}` only. */
export type AIView = {
  mode: "deployment" | "own_key" | "allowance" | "allowance_used" | "off";
  service_available?: boolean;
  consent?: { given: boolean; outdated: boolean; version: string; text: string };
  allowance?: { tokens: number; used_today: number };
  own_key?: { available: boolean; hint: string | null; status: string | null; tokens_today: number };
};

export type UsageSummary = {
  runs: number;
  statuses: Record<string, number>;
  success_rate: number | null;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  llm_latency_ms: { p50: number | null; p95: number | null };
  duration_ms: { p50: number | null };
};

export type UsageReport = {
  days: number;
  totals: UsageSummary;
  workflows: Record<string, UsageSummary>;
  by_day: Record<string, { runs: number; tokens: number }>;
  models: Record<string, number>;
};


export type GoalView = {
  id: string;
  url: string;
  title: string;
  type: string | null;
  status: string | null;
  state: "active" | "planned" | "done" | "dropped" | null;
  area: string | null;
  current: number | null;
  target: number | null;
  progress: number | null;
  success_metric: string;
  deadline: string | null;
  open_tasks: { id: string; title: string; status: string | null; due: string | null; url: string }[];
};

export type GoalsView = { available: boolean; tasks_linked?: boolean; goals: GoalView[] };

export type BookView = {
  id: string;
  url: string;
  title: string;
  author: string;
  status: string | null;
  state: "reading" | "want" | "finished" | "abandoned" | null;
  pages: number | null;
  pages_read: number | null;
  progress: number | null;
  started: string | null;
  finished: string | null;
};

export type ReadingView = { available: boolean; books: BookView[] };

export type HistoryRun = {
  id: string;
  status: string;
  started_at: string;
  summary: Record<string, unknown> | null;
};

export type HistoryDay = { day: string; briefing: HistoryRun | null; checkin: HistoryRun | null };
