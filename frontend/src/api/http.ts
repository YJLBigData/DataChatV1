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

/** 从后端响应体中提取对用户友好的提示（兼容 user_message / detail 字符串或对象）。 */
export function pickServerMessage(body: any): string {
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
