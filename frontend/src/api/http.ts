/**
 * API 共享底座 —— fetch + Bearer Token、401 自动登出、统一错误归一化。
 * 各 domain 模块（auth/chat/expert/exports/smartq/admin）都复用这里的 jsonReq。
 */
import type { AuthUser } from "../types";

export const BASE = "";
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

/**
 * 已知错误码 → 中文友好文案白名单（与后端 USER_FRIENDLY 对齐 + 中间件常见码）。
 * 审计 P2：未知错误码/原始 error 不直接抛给业务用户，避免泄露下游英文/技术细节。
 */
const ERROR_CODE_CN: Record<string, string> = {
  RATE_LIMITED: "操作过于频繁，请稍后再试。",
  CHAT_FAILED: "问数失败，请检查输入的问题是否符合规范，或联系管理员。",
  REPORT_FAILED: "报告生成失败，请稍后重试，或联系管理员。",
  FEISHU_FAILED: "推送飞书失败，请检查推送配置，或联系管理员。",
  PERMISSION_DENIED: "权限不足，请联系管理员开通相关数据权限。",
  INPUT_INVALID: "输入内容不符合规范，请调整后重试。",
  INTERNAL_ERROR: "系统繁忙，请稍后重试。",
  SERVER_ERROR: "服务暂时不可用，请稍后重试或联系管理员。",
  NOT_FOUND: "请求的资源不存在或已被删除。",
  FORBIDDEN: "没有权限执行该操作。",
  UNAUTHORIZED: "登录已过期，请重新登录。",
};

/**
 * 判断一段后端文案是否"可直接展示给用户"：含中文、长度可控、且不含明显技术痕迹
 * （堆栈/异常类名/SQL/HTTP/连接错误码等）。用于决定原始 error/detail 是否外显。
 */
function isUserSafeText(s: string): boolean {
  if (!s || s.length > 120) return false;
  if (!/[一-龥]/.test(s)) return false; // 纯英文一律视为技术错误
  if (/traceback|exception|stack|errno|\bE[A-Z]{3,}\b|sqlstate|syntax|null|undefined|at line|status code|[a-z]+error\b|<[^>]+>/i.test(s)) {
    return false;
  }
  return true;
}

/**
 * 从后端响应体中提取**对用户友好**的提示。优先级：
 *   user_message（后端已本地化，最可信）→ 白名单 error_code → 安全的 detail/message。
 * 原始 error/detail 若看起来是技术错误，仅 console 记录（带 trace_id）供排查，前台回退统一文案。
 */
export function pickServerMessage(body: any): string {
  if (!body || typeof body !== "object") return "";
  const trace = body.trace_id ? String(body.trace_id).slice(0, 8) : "";
  const withTrace = (msg: string) => (trace ? `${msg}（trace_id: ${trace}）` : msg);
  const logTechnical = (raw: string) => {
    try { console.warn("[api] 后端技术错误（已对用户隐藏）:", raw, trace ? `trace_id=${trace}` : ""); } catch { /* noop */ }
  };

  // 1) 后端本地化文案（最可信）
  if (typeof body.user_message === "string" && body.user_message) return withTrace(body.user_message);

  const d = body.detail;
  if (d && typeof d === "object") {
    if (typeof d.user_message === "string" && d.user_message) return withTrace(d.user_message);
    if (typeof d.message === "string" && isUserSafeText(d.message)) return d.message;
  }

  // 2) 白名单错误码 → 中文
  const code = typeof body.error_code === "string" ? body.error_code : "";
  if (code && ERROR_CODE_CN[code]) return withTrace(ERROR_CODE_CN[code]);

  // 3) 安全的 detail / message 字符串（含中文且无技术痕迹）才外显
  if (typeof d === "string" && d) {
    if (isUserSafeText(d)) return d;
    logTechnical(d);
  }
  if (typeof body.message === "string" && body.message) {
    if (isUserSafeText(body.message)) return body.message;
    logTechnical(body.message);
  }
  // 4) 原始 error（slowapi 等中间件的 {"error": "..."}）：安全才显示，否则记日志、回退统一文案。
  if (typeof body.error === "string" && body.error) {
    if (isUserSafeText(body.error)) return body.error;
    logTechnical(body.error);
  }
  return "";
}

/** Response body 只读取一次：先取 text，再尝试 JSON.parse（二者都不再二次消费 stream）。 */
export async function readBodyOnce(resp: Response): Promise<{ json: any; text: string }> {
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

export async function jsonReq<T>(path: string, init?: RequestInit): Promise<T> {
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
