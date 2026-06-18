/** 问数核心 API —— bootstrap/问数（同步+SSE 流式）/反馈/会话/文件夹/报告模板/飞书/报告下载/LLM provider。 */
import type {
  BootstrapInfo,
  ChatResult,
  ConversationDetail,
  ConversationMeta,
  StageEvent,
} from "../types";
import { BASE, auth, friendlyError, jsonReq, pickServerMessage, readBodyOnce } from "./http";

export const chatApi = {
  /* 公共 */
  health: () => jsonReq<any>("/api/health"),
  bootstrap: () => jsonReq<BootstrapInfo>("/api/bootstrap"),
  suggestions: () => jsonReq<{ items: string[] }>("/api/suggestions"),

  /* 问数反馈（采纳→few-shot 飞轮；点踩→bad case 库） */
  chatFeedback: (conversation_id: string, trace_id: string, vote: "up" | "down") =>
    jsonReq<{ ok: boolean; adopted?: boolean }>("/api/chat/feedback", {
      method: "POST",
      body: JSON.stringify({ conversation_id, trace_id, vote }),
    }),

  /* 报告模板（per-user） */
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

  /* 文件夹 + 会话收藏 */
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
  conversationFolderIdsBatch: (conversation_ids: string[]) =>
    jsonReq<{ map: Record<string, string[]> }>("/api/folders/membership", {
      method: "POST", body: JSON.stringify({ conversation_ids }),
    }),

  /* 会话 */
  listConversations: () => jsonReq<{ items: ConversationMeta[] }>(`/api/conversations`),
  createConversation: (title = "新会话") =>
    jsonReq<ConversationMeta>("/api/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  getConversation: (cid: string) => jsonReq<ConversationDetail>(`/api/conversations/${cid}`),
  renameConversation: (cid: string, title: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${cid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteConversation: (cid: string) =>
    jsonReq<{ ok: boolean }>(`/api/conversations/${cid}`, { method: "DELETE" }),

  /* 问数（同步） */
  chat: (req: { question: string; conversation_id?: string | null; force_refresh?: boolean; llm_provider?: string | null }) =>
    jsonReq<ChatResult>("/api/chat", { method: "POST", body: JSON.stringify(req) }),

  /* LLM provider 下拉（全体用户） */
  listLLMProviders: () =>
    jsonReq<{
      available: { id: string; label: string; hint: string }[];
      default: string;
    }>("/api/llm/providers"),

  /* 飞书：内容由后端按 trace 取可信结果生成，前端仅传定位用的 conversation_id / trace_id */
  feishuPush: (req: {
    conversation_id: string;
    trace_id: string;
    user_email?: string;
  }) =>
    jsonReq<{ ok: boolean; error_code?: string; user_message?: string; trace_id?: string; content_sha256?: string }>(
      "/api/feishu/push",
      { method: "POST", body: JSON.stringify(req) },
    ),

  /* 报告下载（直接 fetch blob，URL 给调用方） */
  reportDownloadUrl: () => `${BASE}/api/report/generate`,

  /* SSE 流式问数 */
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
