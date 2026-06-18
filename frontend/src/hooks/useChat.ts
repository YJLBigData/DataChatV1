/**
 * 问数页的会话 + 流式状态容器（与 useExpertTeam 对称）。
 *
 * 把原先散在 App.tsx 里的 chat 状态机整体抽到这里：会话列表、每会话 turns、并发流式、
 * 红点、draft→真实会话迁移、SmartQ 非流式问数、打开/新建/改名/删除/终止。
 * App 只负责把它接到侧栏会话列表与 ChatPage 两处渲染。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, friendlyError } from "../api";
import { toast } from "../shared/toast";
import { useConversations } from "./useConversations";
import type { AuthUser, ChatResult, ChatTurn } from "../types";

const DRAFT_KEY = "__draft__";

function uuid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function useChat(opts: { enabled: boolean; user: AuthUser | null; llmChoice: string; smartqCubeIds?: string[] }) {
  const { user, llmChoice } = opts;
  const smartqContextIds = useMemo(() => [...new Set((opts.smartqCubeIds || []).filter(Boolean))], [opts.smartqCubeIds]);
  const { conversations, setConversations, refreshConversations } = useConversations(opts.enabled);
  const [activeId, setActiveId] = useState<string | null>(null);
  /** 每个对话独立的 turns，draft（未保存）放在 __draft__ key。允许同时多个对话流式进行。 */
  const [turnsByConv, setTurnsByConv] = useState<Record<string, ChatTurn[]>>({});
  /** 当前正在 streaming 的对话 key 集合。 */
  const [streamingConvs, setStreamingConvs] = useState<Set<string>>(new Set());
  /** 完成但用户没看的对话 — 用于红点。 */
  const [unread, setUnread] = useState<Set<string>>(new Set());
  /** 每个对话的 stream 句柄（用于"用户终止"按钮）。 */
  const streamHandles = useRef<Record<string, { close: () => void }>>({});
  const [input, setInput] = useState("");
  const [forceRefresh, setForceRefresh] = useState(false);
  /* SmartQ（Quick BI 智能小Q）问数模式 */
  const [smartqDatasets, setSmartqDatasets] = useState<{ cube_id: string; name: string; theme: string }[]>([]);
  const [smartqCube, setSmartqCube] = useState<string>("");

  const isSmartQ = llmChoice === "smartq";

  const turns = useMemo(() => {
    const key = activeId || DRAFT_KEY;
    const cur = turnsByConv[key];
    if (cur && cur.length) return cur;
    // 过渡兜底：draft → 真实会话迁移那一帧，活动桶可能瞬时为空，
    // 若 draft 桶里还有刚提交的 turn，就先用它，避免"问完闪一下首页"。
    if (activeId) {
      const draft = turnsByConv[DRAFT_KEY];
      if (draft && draft.length) return draft;
    }
    return cur || [];
  }, [turnsByConv, activeId]);
  const streaming = streamingConvs.has(activeId || DRAFT_KEY);

  const updateTurnsForConv = useCallback((convKey: string, mapper: (arr: ChatTurn[]) => ChatTurn[]) => {
    setTurnsByConv((prev) => {
      const next = { ...prev };
      next[convKey] = mapper(prev[convKey] || []);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    setConversations([]); setTurnsByConv({}); setStreamingConvs(new Set()); setUnread(new Set()); setActiveId(null);
  }, [setConversations]);

  /**
   * Build ChatTurn[] from a fetched conversation detail (msgs pair: user → assistant).
   * Hardened to never produce undefined sub-fields that crash AnswerCard.
   */
  const restoreTurnsFromMessages = useCallback((cid: string, msgs: any[]): ChatTurn[] => {
    const out: ChatTurn[] = [];
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      if (m.role !== "user") continue;
      const next = msgs[i + 1];
      if (next && next.role === "assistant") {
        const a = next.payload?.answer || {};
        out.push({
          id: m.id, question: m.content, pending: false, events: [],
          result: {
            trace_id: next.payload?.trace_id || "",
              conversation_id: cid, question: m.content,
            answer: {
              needs_clarify: !!a.needs_clarify,
              narrative: a.narrative || next.content || "",
              highlights: Array.isArray(a.highlights) ? a.highlights : [],
              risk_notes: Array.isArray(a.risk_notes) ? a.risk_notes : [],
              table: {
                columns: a.table?.columns || [],
                rows: a.table?.rows || [],
                display_columns: a.table?.display_columns || [],
                display_rows: a.table?.display_rows || [],
                row_count: a.table?.row_count ?? 0,
                elapsed_ms: a.table?.elapsed_ms ?? 0,
              },
              chart: a.chart || { type: "none" },
              suggestions: Array.isArray(a.suggestions) ? a.suggestions : [],
              clarify_options: Array.isArray(a.clarify_options) ? a.clarify_options : [],
              explainability: a.explainability || {} as any,
            },
            plan: next.payload?.plan || ({} as any),
            sql: next.payload?.sql || "",
            rows: next.payload?.rows || 0,
            cached: !!next.payload?.cached,
            elapsed_ms: 0,
            smartq: next.payload?.smartq || undefined,
          },
        });
        i += 1;
      } else {
        out.push({ id: m.id, question: m.content, pending: false, events: [], error: "未找到回复" });
      }
    }
    return out;
  }, []);

  // SmartQ 选中时拉取该用户被授权的数据集（服务端按身份解析，前端只选范围）。
  useEffect(() => {
    if (!isSmartQ || !user) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.smartqDatasets();
        if (cancelled) return;
        setSmartqDatasets(r.items || []);
        if (r.items?.length) setSmartqCube((cur) => cur || r.items[0].cube_id);
        if (!r.ok && r.error) toast.error(r.error);
      } catch (e: any) {
        if (!cancelled) toast.error(friendlyError(e));
      }
    })();
    return () => { cancelled = true; };
  }, [isSmartQ, user]);

  // SmartQ 问数：走 /api/smartq/query（非流式）。结果由后端落库到问数会话并返回
  // 可信 (conversation_id, trace_id) —— 与普通问数共享导出/报告/飞书/反馈链路。
  const submitSmartQ = useCallback(async (q: string) => {
    const ownerCid = activeId || DRAFT_KEY;
    const turnId = uuid();
    updateTurnsForConv(ownerCid, (arr) => [...arr, { id: turnId, question: q, pending: true, events: [] }]);
    setInput("");
    try {
      const r = await api.smartqQuery({
        question: q,
        cube_id: smartqCube || undefined,
        conversation_id: activeId || undefined,
      });
      if (!r.ok || !r.answer) {
        const err = r.error || "智能小Q查询失败";
        updateTurnsForConv(ownerCid, (arr) => arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: err } : t)));
        toast.error(err);
        return;
      }
      const realCid = r.conversation_id || (ownerCid === DRAFT_KEY ? "" : ownerCid);
      const result = {
        trace_id: r.trace_id || "", conversation_id: realCid, question: q,
        answer: r.answer, plan: {} as any, sql: r.sql || "",
        rows: r.rows ?? (r.answer.table?.row_count || 0), cached: false, elapsed_ms: 0,
        smartq: r.smartq || undefined,
      } as ChatResult;
      // draft → 真实会话：把该轮迁移到后端新建的会话 id，并跟随过去（与普通问数一致）。
      if (ownerCid === DRAFT_KEY && realCid) {
        setTurnsByConv((prev) => {
          const next = { ...prev };
          const draftArr = next[DRAFT_KEY] || [];
          const moving = draftArr.filter((t) => t.id === turnId).map((t) => ({ ...t, pending: false, result }));
          const remain = draftArr.filter((t) => t.id !== turnId);
          if (remain.length) next[DRAFT_KEY] = remain; else delete next[DRAFT_KEY];
          next[realCid] = [...(next[realCid] || []), ...moving];
          return next;
        });
        setActiveId((cur) => (cur === null ? realCid : cur));
      } else {
        updateTurnsForConv(ownerCid, (arr) => arr.map((t) => (t.id === turnId ? { ...t, pending: false, result } : t)));
      }
      refreshConversations();
    } catch (e: any) {
      updateTurnsForConv(ownerCid, (arr) => arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: friendlyError(e) } : t)));
      toast.error(friendlyError(e));
    }
  }, [activeId, smartqCube, updateTurnsForConv, refreshConversations]);

  const submit = useCallback(
    (qOverride?: string) => {
      const q = (qOverride ?? input).trim();
      if (!q || !user) return;
      if (smartqContextIds.length === 0 && isSmartQ) { void submitSmartQ(q); return; }
      // 允许同时多个对话流式：以 submit 那一刻的 activeId 为 owner（null → draft）
      const ownerCid = activeId || DRAFT_KEY;
      // 该 owner 是否已经有正在跑的请求？防止用户在同一对话快速连续点
      if (streamingConvs.has(ownerCid)) return;
      const turnId = uuid();
      updateTurnsForConv(ownerCid, (arr) => [...arr, { id: turnId, question: q, pending: true, events: [] }]);
      setInput("");
      setStreamingConvs((p) => { const n = new Set(p); n.add(ownerCid); return n; });

      // 用一个 ref 来追踪此 stream 的真实 cid（draft 在 session 事件后会变成真实 cid）
      let currentCid = ownerCid;
      const startedFromDraft = ownerCid === DRAFT_KEY;

      const moveTurn = (fromKey: string, toKey: string) => {
        if (fromKey === toKey) return;
        setTurnsByConv((prev) => {
          const next = { ...prev };
          const arr = next[fromKey] || [];
          const moving = arr.filter((t) => t.id === turnId);
          const remaining = arr.filter((t) => t.id !== turnId);
          if (remaining.length) next[fromKey] = remaining; else delete next[fromKey];
          next[toKey] = [...(next[toKey] || []), ...moving];
          return next;
        });
        setStreamingConvs((prev) => {
          const n = new Set(prev); n.delete(fromKey); n.add(toKey); return n;
        });
      };

      const handle = api.stream(
        { question: q, conversation_id: activeId, force_refresh: forceRefresh, llm_provider: llmChoice || undefined,
          smartq_cube_ids: smartqContextIds.length ? smartqContextIds : null },
        (evt) => {
          // session 事件：把 draft 迁移到真实 cid（如果当前 owner 是 draft）
          if (evt.stage === "session" && evt.payload?.conversation_id && currentCid === DRAFT_KEY) {
            const realCid = String(evt.payload.conversation_id);
            moveTurn(DRAFT_KEY, realCid);
            // 如果当前用户视图还在 draft（即 activeId 是 null），自动跟随到新会话
            setActiveId((curr) => (curr === null ? realCid : curr));
            // 该 stream 的句柄改挂到 realCid 上
            streamHandles.current[realCid] = handle;
            delete streamHandles.current[DRAFT_KEY];
            currentCid = realCid;
          }
          updateTurnsForConv(currentCid, (arr) =>
            arr.map((t) => (t.id === turnId ? { ...t, events: [...t.events, evt] } : t)),
          );
        },
        (result) => {
          const finalCid = result.conversation_id || currentCid;
          if (finalCid && finalCid !== currentCid) {
            moveTurn(currentCid, finalCid);
            currentCid = finalCid;
          }
          if (startedFromDraft && finalCid) {
            streamHandles.current[finalCid] = handle;
            delete streamHandles.current[DRAFT_KEY];
          }
          if ((result as any)?.ok === false) {
            const err = (result as any).user_message || "问数失败，请稍后重试";
            const tid = (result as any).trace_id ? `（trace_id: ${String((result as any).trace_id).slice(0, 8)}）` : "";
            updateTurnsForConv(currentCid, (arr) =>
              arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: err + tid } : t)),
            );
          } else {
            updateTurnsForConv(currentCid, (arr) =>
              arr.map((t) => (t.id === turnId ? { ...t, pending: false, result } : t)),
            );
          }
          setStreamingConvs((p) => { const n = new Set(p); n.delete(currentCid); return n; });
          delete streamHandles.current[currentCid];
          // 若用户已切走 → 标记 unread 红点
          setActiveId((curr) => {
            if (startedFromDraft && curr === null && finalCid) {
              setUnread((u) => {
                if (!u.has(finalCid)) return u;
                const n = new Set(u); n.delete(finalCid); return n;
              });
              return finalCid;
            }
            if (curr !== currentCid) {
              setUnread((u) => { const n = new Set(u); n.add(currentCid); return n; });
            }
            return curr;
          });
          refreshConversations();
        },
        (err) => {
          updateTurnsForConv(currentCid, (arr) =>
            arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: err } : t)),
          );
          setStreamingConvs((p) => { const n = new Set(p); n.delete(currentCid); return n; });
          delete streamHandles.current[currentCid];
        },
      );
      streamHandles.current[currentCid] = handle;
    },
    [input, activeId, forceRefresh, user, streamingConvs, updateTurnsForConv, refreshConversations, llmChoice, isSmartQ, submitSmartQ, smartqContextIds],
  );

  const startNew = useCallback(() => {
    // 不再阻断流式：可以随时开新会话
    setActiveId(null);
    setInput("");
    // 不清空 turnsByConv，已有对话保持在内存中以便切换回看
  }, []);

  const openConversation = useCallback(
    async (cid: string) => {
      // 允许在 streaming 时切换会话（已有 turns 保留在原 cid 下）
      setActiveId(cid);
      setUnread((u) => { if (!u.has(cid)) return u; const n = new Set(u); n.delete(cid); return n; });
      try {
        const detail = await api.getConversation(cid);
        const restored = restoreTurnsFromMessages(cid, detail.messages || []);
        // 合并：服务端已保存的历史 + 内存中正在进行的 pending turn（若有）
        setTurnsByConv((prev) => {
          const existing = prev[cid] || [];
          const pendings = existing.filter((t) => t.pending);
          const restoredIds = new Set(restored.map((t) => t.id));
          // 去掉已存在的 pending 重复（不会重复，只是为了安全）
          const merged = [...restored, ...pendings.filter((t) => !restoredIds.has(t.id))];
          return { ...prev, [cid]: merged };
        });
      } catch (e: any) {
        setTurnsByConv((prev) => ({
          ...prev,
          [cid]: [{ id: uuid(), question: "(加载会话失败)", pending: false, events: [], error: e?.message || String(e) }],
        }));
      }
    },
    [restoreTurnsFromMessages],
  );

  const renameConversation = useCallback(async (cid: string, title: string) => {
    try { await api.renameConversation(cid, title); refreshConversations(); } catch { /* ignore */ }
  }, [refreshConversations]);

  const deleteConversation = useCallback(async (cid: string) => {
    try {
      await api.deleteConversation(cid);
      // 若当前活动会话被删除 → 切回 draft
      setActiveId((cur) => (cid === cur ? null : cur));
      setTurnsByConv((p) => { const n = { ...p }; delete n[cid]; return n; });
      setStreamingConvs((p) => { const n = new Set(p); n.delete(cid); return n; });
      setUnread((p) => { if (!p.has(cid)) return p; const n = new Set(p); n.delete(cid); return n; });
      refreshConversations();
    } catch { /* ignore */ }
  }, [refreshConversations]);

  const abort = useCallback(() => {
    // 只终止当前活动会话的 stream
    const cid = activeId || DRAFT_KEY;
    const h = streamHandles.current[cid];
    if (h) {
      h.close();
      delete streamHandles.current[cid];
    }
    setStreamingConvs((p) => { const n = new Set(p); n.delete(cid); return n; });
    updateTurnsForConv(cid, (arr) => arr.map((t) => (t.pending ? { ...t, pending: false, error: "用户终止" } : t)));
  }, [activeId, updateTurnsForConv]);

  return {
    conversations, refreshConversations,
    activeId, setActiveId,
    turns, turnsByConv,
    streaming, streamingConvs, unread,
    input, setInput,
    forceRefresh, setForceRefresh,
    smartqDatasets, smartqCube, setSmartqCube, isSmartQ, smartqContextIds,
    submit, submitSmartQ, startNew, openConversation, renameConversation, deleteConversation, abort,
    reset,
  };
}

export type ChatHook = ReturnType<typeof useChat>;
