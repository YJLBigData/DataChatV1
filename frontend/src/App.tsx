/**
 * 飞鹤小Q · 智能问数 — 单页 App。
 *
 * 顶层布局：
 *   ┌────────┬────────────────────────────────────────────┐
 *   │        │  顶部 Header（标题 + 健康徽章 + 用户菜单）   │
 *   │        ├────────────────────────────────────────────┤
 *   │ 左侧   │                                            │
 *   │ 导航   │          页面内容（按 currentPage 切换）    │
 *   │        │                                            │
 *   └────────┴────────────────────────────────────────────┘
 *
 * 路由（前端状态机）：
 *   chat        — 聊天主页（普通用户也能用）
 *   logs        — 审计日志（admin）
 *   semantic    — 知识库 / 语义层（admin）
 *   permissions — 数据权限（admin）
 *   users       — 用户管理（admin）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, auth, friendlyError } from "./api";
import { AnswerCard } from "./components/AnswerCard";
import { Composer } from "./components/Composer";
import { ConversationList } from "./components/ConversationList";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Hero } from "./components/Hero";
import { LoginScreen } from "./components/LoginScreen";
import { PasswordModal } from "./components/PasswordModal";
import { Sidebar } from "./components/Sidebar";
import { UserMenu } from "./components/UserMenu";
import { LLMSettingsPage } from "./components/pages/LLMSettingsPage";
import { LogsPage } from "./components/pages/LogsPage";
import { PermissionsPage } from "./components/pages/PermissionsPage";
import { ReportTemplatesPage } from "./components/pages/ReportTemplatesPage";
import { SemanticPage } from "./components/pages/SemanticPage";
import { UsersPage } from "./components/pages/UsersPage";
import { ReportDownloadModal } from "./components/ReportDownloadModal";
import type { AuthUser, BootstrapInfo, ChatTurn, ConversationMeta, PageId } from "./types";

function uuid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export default function App() {
  /* ------------------------------- auth + boot ----------------------------- */
  const [user, setUser] = useState<AuthUser | null>(() => auth.getUser());
  const [boot, setBoot] = useState<BootstrapInfo | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  /* ------------------------------- router ---------------------------------- */
  const [page, setPage] = useState<PageId>("chat");

  /* ------------------------------- chat state ------------------------------ */
  const DRAFT_KEY = "__draft__";
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const viewportRef = useRef<HTMLDivElement | null>(null);

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

  /* ------------------------------- modals ---------------------------------- */
  const [pwdOpen, setPwdOpen] = useState(false);
  const [reportFor, setReportFor] = useState<ChatTurn | null>(null);

  /* --------------- LLM provider 切换（右上角下拉，每次 chat 请求都传） --------------- */
  type LLMProvider = { id: string; label: string; hint: string };
  const [llmProviders, setLlmProviders] = useState<LLMProvider[]>([]);
  const [llmDefault, setLlmDefault] = useState<string>("");
  const LLM_STORAGE_KEY = "datachatv1:llm_provider";
  const [llmChoice, setLlmChoice] = useState<string>(
    () => (typeof window !== "undefined" && localStorage.getItem(LLM_STORAGE_KEY)) || "",
  );

  // 提取为可复用函数：LLM 设置页新建/编辑/删除/设默认后会触发同名事件来重拉，
  // 让右上角下拉框无需刷新页面就能拿到最新的 preset 列表。
  const reloadLLMProviders = useCallback(async () => {
    try {
      const r = await api.listLLMProviders();
      setLlmProviders(r.available || []);
      setLlmDefault(r.default || "");
      setLlmChoice((cur) => {
        const ids = (r.available || []).map((x) => x.id);
        if (cur && ids.includes(cur)) return cur;
        return r.default || ids[0] || "";
      });
    } catch {
      /* 拉不到列表不阻塞主流程 */
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void reloadLLMProviders();
    const onChanged = () => { void reloadLLMProviders(); };
    window.addEventListener("datachat:llm_providers_changed", onChanged);
    return () => window.removeEventListener("datachat:llm_providers_changed", onChanged);
  }, [user, reloadLLMProviders]);
  useEffect(() => {
    if (llmChoice) localStorage.setItem(LLM_STORAGE_KEY, llmChoice);
  }, [llmChoice]);

  /* ----------------------------- 401 handling ------------------------------ */
  useEffect(() => {
    const fn = () => {
      setUser(null); setConversations([]); setTurnsByConv({}); setStreamingConvs(new Set()); setUnread(new Set()); setActiveId(null);
    };
    window.addEventListener("datachat:unauthorized", fn);
    return () => window.removeEventListener("datachat:unauthorized", fn);
  }, []);

  /* ------------------------------- bootstrap ------------------------------- */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const b = await api.bootstrap();
        if (!cancelled) setBoot(b);
      } catch (e: any) {
        if (!cancelled) setBootError(e?.message || String(e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /* ----------------------- conversations after login ----------------------- */
  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      try {
        const cs = await api.listConversations();
        if (!cancelled) setConversations(cs.items || []);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => {
    const v = viewportRef.current;
    if (!v) return;
    v.scrollTo({ top: v.scrollHeight, behavior: turns.length > 1 ? "smooth" : "auto" });
  }, [turns]);

  const refreshConversations = useCallback(async () => {
    if (!user) return;
    try { setConversations((await api.listConversations()).items || []); } catch { /* ignore */ }
  }, [user]);

  /* ------------------------------- chat fns -------------------------------- */
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
          },
        });
        i += 1;
      } else {
        out.push({ id: m.id, question: m.content, pending: false, events: [], error: "未找到回复" });
      }
    }
    return out;
  }, []);

  const submit = useCallback(
    (qOverride?: string) => {
      const q = (qOverride ?? input).trim();
      if (!q || !user) return;
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
        { question: q, conversation_id: activeId, force_refresh: forceRefresh, llm_provider: llmChoice || undefined },
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
    [input, activeId, forceRefresh, user, streamingConvs, updateTurnsForConv, refreshConversations, llmChoice],
  );

  const startNew = useCallback(() => {
    // 不再阻断流式：可以随时开新会话
    setActiveId(null);
    setInput("");
    setPage("chat");
    // 不清空 turnsByConv，已有对话保持在内存中以便切换回看
  }, []);

  const openConversation = useCallback(
    async (cid: string) => {
      // 允许在 streaming 时切换会话（已有 turns 保留在原 cid 下）
      setActiveId(cid);
      setPage("chat");
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
      if (cid === activeId) setActiveId(null);
      setTurnsByConv((p) => { const n = { ...p }; delete n[cid]; return n; });
      setStreamingConvs((p) => { const n = new Set(p); n.delete(cid); return n; });
      setUnread((p) => { if (!p.has(cid)) return p; const n = new Set(p); n.delete(cid); return n; });
      refreshConversations();
    } catch { /* ignore */ }
  }, [activeId, refreshConversations]);

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

  /* ------------------------------- auth fns -------------------------------- */
  const onLogin = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    auth.setToken(r.token); auth.setUser(r.user);
    setUser(r.user);
  }, []);
  const onLogout = useCallback(() => {
    auth.clear(); setUser(null); setConversations([]); setTurnsByConv({}); setStreamingConvs(new Set()); setUnread(new Set()); setActiveId(null);
    setPage("chat");
  }, []);

  const headerHealth = useMemo(() => {
    if (!boot) return null;
    // 只要有任意一项就展示下拉框（内置两条 legacy 永远在，所以基本恒为 true）。
    const hasChoices = llmProviders.length >= 1;
    const current = llmProviders.find((p) => p.id === llmChoice);
    return (
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {hasChoices ? (
          <label
            className="qq-pill-blue !cursor-pointer !py-0 !pr-1 inline-flex items-center gap-1"
            title={current?.hint || "切换大模型 provider（仅本次会话本地保存）"}
          >
            <span>🤖</span>
            <select
              className="bg-transparent text-[11px] font-medium outline-none cursor-pointer pr-1"
              value={llmChoice}
              onChange={(e) => setLlmChoice(e.target.value)}
            >
              {llmProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}{p.id === llmDefault ? "（默认）" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="qq-pill-blue" title={current?.hint || ""}>
            {current?.label || boot.model.name}
          </span>
        )}
        <span className="qq-pill-grey">{boot.metrics_count} 指标 · {boot.tables_count} 表</span>
        <span className="qq-pill-grey">数据 {boot.data_range[0]} ~ {boot.data_range[1]}</span>
      </div>
    );
  }, [boot, llmProviders, llmChoice, llmDefault]);

  /* ------------------------------- early states ---------------------------- */
  if (bootError) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f5f7fc]">
        <div className="qq-card max-w-md px-6 py-5 text-center">
          <div className="text-base font-semibold text-rose-600">服务无法连接</div>
          <div className="mt-2 text-xs text-slate-500">{bootError}</div>
          <button className="qq-btn-primary mt-4" onClick={() => location.reload()}>刷新页面</button>
        </div>
      </div>
    );
  }
  if (!boot) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f5f7fc]">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <span className="qq-loading-dot" /><span className="qq-loading-dot" /><span className="qq-loading-dot" />
          <span className="ml-2">正在唤醒飞鹤小Q…</span>
        </div>
      </div>
    );
  }
  if (!user || !auth.getToken()) {
    return <LoginScreen onLogin={onLogin} />;
  }

  /* ------------------------------- main render ----------------------------- */
  return (
    <div className="flex h-full w-full bg-[#f5f7fc]">
      {/* left main nav */}
      <Sidebar user={user} current={page} onChange={setPage} />

      {/* page area (conversations sidebar + main) */}
      <div className="flex flex-1 min-w-0">
        {page === "chat" && (
          <ConversationList
            items={conversations}
            activeId={activeId}
            onPick={openConversation}
            onNew={startNew}
            onRename={renameConversation}
            onDelete={deleteConversation}
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed((v) => !v)}
            unreadCids={unread}
            streamingCids={streamingConvs}
          />
        )}

        <main className="flex flex-1 min-w-0 flex-col">
          <header className="flex items-center justify-between border-b bg-white px-5 py-3" style={{ borderColor: "#eef1f8" }}>
            <div className="flex items-center gap-3">
              <div className="qq-avatar !h-8 !w-8 !rounded-xl !text-base">Q</div>
              <div>
                <div className="text-[15px] font-semibold tracking-tight text-slate-800">
                  飞鹤小Q · 智能问数
                </div>
                <div className="text-[11px] text-slate-400">交给小Q，你可以相信我</div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {headerHealth}
              <UserMenu user={user} onChangePassword={() => setPwdOpen(true)} onLogout={onLogout} />
            </div>
          </header>

          {/* ====================== chat page ====================== */}
          {page === "chat" && (
            <>
              <section ref={viewportRef} className="flex-1 overflow-y-auto py-5" style={{ paddingLeft: 80, paddingRight: 80 }}>
                {turns.length === 0 ? (
                  <Hero
                    suggestions={boot.suggestions}
                    onPick={(q) => submit(q)}
                    dataRange={boot.data_range}
                    metricsCount={boot.metrics_count}
                    tablesCount={boot.tables_count}
                  />
                ) : (
                  <div className="space-y-5 pb-3">
                    {turns.map((turn) => (
                      <div key={turn.id} className="space-y-2">
                        <div className="flex w-full justify-end">
                          <div className="qq-bubble-user">{turn.question}</div>
                        </div>
                        <ErrorBoundary inline resetKeys={[turn.id, turn.pending, turn.error, turn.result]}>
                        <AnswerCard
                          turn={turn}
                          onPickSuggestion={(s) => submit(s)}
                          onPickClarify={(label) => submit(label)}
                          onFeedback={async (vote) => {
                            const r = turn.result;
                            if (!r?.trace_id) return { ok: false, msg: "无结果可反馈" };
                            const cid = r.conversation_id || activeId;
                            if (!cid || cid === DRAFT_KEY) return { ok: false, msg: "会话未保存" };
                            try {
                              const res = await api.chatFeedback(cid, r.trace_id, vote);
                              return {
                                ok: true,
                                msg: vote === "up"
                                  ? (res.adopted ? "已沉淀为范例，同类问题会更准" : "已记录")
                                  : "已记录，将用于优化",
                              };
                            } catch (e: any) {
                              return { ok: false, msg: friendlyError(e) };
                            }
                          }}
                          onPushFeishu={async () => {
                            const r = turn.result;
                            if (!r) return { ok: false, msg: "无结果" };
                            // 推送策略：
                            //   · admin 账号（含 admin@feihe.com）通常不是飞鹤真人邮箱，
                            //     直接用自己的 email 去飞书 batch_get_id 拉不到 open_id → 必失败。
                            //     所以管理员每次推送必须显式输入目标飞书账号。
                            //   · 普通用户用自己绑定的飞书邮箱（user.email），后端兜底。
                            let target_email: string | undefined = undefined;
                            const isAdmin = user.role === "admin" || /^admin(@|$)/.test(user.username || "");
                            if (isAdmin) {
                              const seed = (user.email && user.email.includes("@") && !/^admin(@|$)/.test(user.email)) ? user.email : "";
                              const input = window.prompt(
                                "请输入目标飞书账号邮箱（用于推送到对方飞书私信）：",
                                seed,
                              );
                              if (input === null) return { ok: false, msg: "已取消" };
                              const trimmed = input.trim();
                              if (!trimmed) return { ok: false, msg: "未输入邮箱" };
                              if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) {
                                return { ok: false, msg: "邮箱格式不合法" };
                              }
                              target_email = trimmed;
                            }
                            try {
                              const res = await api.feishuPush({
                                title: turn.question.slice(0, 30) || "飞鹤经营分析",
                                narrative: r.answer.narrative,
                                highlights: r.answer.highlights || [],
                                rows_preview: (r.answer.table?.display_rows || []).slice(0, 5).map((row) => row.join(" | ")),
                                ...(target_email ? { user_email: target_email } : {}),
                              });
                              // 后端失败时返回 HTTP 200 + ok:false，必须按 ok 判定，不能只看是否抛错
                              if (res && res.ok === true) return { ok: true, msg: target_email ? `✓ 已推送给 ${target_email}` : "✓ 已推送" };
                              const m = (res && res.user_message) || "推送失败，请稍后重试或联系管理员";
                              return { ok: false, msg: "× " + m.slice(0, 60) };
                            } catch (e: any) {
                              return { ok: false, msg: "× " + friendlyError(e).slice(0, 60) };
                            }
                          }}
                          onDownloadReport={async () => {
                            if (!turn.result) return { ok: false, msg: "无结果" };
                            setReportFor(turn);
                            return { ok: true, msg: "请选模板" };
                          }}
                          onCopySql={() => {
                            const r = turn.result;
                            if (!r) return;
                            navigator.clipboard.writeText(r.sql || "").catch(() => { /* ignore */ });
                          }}
                        />
                        </ErrorBoundary>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <Composer
                value={input}
                onChange={setInput}
                onSubmit={() => submit()}
                disabled={streaming}
                loading={streaming}
                placeholder={turns.length === 0 ? "试试：本月各大区销售额排名" : "继续追问，例如：按城市再下钻 / 看看同比 / 推送给老板"}
                onAbort={abort}
                forceRefresh={forceRefresh}
                onToggleForceRefresh={setForceRefresh}
              />
            </>
          )}

          {/* ====================== admin pages ====================== */}
          {page === "users" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><UsersPage /></section>
          )}
          {page === "logs" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><LogsPage /></section>
          )}
          {page === "report_templates" && (
            <section className="flex-1 overflow-y-auto"><ReportTemplatesPage /></section>
          )}
          {page === "semantic" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><SemanticPage /></section>
          )}
          {page === "permissions" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><PermissionsPage /></section>
          )}
          {page === "llm_settings" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><LLMSettingsPage /></section>
          )}
          {(page === "logs" || page === "users" || page === "permissions" || page === "semantic" || page === "llm_settings") && user.role !== "admin" && (
            <section className="flex flex-1 items-center justify-center text-sm text-slate-400">
              该页面仅管理员可访问
            </section>
          )}
        </main>
      </div>

      <PasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} onChanged={() => { /* noop */ }} />
      <ReportDownloadModal
        open={!!reportFor}
        onClose={() => setReportFor(null)}
        onDownload={async (template_id) => {
          const r = reportFor?.result; if (!r) return;
          const tk = auth.getToken();
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (tk) headers["Authorization"] = "Bearer " + tk;
          const resp = await fetch(api.reportDownloadUrl(), {
            method: "POST", headers,
            body: JSON.stringify({
              question: reportFor!.question, answer: r.answer, plan: r.plan, sql: r.sql,
              template_id: template_id || undefined,
            }),
          });
          if (!resp.ok) {
            // 读取后端友好提示（user_message / detail），不再只显示 HTTP 状态码
            let msg = "报告生成失败，请稍后重试，或联系管理员。";
            try {
              const t = await resp.text();
              const j = t ? JSON.parse(t) : null;
              const d = j && (typeof j.detail === "string" ? j.detail : j.detail?.user_message);
              msg = (j && (j.user_message || d)) || msg;
            } catch { /* 保底友好文案 */ }
            throw new Error(msg);
          }
          const blob = await resp.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          const ts = new Date().toISOString().replace(/\D+/g, "").slice(0, 14);
          a.download = `feihe_report_${ts}.docx`;
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          URL.revokeObjectURL(url);
        }}
      />
    </div>
  );
}
