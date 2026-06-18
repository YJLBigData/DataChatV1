/** 专家团 API —— 成员增删改查 + 后台编排 job + 独立会话历史 + 文件夹收藏。 */
import type {
  ConversationDetail,
  ConversationMeta,
  ExpertMemberDetail,
  ExpertTeamBootstrap,
  ExpertTeamResult,
} from "../types";
import { jsonReq } from "./http";

export const expertApi = {
  /* 多 skill 编排：决策总监调度 + 专家协同 + 报告合成 */
  expertTeamBootstrap: () =>
    jsonReq<ExpertTeamBootstrap>("/api/expert-team/bootstrap"),
  expertTeamChat: (req: {
    question: string;
    expert_ids?: string[] | null;
    want_report?: boolean;
    llm_provider?: string | null;
    conversation_id?: string | null;
    smartq_cube_ids?: string[] | null;
  }) =>
    jsonReq<ExpertTeamResult>("/api/expert-team/chat", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  // 成员（内置专家 + 自建 skill）统一增删改查
  expertTeamGetMember: (id: string) =>
    jsonReq<{ ok: boolean; error?: string; member?: ExpertMemberDetail }>(
      `/api/expert-team/members/${encodeURIComponent(id)}`,
    ),
  expertTeamCreateSkill: (req: { name: string; profession?: string; instructions?: string; emoji?: string }) =>
    jsonReq<{ ok: boolean; error?: string; member?: ExpertMemberDetail }>("/api/expert-team/skills", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  expertTeamUpdateMember: (id: string, patch: { name?: string; profession?: string; instructions?: string; emoji?: string }) =>
    jsonReq<{ ok: boolean; error?: string; member?: ExpertMemberDetail }>(
      `/api/expert-team/members/${encodeURIComponent(id)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),
  expertTeamDeleteMember: (id: string) =>
    jsonReq<{ ok: boolean; error?: string; hidden?: boolean }>(`/api/expert-team/members/${encodeURIComponent(id)}`, { method: "DELETE" }),
  expertTeamResetMember: (id: string) =>
    jsonReq<{ ok: boolean; error?: string; member?: ExpertMemberDetail }>(
      `/api/expert-team/members/${encodeURIComponent(id)}/reset`,
      { method: "POST" },
    ),

  /* 后台编排（异步 job，切页/刷新不中断） */
  expertChatAsync: (req: {
    question: string;
    expert_ids?: string[] | null;
    want_report?: boolean;
    llm_provider?: string | null;
    conversation_id?: string | null;
    smartq_cube_ids?: string[] | null;
  }) =>
    jsonReq<{ ok: boolean; error?: string; job_id?: string; conversation_id?: string }>(
      "/api/expert-team/chat/async",
      { method: "POST", body: JSON.stringify(req) },
    ),
  expertJob: (jobId: string) =>
    jsonReq<{
      ok: boolean; status?: string; error?: string; conversation_id?: string;
      events?: { stage: string; [k: string]: any }[];
      result?: ExpertTeamResult | null;
    }>(`/api/expert-team/jobs/${encodeURIComponent(jobId)}`),
  expertRunningJobs: () =>
    // active = queued|running：含刚提交还没轮到的排队 job（刷新后也能重挂红点/进度）。
    jsonReq<{ ok: boolean; items: { job_id: string; conversation_id: string; status: string }[] }>(
      "/api/expert-team/jobs?status=active",
    ),
  expertCancelJob: (jobId: string) =>
    jsonReq<{ ok: boolean }>(`/api/expert-team/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),

  /* 会话历史（与问数完全独立） */
  expertListConversations: () =>
    jsonReq<{ items: ConversationMeta[] }>("/api/expert-team/conversations"),
  expertCreateConversation: (title = "新会话") =>
    jsonReq<ConversationMeta>("/api/expert-team/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  expertGetConversation: (cid: string) =>
    jsonReq<ConversationDetail>(`/api/expert-team/conversations/${cid}`),
  expertRenameConversation: (cid: string, title: string) =>
    jsonReq<{ ok: boolean }>(`/api/expert-team/conversations/${cid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  expertDeleteConversation: (cid: string) =>
    jsonReq<{ ok: boolean }>(`/api/expert-team/conversations/${cid}`, { method: "DELETE" }),

  /* 文件夹 + 收藏（与问数完全独立） */
  expertListFolders: () =>
    jsonReq<{ items: { id: string; name: string; color: string; created_at: number }[] }>("/api/expert-team/folders"),
  expertCreateFolder: (name: string, color = "") =>
    jsonReq<any>("/api/expert-team/folders", { method: "POST", body: JSON.stringify({ name, color }) }),
  expertRenameFolder: (id: string, name: string, color?: string) =>
    jsonReq<any>(`/api/expert-team/folders/${id}`, { method: "PATCH", body: JSON.stringify({ name, color }) }),
  expertDeleteFolder: (id: string) =>
    jsonReq<any>(`/api/expert-team/folders/${id}`, { method: "DELETE" }),
  expertFolderConversations: (id: string) =>
    jsonReq<{ items: { id: string; title: string; created_at: number; collected_at: number }[] }>(`/api/expert-team/folders/${id}/conversations`),
  expertCollectConversation: (conversation_id: string, folder_id: string) =>
    jsonReq<{ ok: boolean }>(`/api/expert-team/conversations/${conversation_id}/collect`, {
      method: "POST", body: JSON.stringify({ conversation_id, folder_id }),
    }),
  expertUncollectConversation: (conversation_id: string, folder_id: string) =>
    jsonReq<{ ok: boolean }>(`/api/expert-team/conversations/${conversation_id}/collect/${folder_id}`, { method: "DELETE" }),
  expertConversationFolderIds: (cid: string) =>
    jsonReq<{ folder_ids: string[] }>(`/api/expert-team/conversations/${cid}/folders`),
  expertConversationFolderIdsBatch: (conversation_ids: string[]) =>
    jsonReq<{ map: Record<string, string[]> }>("/api/expert-team/folders/membership", {
      method: "POST", body: JSON.stringify({ conversation_ids }),
    }),
};
