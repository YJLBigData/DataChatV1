/**
 * 专家团会话 + 后台编排状态（与问数完全独立）。
 *
 * 设计要点（满足「后台提交、切页/刷新不中断、出结果打红点」）：
 *  · 状态提升到 App 级（本 hook 在 App 里实例化）→ 切页不卸载、轮询不中断。
 *  · 提交走服务端后台任务 `expertChatAsync`（立即拿 job_id + conversation_id），
 *    真正的编排在后端线程池里跑、结果落库；前端只负责轮询展示。
 *  · 轮询器（App 级 setInterval）对所有 running job 拉 `expertJob`，
 *    done/error 写回对应轮；若用户已切走该会话 → 加入 unread（红点）。
 *  · SPA 刷新后用 `expertRunningJobs` 重新挂上仍在跑的后台分析。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api";
import type { ConversationMeta, ExpertTeamResult, ExpertTurn } from "../types";

const DRAFT = "__expert_draft__";
const POLL_MS = 1500;

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

interface JobRef { jobId: string; turnId: string }

/** 把会话详情的 messages（user→assistant 成对）还原成 ExpertTurn[]。 */
function restoreTurns(msgs: any[], runningJobId?: string): ExpertTurn[] {
  const out: ExpertTurn[] = [];
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i];
    if (m.role !== "user") continue;
    const next = msgs[i + 1];
    if (next && next.role === "assistant") {
      const result = (next.payload?.result as ExpertTeamResult | undefined) || undefined;
      out.push({
        id: m.id, question: m.content, pending: false,
        result, error: result ? undefined : (next.content || "无结果"),
      });
      i += 1;
    } else {
      // 末尾有 user 但还没 assistant：要么后台还在跑，要么服务重启丢了 job。
      out.push({
        id: m.id, question: m.content, pending: !!runningJobId, jobId: runningJobId,
        error: runningJobId ? undefined : "后台分析进行中或已结束，请稍后重新打开本会话查看结果。",
      });
    }
  }
  return out;
}

export function useExpertTeam(enabled: boolean, opts: { smartqCubeIds?: string[] } = {}) {
  const smartqContextIds = useMemo(() => [...new Set((opts.smartqCubeIds || []).filter(Boolean))], [opts.smartqCubeIds]);
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turnsByConv, setTurnsByConv] = useState<Record<string, ExpertTurn[]>>({});
  /** cid -> 正在跑的 job 引用（驱动会话「转圈」点 + 轮询）。 */
  const [jobsByConv, setJobsByConv] = useState<Record<string, JobRef>>({});
  /** 完成但用户没在看的会话 — 红点。 */
  const [unread, setUnread] = useState<Set<string>>(new Set());

  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const pollingRef = useRef(false);

  const updateTurns = useCallback((cid: string, mapper: (arr: ExpertTurn[]) => ExpertTurn[]) => {
    setTurnsByConv((prev) => ({ ...prev, [cid]: mapper(prev[cid] || []) }));
  }, []);

  const refreshConversations = useCallback(async () => {
    if (!enabled) return;
    try { setConversations((await api.expertListConversations()).items || []); } catch { /* ignore */ }
  }, [enabled]);

  const reset = useCallback(() => {
    setConversations([]); setActiveId(null); setTurnsByConv({}); setJobsByConv({}); setUnread(new Set());
  }, []);

  /* 登录后：拉会话 + 重挂仍在跑的后台分析。 */
  useEffect(() => {
    if (!enabled) { reset(); return; }
    let cancelled = false;
    (async () => {
      try {
        const cs = await api.expertListConversations();
        if (!cancelled) setConversations(cs.items || []);
      } catch { /* ignore */ }
      try {
        const r = await api.expertRunningJobs();
        if (!cancelled && r.items?.length) {
          setJobsByConv((prev) => {
            const next = { ...prev };
            for (const j of r.items) {
              if (j.conversation_id && j.job_id) next[j.conversation_id] = { jobId: j.job_id, turnId: "" };
            }
            return next;
          });
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [enabled, reset]);

  /* ----------------------------------------------------- 轮询所有 running job */
  useEffect(() => {
    if (!enabled) return;
    const hasJobs = Object.keys(jobsByConv).length > 0;
    if (!hasJobs) return;

    const tick = async () => {
      if (pollingRef.current) return;
      pollingRef.current = true;
      try {
        const entries = Object.entries(jobsByConv);
        for (const [cid, ref] of entries) {
          let res;
          try { res = await api.expertJob(ref.jobId); }
          catch { continue; }  // 网络抖动：下次再试
          const status = res.status;
          if (status === "running" || status === "queued") {
            if (res.events?.length) {
              updateTurns(cid, (arr) => arr.map((t) =>
                (t.jobId === ref.jobId || t.id === ref.turnId) ? { ...t, events: res.events } : t));
            }
            continue;
          }
          // 终态：done / error / missing / cancelled → 写回该轮、摘掉 job
          const interrupted = status === "missing" || (res.ok === false && status !== "error");
          const cancelled = status === "cancelled";
          updateTurns(cid, (arr) => arr.map((t) => {
            if (t.jobId !== ref.jobId && t.id !== ref.turnId) return t;
            if (cancelled) return { ...t, pending: false, error: "已取消该分析。" };
            if (interrupted) return { ...t, pending: false, error: res.error || "分析已中断（服务可能已重启），请重试。" };
            return { ...t, pending: false, result: res.result || undefined, error: res.result?.ok === false ? (res.result.error || "分析失败") : undefined };
          }));
          setJobsByConv((prev) => { const n = { ...prev }; delete n[cid]; return n; });
          if (activeIdRef.current !== cid) {
            setUnread((u) => { const n = new Set(u); n.add(cid); return n; });
          }
          refreshConversations();
        }
      } finally {
        pollingRef.current = false;
      }
    };

    const h = window.setInterval(tick, POLL_MS);
    void tick();
    return () => window.clearInterval(h);
  }, [enabled, jobsByConv, updateTurns, refreshConversations]);

  /* ------------------------------------------------------------------ submit */
  const submit = useCallback(async (
    question: string,
    opts: { expert_ids?: string[] | null; want_report?: boolean; llm_provider?: string | null } = {},
  ): Promise<{ ok: boolean; error?: string }> => {
    const q = (question || "").trim();
    if (!q) return { ok: false, error: "请输入问题" };
    const ownerKey = activeIdRef.current || DRAFT;
    const turnId = uid();
    updateTurns(ownerKey, (arr) => [...arr, { id: turnId, question: q, pending: true, events: [] }]);
    try {
      const r = await api.expertChatAsync({
        question: q,
        expert_ids: opts.expert_ids && opts.expert_ids.length ? opts.expert_ids : null,
        want_report: !!opts.want_report,
        llm_provider: opts.llm_provider ?? undefined,
        smartq_cube_ids: smartqContextIds.length ? smartqContextIds : null,
        conversation_id: activeIdRef.current,
      });
      if (!r.ok || !r.job_id || !r.conversation_id) {
        updateTurns(ownerKey, (arr) => arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: r.error || "提交失败" } : t)));
        return { ok: false, error: r.error || "提交失败" };
      }
      const cid = r.conversation_id;
      // draft → 真实 cid：迁移该轮
      if (ownerKey === DRAFT) {
        setTurnsByConv((prev) => {
          const next = { ...prev };
          const draftArr = next[DRAFT] || [];
          const moving = draftArr.filter((t) => t.id === turnId).map((t) => ({ ...t, jobId: r.job_id }));
          const remain = draftArr.filter((t) => t.id !== turnId);
          if (remain.length) next[DRAFT] = remain; else delete next[DRAFT];
          next[cid] = [...(next[cid] || []), ...moving];
          return next;
        });
        setActiveId(cid);
      } else {
        updateTurns(cid, (arr) => arr.map((t) => (t.id === turnId ? { ...t, jobId: r.job_id } : t)));
      }
      setJobsByConv((prev) => ({ ...prev, [cid]: { jobId: r.job_id!, turnId } }));
      refreshConversations();
      return { ok: true };
    } catch (e: any) {
      updateTurns(ownerKey, (arr) => arr.map((t) => (t.id === turnId ? { ...t, pending: false, error: e?.message || "提交失败" } : t)));
      return { ok: false, error: e?.message || "提交失败" };
    }
  }, [updateTurns, refreshConversations, smartqContextIds]);

  /* ------------------------------------------------------------ open / new */
  const openConversation = useCallback(async (cid: string) => {
    setActiveId(cid);
    setUnread((u) => { if (!u.has(cid)) return u; const n = new Set(u); n.delete(cid); return n; });
    try {
      const detail = await api.expertGetConversation(cid);
      const runningJobId = jobsByConv[cid]?.jobId;
      const restored = restoreTurns(detail.messages || [], runningJobId);
      setTurnsByConv((prev) => {
        // 保留内存里正在 pending 的轮（其 assistant 还没落库）
        const existing = prev[cid] || [];
        const pendings = existing.filter((t) => t.pending && !restored.some((r) => r.id === t.id));
        return { ...prev, [cid]: [...restored, ...pendings] };
      });
    } catch (e: any) {
      setTurnsByConv((prev) => ({ ...prev, [cid]: [{ id: uid(), question: "(加载会话失败)", pending: false, error: e?.message || String(e) }] }));
    }
  }, [jobsByConv]);

  const startNew = useCallback(() => { setActiveId(null); }, []);

  /** 取消某会话正在跑的后台分析（排队中即时取消；运行中 best-effort）。 */
  const cancel = useCallback(async (cid: string) => {
    const ref = jobsByConv[cid];
    if (!ref) return;
    try { await api.expertCancelJob(ref.jobId); } catch { /* ignore */ }
    setJobsByConv((prev) => { const n = { ...prev }; delete n[cid]; return n; });
    updateTurns(cid, (arr) => arr.map((t) =>
      (t.jobId === ref.jobId || t.id === ref.turnId) && t.pending ? { ...t, pending: false, error: "已取消该分析。" } : t));
  }, [jobsByConv, updateTurns]);

  const renameConversation = useCallback(async (cid: string, title: string) => {
    try { await api.expertRenameConversation(cid, title); refreshConversations(); } catch { /* ignore */ }
  }, [refreshConversations]);

  const deleteConversation = useCallback(async (cid: string) => {
    try {
      await api.expertDeleteConversation(cid);
      if (cid === activeIdRef.current) setActiveId(null);
      setTurnsByConv((p) => { const n = { ...p }; delete n[cid]; return n; });
      setJobsByConv((p) => { const n = { ...p }; delete n[cid]; return n; });
      setUnread((p) => { if (!p.has(cid)) return p; const n = new Set(p); n.delete(cid); return n; });
      refreshConversations();
    } catch { /* ignore */ }
  }, [refreshConversations]);

  const activeTurns = (activeId ? turnsByConv[activeId] : turnsByConv[DRAFT]) || [];
  const streamingCids = new Set(Object.keys(jobsByConv));

  return {
    conversations, refreshConversations,
    activeId, setActiveId,
    turns: activeTurns, turnsByConv,
    submit, openConversation, startNew, renameConversation, deleteConversation, cancel,
    unread, streamingCids, reset,
  };
}

export type ExpertTeamHook = ReturnType<typeof useExpertTeam>;
