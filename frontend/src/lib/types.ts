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
};

export type QuestionRequest = {
  type: "question";
  workflow: string;
  prompt: string;
  tasks: { ref: string; id: string; title: string; status: string | null; priority: string | null; due: string | null }[];
  books: { ref: string; id: string; title: string }[];
  habits: { names: string[]; logged_today: { checks: Record<string, boolean> } | null };
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
  avg_sleep: number | null;
  avg_screen_time: number | null;
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

export type WaitingFor = QuestionRequest | DecisionsRequest | ApprovalRequest;

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
  notion_profile: string;
  writes_enabled: boolean;
  timezone: string;
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
