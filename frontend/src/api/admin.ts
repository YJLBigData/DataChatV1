/** 管理端 API —— 用户管理、审计日志、语义层、数据权限、LLM 设置/预设、诊断。 */
import type {
  AuthUser,
  LLMPreset,
  LLMPresetTestResult,
  LLMSettingsResp,
  PermissionsAllItem,
  QueryLogEntry,
  SemanticOverview,
} from "../types";
import { jsonReq } from "./http";

export const adminApi = {
  adminDiagnostics: () => jsonReq<any>("/api/admin/diagnostics"),

  /* 用户管理 */
  listUsers: () => jsonReq<{ items: AuthUser[] }>("/api/admin/users"),
  createUser: (
    username: string,
    password: string | null,
    role: string = "user",
    email: string = "",
    must_change_password: boolean = true,
  ) =>
    jsonReq<AuthUser & { one_time_password?: string }>("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ username, password: password || undefined, role, email, must_change_password }),
    }),
  deleteUser: (username: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" }),
  setUserActive: (username: string, is_active: boolean) =>
    jsonReq<{ ok: boolean; username: string; is_active: boolean }>(
      `/api/admin/users/${encodeURIComponent(username)}/active`,
      { method: "POST", body: JSON.stringify({ is_active }) },
    ),
  resetPassword: (username: string, new_password: string | null = null, must_change_password: boolean = true) =>
    jsonReq<{ ok: boolean; one_time_password?: string }>(
      `/api/admin/users/${encodeURIComponent(username)}/password`,
      {
        method: "POST",
        body: JSON.stringify({ new_password: new_password || undefined, must_change_password }),
      },
    ),

  /* 审计日志 */
  listLogs: (params: { limit?: number; offset?: number; username?: string; status?: string; keyword?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    if (params.username) qs.set("username", params.username);
    if (params.status) qs.set("status", params.status);
    if (params.keyword) qs.set("keyword", params.keyword);
    return jsonReq<{ items: QueryLogEntry[]; total: number; limit: number; offset: number }>(
      `/api/admin/logs?${qs.toString()}`,
    );
  },

  /* 语义层 */
  semanticOverview: () => jsonReq<SemanticOverview>("/api/semantic/overview"),
  semanticGet: () => jsonReq<{ path: string; content: string; bytes: number }>("/api/admin/semantic"),
  semanticPut: (content: string) =>
    jsonReq<{ ok: boolean; metrics: number; dimensions: number; tables: number }>("/api/admin/semantic", {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  // #15：保存前校验（dry-run）/ 历史版本 / 回滚
  semanticValidate: (content: string) =>
    jsonReq<{ ok: boolean; errors: string[]; summary: Record<string, number> }>("/api/admin/semantic/validate", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  semanticVersions: () =>
    jsonReq<{ items: { id: string; bytes: number; mtime: number }[] }>("/api/admin/semantic/versions"),
  semanticVersionContent: (vid: string) =>
    jsonReq<{ id: string; content: string }>(`/api/admin/semantic/versions/${encodeURIComponent(vid)}`),
  semanticRollback: (vid: string) =>
    jsonReq<{ ok: boolean; metrics: number; dimensions: number; tables: number; rolled_back_to: string }>(
      `/api/admin/semantic/rollback/${encodeURIComponent(vid)}`,
      { method: "POST" },
    ),

  /* 数据权限 */
  listPermissions: () => jsonReq<{ items: PermissionsAllItem[] }>("/api/admin/permissions"),
  getPermissions: (user_id: string) =>
    jsonReq<{ user_id: string; row_rules: Record<string,string[]>; allowed_tables: string[]; allowed_columns: Record<string,string[]>; deny_by_default: boolean }>(`/api/admin/permissions/${encodeURIComponent(user_id)}`),
  putPermissions: (
    user_id: string,
    payload: { row_rules?: Record<string,string[]>; allowed_tables?: string[]; allowed_columns?: Record<string,string[]>; deny_by_default?: boolean },
  ) =>
    jsonReq<{ ok: boolean }>(`/api/admin/permissions/${encodeURIComponent(user_id)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  /* 语义层 CRUD + 自动分析 */
  semanticEntities: (kind: "tables"|"dimensions"|"metrics") =>
    jsonReq<{ items: Record<string, any> }>(`/api/admin/semantic/${kind}`),
  semanticUpsert: (kind: "tables"|"dimensions"|"metrics", name: string, body: any) =>
    jsonReq<{ ok: boolean }>(`/api/admin/semantic/${kind}/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ name, body }),
    }),
  semanticDelete: (kind: "tables"|"dimensions"|"metrics", name: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/semantic/${kind}/${encodeURIComponent(name)}`, { method: "DELETE" }),
  semanticAnalyze: (table: string) =>
    jsonReq<{ ok: boolean; proposal?: any; user_message?: string }>("/api/admin/semantic/analyze", {
      method: "POST",
      body: JSON.stringify({ table }),
    }),

  /* 语义认证工作流（机器起草 → 人工认证） */
  semanticCertification: () =>
    jsonReq<{ kinds: Record<string, { name: string; label: string; status: "draft" | "verified" }[]>;
              stats: { draft: number; verified: number } }>("/api/admin/semantic/certification"),
  semanticSetStatus: (kind: "tables"|"dimensions"|"metrics", name: string, status: "draft"|"verified") =>
    jsonReq<{ ok: boolean }>(`/api/admin/semantic/${kind}/${encodeURIComponent(name)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  /* LLM 设置（热改 + 持久化）。入参：null=不动，""=清除回退 env/默认，非空=写入 DB */
  adminGetLLMSettings: () =>
    jsonReq<LLMSettingsResp>("/api/admin/llm-settings"),
  adminPutLLMSettings: (req: {
    DASHSCOPE_API_KEY?: string | null;
    DASHSCOPE_BASE_URL?: string | null;
    DASHSCOPE_MODEL?: string | null;
    DASHSCOPE_EMBED_MODEL?: string | null;
    LLM_PROVIDER?: string | null;
  }) =>
    jsonReq<{ ok: boolean; updated: string[]; version: number }>("/api/admin/llm-settings", {
      method: "PUT",
      body: JSON.stringify(req),
    }),

  /* 多套 LLM 预设（preset） */
  adminListLLMPresets: () =>
    jsonReq<{ items: LLMPreset[] }>("/api/admin/llm-presets"),
  adminTestLLMPresetCandidate: (req: {
    provider: "bailian" | "feihe";
    api_key?: string;
    base_url?: string;
    model: string;
    prompt?: string;
    preset_id?: string;   // 编辑时"旧 AK + 当前草稿字段"合并测试用
  }) =>
    jsonReq<LLMPresetTestResult>("/api/admin/llm-presets/test", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  adminCreateLLMPreset: (req: {
    name: string;
    provider: "bailian" | "feihe";
    api_key?: string;
    base_url?: string;
    model: string;
    embed_model?: string;
  }) =>
    jsonReq<{ ok: boolean; preset: LLMPreset }>("/api/admin/llm-presets", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  adminUpdateLLMPreset: (id: string, req: {
    name?: string;
    provider?: "bailian" | "feihe";
    api_key?: string | null;
    base_url?: string | null;
    model?: string;
    embed_model?: string | null;
    is_active?: boolean;
  }) =>
    jsonReq<{ ok: boolean; preset: LLMPreset }>(`/api/admin/llm-presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(req),
    }),
  adminDeleteLLMPreset: (id: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/llm-presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  adminSetDefaultLLMPreset: (id: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/llm-presets/${encodeURIComponent(id)}/set-default`, { method: "POST" }),
  adminTestExistingLLMPreset: (id: string) =>
    jsonReq<LLMPresetTestResult>(`/api/admin/llm-presets/${encodeURIComponent(id)}/test`, { method: "POST" }),
};
