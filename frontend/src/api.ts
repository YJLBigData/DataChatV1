/**
 * 前端 API 客户端 — 全部走 fetch + Bearer Token，401 自动登出。
 *
 * 命名空间：
 *   auth.*       本地 token / 用户缓存
 *   api.*        REST 调用 + SSE 流
 */
import type {
  AuthUser,
  BootstrapInfo,
  ChatResult,
  ConversationDetail,
  ConversationMeta,
  PermissionsAllItem,
  QueryLogEntry,
  SemanticOverview,
  StageEvent,
} from "./types";

const BASE = "";
const TOKEN_KEY = "datachat.token";
const USER_KEY = "datachat.user";

export const auth = {
  getToken(): string { return localStorage.getItem(TOKEN_KEY) || ""; },
  setToken(token: string) { localStorage.setItem(TOKEN_KEY, token); },
  getUser(): AuthUser | null {
    const raw = localStorage.getItem(USER_KEY);
    try { return raw ? (JSON.parse(raw) as AuthUser) : null; } catch { return null; }
  },
  setUser(u: AuthUser) { localStorage.setItem(USER_KEY, JSON.stringify(u)); },
  clear() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); },
};

/**
 * 把 fetch / 解析异常翻译成对用户友好的中文文案。
 * 绝不向用户暴露 "Failed to fetch" / "body stream already read" 等技术细节。
 */
export function friendlyError(e: any): string {
  const m = String((e && e.message) || e || "");
  if (e?.name === "TypeError" || /failed to fetch|networkerror|load failed|err_|econn/i.test(m)) {
    return "无法连接服务器，请检查网络或联系管理员。";
  }
  if (/body stream already read|already read|response\.json|response\.text/i.test(m)) {
    return "服务暂时不可用，请稍后重试或联系管理员。";
  }
  return m || "请求失败，请稍后重试。";
}

/** 从后端响应体中提取对用户友好的提示（兼容 user_message / detail 字符串或对象）。 */
function pickServerMessage(body: any): string {
  if (!body || typeof body !== "object") return "";
  const withTrace = (msg: string) =>
    body.trace_id ? `${msg}（trace_id: ${String(body.trace_id).slice(0, 8)}）` : msg;
  if (typeof body.user_message === "string" && body.user_message) return withTrace(body.user_message);
  const d = body.detail;
  if (typeof d === "string" && d) return d;
  if (d && typeof d === "object") {
    if (typeof d.user_message === "string" && d.user_message) return d.user_message;
    if (typeof d.message === "string" && d.message) return d.message;
  }
  if (typeof body.message === "string" && body.message) return body.message;
  return "";
}

/** Response body 只读取一次：先取 text，再尝试 JSON.parse（二者都不再二次消费 stream）。 */
async function readBodyOnce(resp: Response): Promise<{ json: any; text: string }> {
  let text = "";
  try {
    text = await resp.text();
  } catch {
    return { json: null, text: "" };
  }
  try {
    return { json: text ? JSON.parse(text) : null, text };
  } catch {
    return { json: null, text };
  }
}

async function jsonReq<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...((init?.headers as any) || {}) };
  const tk = auth.getToken();
  if (tk) headers["Authorization"] = "Bearer " + tk;

  let resp: Response;
  try {
    resp = await fetch(BASE + path, { headers, ...init });
  } catch (e: any) {
    // 网络层失败：断网 / 反代不通 / 混合内容 / DNS —— 统一友好提示
    throw new Error(friendlyError(e));
  }

  if (resp.status === 401) {
    auth.clear();
    window.dispatchEvent(new CustomEvent("datachat:unauthorized"));
    const { json } = await readBodyOnce(resp);
    throw new Error(pickServerMessage(json) || "登录已过期，请重新登录。");
  }

  if (!resp.ok) {
    const { json } = await readBodyOnce(resp);
    const msg = pickServerMessage(json);
    throw new Error(msg || "操作失败，请稍后重试或联系管理员。");
  }

  const { json } = await readBodyOnce(resp);
  return json as T;
}

export const api = {
  /* ---------- 公共 ---------- */
  health: () => jsonReq<any>("/api/health"),
  adminDiagnostics: () => jsonReq<any>("/api/admin/diagnostics"),
  bootstrap: () => jsonReq<BootstrapInfo>("/api/bootstrap"),
  suggestions: () => jsonReq<{ items: string[] }>("/api/suggestions"),

  /* ---------- 鉴权 ---------- */
  login: (username: string, password: string) =>
    jsonReq<{ token: string; user: AuthUser }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => jsonReq<AuthUser>("/api/me"),
  changeMyPassword: (old_password: string, new_password: string) =>
    jsonReq<{ ok: boolean }>("/api/me/password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    }),

  /* ---------- 用户管理（admin） ---------- */
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
  updateMyProfile: (email: string) =>
    jsonReq<AuthUser>("/api/me/profile", { method: "PATCH", body: JSON.stringify({ email }) }),

  /* ---------- 审计日志（admin） ---------- */
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

  /* ---------- 语义层 ---------- */
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

  /* ---------- 数据权限（admin） ---------- */
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

  /* ---------- 语义层 CRUD + 自动分析 ---------- */
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

  /* ---------- 语义认证工作流（机器起草 → 人工认证） ---------- */
  semanticCertification: () =>
    jsonReq<{ kinds: Record<string, { name: string; label: string; status: "draft" | "verified" }[]>;
              stats: { draft: number; verified: number } }>("/api/admin/semantic/certification"),
  semanticSetStatus: (kind: "tables"|"dimensions"|"metrics", name: string, status: "draft"|"verified") =>
    jsonReq<{ ok: boolean }>(`/api/admin/semantic/${kind}/${encodeURIComponent(name)}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  /* ---------- 问数反馈（采纳→few-shot 飞轮；点踩→bad case 库） ---------- */
  chatFeedback: (conversation_id: string, trace_id: string, vote: "up" | "down") =>
    jsonReq<{ ok: boolean; adopted?: boolean }>("/api/chat/feedback", {
      method: "POST",
      body: JSON.stringify({ conversation_id, trace_id, vote }),
    }),

  /* ---------- 报告模板（per-user） ---------- */
  listReportTemplates: (owner?: string) => {
    const qs = owner ? `?owner=${encodeURIComponent(owner)}` : "";
    return jsonReq<{ items: { id: string; name: string; prompt: string; is_default: boolean;
                              user_id: string; is_system: boolean; is_mine: boolean;
                              created_at: number; updated_at: number }[] }>(`/api/report/templates${qs}`);
  },
  createReportTemplate: (name: string, prompt: string, is_default: boolean = false, system: boolean = false) =>
    jsonReq<any>("/api/report/templates", {
      method: "POST",
      body: JSON.stringify({ name, prompt, is_default, system }),
    }),
  updateReportTemplate: (id: string, patch: { name?: string; prompt?: string; is_default?: boolean }) =>
    jsonReq<any>(`/api/report/templates/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteReportTemplate: (id: string) =>
    jsonReq<any>(`/api/report/templates/${id}`, { method: "DELETE" }),

  /* ---------- 文件夹 + 会话收藏 ---------- */
  listFolders: () => jsonReq<{ items: { id: string; name: string; color: string; created_at: number }[] }>("/api/folders"),
  createFolder: (name: string, color: string = "") =>
    jsonReq<any>("/api/folders", { method: "POST", body: JSON.stringify({ name, color }) }),
  renameFolder: (id: string, name: string, color?: string) =>
    jsonReq<any>(`/api/folders/${id}`, { method: "PATCH", body: JSON.stringify({ name, color }) }),
  deleteFolder: (id: string) => jsonReq<any>(`/api/folders/${id}`, { method: "DELETE" }),
  folderConversations: (id: string) =>
    jsonReq<{ items: { id: string; title: string; created_at: number; collected_at: number }[] }>(`/api/folders/${id}/conversations`),
  collectConversation: (conversation_id: string, folder_id: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${conversation_id}/collect`, {
      method: "POST",
      body: JSON.stringify({ conversation_id, folder_id }),
    }),
  uncollectConversation: (conversation_id: string, folder_id: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${conversation_id}/collect/${folder_id}`, { method: "DELETE" }),
  conversationFolderIds: (cid: string) =>
    jsonReq<{ folder_ids: string[] }>(`/api/conversations/${cid}/folders`),

  /* ---------- 会话 ---------- */
  listConversations: () => jsonReq<{ items: ConversationMeta[] }>(`/api/conversations`),
  createConversation: (title = "新会话") =>
    jsonReq<ConversationMeta>("/api/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  getConversation: (cid: string) => jsonReq<ConversationDetail>(`/api/conversations/${cid}`),
  renameConversation: (cid: string, title: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${cid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteConversation: (cid: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${cid}`, { method: "DELETE" }),

  /* ---------- 问数（同步 + 流式） ---------- */
  chat: (req: { question: string; conversation_id?: string | null; force_refresh?: boolean; llm_provider?: string | null }) =>
    jsonReq<ChatResult>("/api/chat", { method: "POST", body: JSON.stringify(req) }),

  /* ---------- LLM 模型 ---------- */
  listLLMProviders: () =>
    jsonReq<{
      available: { id: string; label: string; hint: string }[];
      default: string;
    }>("/api/llm/providers"),

  /* ---------- admin: LLM 设置（热改 + 持久化）---------- */
  adminGetLLMSettings: () =>
    jsonReq<import("./types").LLMSettingsResp>("/api/admin/llm-settings"),

  // 入参字段：null = 不动，"" = 清除回退到 env/默认，非空 = 写入 DB
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

  /* ---------- admin: 多套 LLM 预设（preset） ---------- */
  adminListLLMPresets: () =>
    jsonReq<{ items: import("./types").LLMPreset[] }>("/api/admin/llm-presets"),

  adminTestLLMPresetCandidate: (req: {
    provider: "bailian" | "feihe";
    api_key?: string;
    base_url?: string;
    model: string;
    prompt?: string;
    preset_id?: string;   // 编辑时"旧 AK + 当前草稿字段"合并测试用
  }) =>
    jsonReq<import("./types").LLMPresetTestResult>("/api/admin/llm-presets/test", {
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
    jsonReq<{ ok: boolean; preset: import("./types").LLMPreset }>("/api/admin/llm-presets", {
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
    jsonReq<{ ok: boolean; preset: import("./types").LLMPreset }>(`/api/admin/llm-presets/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(req),
    }),

  adminDeleteLLMPreset: (id: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/llm-presets/${encodeURIComponent(id)}`, { method: "DELETE" }),

  adminSetDefaultLLMPreset: (id: string) =>
    jsonReq<{ ok: boolean }>(`/api/admin/llm-presets/${encodeURIComponent(id)}/set-default`, { method: "POST" }),

  adminTestExistingLLMPreset: (id: string) =>
    jsonReq<import("./types").LLMPresetTestResult>(`/api/admin/llm-presets/${encodeURIComponent(id)}/test`, { method: "POST" }),

  /* ---------- 飞书 ---------- */
  // 安全（P0）：推送内容由后端按 trace 取可信结果生成，前端仅传定位用的
  // conversation_id / trace_id（admin 可附带收件邮箱）。不再传任何经营文案。
  feishuPush: (req: {
    conversation_id: string;
    trace_id: string;
    user_email?: string;
  }) =>
    jsonReq<{ ok: boolean; error_code?: string; user_message?: string; trace_id?: string; content_sha256?: string }>(
      "/api/feishu/push",
      { method: "POST", body: JSON.stringify(req) },
    ),

  /* ---------- 报告 ---------- */
  reportDownloadUrl: () => `${BASE}/api/report/generate`,

  /* ---------- SSE 流式问数 ---------- */
  stream: (
    req: { question: string; conversation_id?: string | null; force_refresh?: boolean; llm_provider?: string | null },
    onEvent: (evt: StageEvent) => void,
    onDone: (result: ChatResult) => void,
    onError: (err: string) => void,
    abort?: AbortSignal,
  ): { close: () => void } => {
    const ctrl = new AbortController();
    if (abort) abort.addEventListener("abort", () => ctrl.abort());

    (async () => {
      try {
        const tk = auth.getToken();
        const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
        if (tk) headers["Authorization"] = "Bearer " + tk;
        const resp = await fetch(BASE + "/api/chat/stream", {
          method: "POST",
          headers,
          body: JSON.stringify(req),
          signal: ctrl.signal,
        });
        if (resp.status === 401) {
          auth.clear();
          window.dispatchEvent(new CustomEvent("datachat:unauthorized"));
          onError("登录已过期，请重新登录。");
          return;
        }
        if (!resp.ok || !resp.body) {
          const { json } = await readBodyOnce(resp);
          onError(pickServerMessage(json) || "问数服务暂时不可用，请稍后重试或联系管理员。");
          return;
        }
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const chunk = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const lines = chunk.split("\n");
            let event = "message";
            let data = "";
            for (const ln of lines) {
              if (ln.startsWith("event:")) event = ln.slice(6).trim();
              else if (ln.startsWith("data:")) data += ln.slice(5).trim();
            }
            if (!data) continue;
            try {
              const obj = JSON.parse(data);
              if (event === "stage") onEvent(obj as StageEvent);
              else if (event === "done") onDone(obj as ChatResult);
              else if (event === "error") onError(String(obj?.error || "未知错误"));
            } catch {
              /* ignore non-JSON */
            }
          }
        }
      } catch (e: any) {
        if (e?.name === "AbortError") return;
        onError(friendlyError(e));
      }
    })();

    return { close: () => ctrl.abort() };
  },
};
