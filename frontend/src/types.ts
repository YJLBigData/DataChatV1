export interface BootstrapInfo {
  service: string;
  version: string;
  data_range: [string, string];
  default_user: string;
  metrics_count: number;
  dimensions_count: number;
  tables_count: number;
  suggestions: string[];
  model: { provider: string; name: string };
}

export interface PlanFilter { dimension: string; op: string; values: string[]; raw?: string }
export interface QueryPlan {
  metric: string;
  table: string;
  group_by: string[];
  filters: PlanFilter[];
  time_range: { kind: string; period: string; n: number; year: string; months: string[]; start_ym: string; end_ym: string };
  calculation: string;
  order_by: { field: string; dir: string }[];
  limit: number;
  needs_clarify: boolean;
  clarify_reason: string;
  clarify_options: { type?: string; key?: string; label?: string; hint?: string }[];
  confidence: number;
  reasoning: string;
}

export interface DisplayColumn {
  key: string;
  label: string;
  kind: string;       // 'metric' | 'dimension' | 'time' | 'value'
  unit: string;
  format: string;
  decimals: number;
}

export interface AnswerTable {
  columns: string[];
  rows: any[][];
  display_columns: DisplayColumn[];
  display_rows: string[][];
  row_count: number;
  elapsed_ms: number;
}

export interface AnswerChart {
  type: string;       // 'bar' | 'line' | 'pie' | 'single_value' | 'none'
  x?: string;
  series?: string[];
  metric?: string;
  orientation?: 'vertical' | 'horizontal';
}

export interface MetricDefinition {
  name: string;
  label: string;
  expression: string;
  table: string;
  unit: string;
  domain: string;
  description: string;
}

export interface AnswerExplain {
  used_tables: string[];
  metric_definition: MetricDefinition;
  filters_applied: PlanFilter[];
  group_by: string[];
  time_range: any;
  calculation: string;
  sql: string;
  row_count: number;
  elapsed_ms: number;
  reasoning: string;
  confidence: number;
}

export interface AnswerPayload {
  needs_clarify?: boolean;
  narrative: string;
  highlights: string[];
  risk_notes: string[];
  table: AnswerTable;
  chart: AnswerChart;
  suggestions: string[];
  clarify_options?: { type?: string; key?: string; label?: string; hint?: string }[];
  explainability: AnswerExplain;
}

export interface SmartQDataset {
  cube_id: string;
  name: string;
  theme: string;
}

export interface SmartQDatasetResult {
  cube_id: string;
  cube_name: string;
  success: boolean;
  sql: string;
  chart_type: string;
  data: any[][];
  answer?: AnswerPayload;
  summary?: string;
  error?: string | null;
}

export interface SmartQMeta {
  mode?: "single_dataset" | "native_multi_dataset" | "multi_dataset" | string;
  native_multi_supported?: boolean | null;
  native_error?: string;
  summary?: string;
  results?: SmartQDatasetResult[];
}

export interface ChatResult {
  trace_id: string;
  conversation_id: string;
  question: string;
  answer: AnswerPayload;
  plan: QueryPlan;
  sql: string;
  rows: number;
  cached: boolean;
  elapsed_ms: number;
  smartq?: SmartQMeta;
}

export interface StageEvent {
  stage: string;          // session | cache | retrieval | plan | compile | guard | execute | answer | clarify
  status: string;         // ok | miss | hit | error | cache_hit
  payload: Record<string, any>;
  elapsed_ms: number;
  timestamp: string;
}

export interface ConversationMeta {
  id: string;
  title: string;
  user_id?: string;
  created_at: number;
  updated_at: number;
}

export interface ConversationDetail extends ConversationMeta {
  messages: { id: string; role: string; content: string; payload: any; created_at: number }[];
}

export interface ChatTurn {
  id: string;
  question: string;
  pending: boolean;
  events: StageEvent[];
  result?: ChatResult;
  error?: string;
}

/* =========================================================================
 * Chart switcher
 * 13 种通用图表 + 列表，由前端按数据形状自动判定可用集，按钮高亮/置灰。
 * ========================================================================= */
export type ChartMode =
  | "table"          // 列表（默认）
  | "kpi"            // 指标卡
  | "bar"            // 柱图
  | "bar_horizontal" // 条形图
  | "line"           // 折线图
  | "area"           // 面积图
  | "stacked_bar"    // 堆叠柱图
  | "dual_axis"      // 双轴图
  | "pie"            // 饼图
  | "rose"           // 玫瑰图
  | "funnel"         // 漏斗图
  | "scatter"        // 散点图
  | "heatmap"        // 热力图
  | "map";           // 地图

/* =========================================================================
 * Auth + admin
 * ========================================================================= */
export interface AuthUser {
  id: string;
  username: string;
  role: string;
  created_at?: number;
  email?: string;
  must_change_password?: boolean;
  is_active?: boolean;
}

export interface QueryLogEntry {
  id: string;
  trace_id: string;
  user_id: string;
  username: string;
  conversation_id: string;
  question: string;
  metric: string;
  table: string;
  sql: string;
  rows: number;
  elapsed_ms: number;
  cached: boolean;
  needs_clarify: boolean;
  status: "ok" | "clarify" | "error";
  error: string;
  plan: any;
  created_at: number;
}

export interface SemanticOverview {
  data_range: [string, string];
  metrics: { name: string; label: string; table: string; domain: string; unit: string; description: string }[];
  dimensions: { name: string; label: string; tables: string[]; samples: string[] }[];
  tables: { name: string; label: string; schema: string; grain: string; description: string }[];
  calculations: { name: string; label: string; aliases: string[]; formula: string }[];
}

export interface PermissionsAllItem {
  user_id: string;
  username: string;
  role: string;
  row_rules: Record<string, string[]>;
  allowed_tables: string[];
  allowed_columns: Record<string, string[]>;
  deny_by_default: boolean;
}

export interface ReportTemplate {
  id: string;
  name: string;
  prompt: string;
  is_default: boolean;
  created_at: number;
  updated_at: number;
}

export interface Folder {
  id: string;
  name: string;
  color: string;
  created_at: number;
}

export interface SemanticEntityMap {
  items: Record<string, any>;
}

export interface SemanticProposal {
  table_proposal: {
    label: string;
    description: string;
    grain: string;
    time_field?: string;
    time_field_year?: string;
    time_field_month?: string;
    primary_dimensions: string[];
    measures: string[];
  };
  dimensions: Record<string, any>;
  metrics: Record<string, any>;
}

export type PageId = "chat" | "expert" | "users" | "logs" | "semantic" | "permissions" | "report_templates" | "llm_settings";

/* ---------- 专家团（expert-team） ---------- */
export interface ExpertCard {
  id: string;
  name: string;
  profession: string;
  emoji: string;
  skills: string[];
  is_director: boolean;
  is_builtin: boolean;
  has_override?: boolean;
  deletable?: boolean;
  deleted?: boolean;
}

export interface ExpertMemberDetail extends ExpertCard {
  instructions: string;
  methodology?: string;
  default?: { name: string; profession: string; emoji: string; instructions: string };
}

export interface ExpertWorkflow { name: string; trigger: string; flow: string }

export interface ExpertTeamBootstrap {
  director: ExpertCard | null;
  experts: ExpertCard[];
  user_skills: ExpertCard[];
  knowledge_files: string[];
  workflows: ExpertWorkflow[];
}

export interface ExpertContribution {
  id: string;
  name: string;
  profession: string;
  emoji: string;
  subtask: string;
  analysis: string;
  data?: { narrative?: string; sql?: string; rows?: number; table_preview?: string; smartq?: SmartQMeta };
}

export interface ExpertTeamResult {
  ok: boolean;
  error?: string;
  route: string;
  plan: string;
  experts: ExpertContribution[];
  report: string;
  elapsed_ms: number;
  /** 部分环节降级提示（某专家取数/分析未成功，但整体仍出了结论）。 */
  warnings?: string[];
  events?: { stage: string; [k: string]: any }[];
}

/** 专家团一轮对话（前端 UI 态）：问题 + 后台 job 进度 / 最终结果。 */
export interface ExpertTurn {
  id: string;
  question: string;
  pending: boolean;
  jobId?: string;
  events?: { stage: string; [k: string]: any }[];
  result?: ExpertTeamResult;
  error?: string;
}

/** 数据导出 job（XLSX 异步导出队列）。 */
export interface ExportJob {
  id: string;
  question: string;
  status: "queued" | "running" | "ready" | "error" | "expired";
  filename: string;
  row_count: number;
  truncated: boolean;
  error: string;
  created_at: number;
  updated_at: number;
  expires_at: number;
}

/** 轮询 GET /api/expert-team/jobs/{id} 的返回。 */
export interface ExpertJob {
  ok: boolean;
  status?: "running" | "done" | "error" | "missing";
  error?: string;
  conversation_id?: string;
  events?: { stage: string; [k: string]: any }[];
  result?: ExpertTeamResult | null;
}

export interface LLMSettingItem {
  value: string;
  is_secret: boolean;
  is_set: boolean;
  source: "db" | "env" | "default";
}
export interface LLMSettingsResp {
  settings: Record<string, LLMSettingItem>;
}

export interface LLMPreset {
  id: string;
  name: string;
  provider: "bailian" | "feihe";
  api_key: string;          // masked
  api_key_set: boolean;
  base_url: string;
  model: string;
  embed_model: string;
  is_default: boolean;
  is_active: boolean;
  last_tested_at: number;
  last_test_ok: boolean;
  last_test_response: string;
  created_at: number;
  updated_at: number;
}
export interface LLMPresetTestResult {
  ok: boolean;
  latency_ms: number;
  text?: string;
  error?: string;
  model_echo?: string;
  prompt?: string;
}
