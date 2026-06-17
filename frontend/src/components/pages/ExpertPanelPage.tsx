/**
 * 专家团 —— 多 skill 编排问数/报告。
 *
 * 决策调度总监（卓见全）理解问题 → 从已选/全部专家里调度 → 各专家基于知识库+真实数据分析
 * → 总监合成报告。用户可勾选参与的专家、开关「出报告」、并自建 skill 任意组合。
 * 页面布局沿用问数页观感（顶部介绍 + 中部消息流 + 底部输入），但承载多专家协同结果。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, friendlyError } from "../../api";
import type { AuthUser, ExpertCard, ExpertTeamBootstrap, ExpertTeamResult } from "../../types";

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

interface Turn {
  id: string;
  question: string;
  pending: boolean;
  result?: ExpertTeamResult;
  error?: string;
}

interface Props {
  user: AuthUser;
  llmProvider?: string | null;
}

/* ----------------------------- 轻量 markdown 渲染（无依赖、纯 React 节点，避免 XSS） --------------------------- */
function inlineBold(text: string, keyPrefix: string) {
  // 仅处理 **bold**，其余原样
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    /^\*\*[^*]+\*\*$/.test(p)
      ? <strong key={`${keyPrefix}-b${i}`} className="font-semibold text-slate-800">{p.slice(2, -2)}</strong>
      : <span key={`${keyPrefix}-t${i}`}>{p}</span>,
  );
}

function MiniMarkdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const nodes: JSX.Element[] = [];
  let list: string[] = [];
  let tableRows: string[][] = [];
  const flushList = () => {
    if (!list.length) return;
    nodes.push(
      <ul key={`ul-${nodes.length}`} className="my-1 ml-4 list-disc space-y-0.5 text-[13px] text-slate-600">
        {list.map((it, i) => <li key={i}>{inlineBold(it, `li${nodes.length}-${i}`)}</li>)}
      </ul>,
    );
    list = [];
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const [head, ...body] = tableRows;
    nodes.push(
      <div key={`tb-${nodes.length}`} className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead><tr>{head.map((h, i) => <th key={i} className="border-b border-slate-200 px-2 py-1 text-left font-medium text-slate-500">{h}</th>)}</tr></thead>
          <tbody>{body.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} className="border-b border-slate-100 px-2 py-1 text-slate-600">{c}</td>)}</tr>)}</tbody>
        </table>
      </div>,
    );
    tableRows = [];
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      if (cells.every((c) => /^-{2,}:?$|^:?-{2,}:?$/.test(c) || c === "")) continue; // 分隔行
      flushList();
      tableRows.push(cells);
      continue;
    }
    flushTable();
    if (/^#{1,6}\s/.test(line)) {
      flushList();
      const level = line.match(/^#+/)![0].length;
      const txt = line.replace(/^#+\s/, "");
      const cls = level <= 1 ? "text-[15px] font-semibold text-slate-800 mt-2"
        : level === 2 ? "text-[14px] font-semibold text-slate-800 mt-2"
        : "text-[13px] font-semibold text-slate-700 mt-1.5";
      nodes.push(<div key={`h-${nodes.length}`} className={cls}>{inlineBold(txt, `h${nodes.length}`)}</div>);
    } else if (/^\s*[-*]\s+/.test(line)) {
      list.push(line.replace(/^\s*[-*]\s+/, ""));
    } else if (/^\s*\d+\.\s+/.test(line)) {
      list.push(line.replace(/^\s*\d+\.\s+/, ""));
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      nodes.push(<p key={`p-${nodes.length}`} className="my-1 text-[13px] leading-relaxed text-slate-600">{inlineBold(line, `p${nodes.length}`)}</p>);
    }
  }
  flushList(); flushTable();
  return <div className="qq-md">{nodes}</div>;
}

/* ----------------------------- 单专家产出卡片 ----------------------------- */
function ExpertBlock({ c }: { c: ExpertTeamResult["experts"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-slate-100 bg-white">
      <div className="flex items-start gap-2.5 px-3.5 py-2.5">
        <div className="text-lg leading-none">{c.emoji}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-slate-800">{c.name}</span>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{c.profession}</span>
          </div>
          {c.subtask ? <div className="mt-0.5 truncate text-[11px] text-slate-400">子任务：{c.subtask}</div> : null}
          <div className="mt-1.5"><MiniMarkdown text={c.analysis} /></div>
          {c.data && (c.data.sql || c.data.table_preview) ? (
            <div className="mt-1.5">
              <button type="button" onClick={() => setOpen((v) => !v)} className="text-[11px] text-blue-500 hover:underline">
                {open ? "收起数据" : `查看取数明细${c.data.rows ? `（${c.data.rows} 行）` : ""}`}
              </button>
              {open ? (
                <div className="mt-1 space-y-1">
                  {c.data.table_preview ? <pre className="overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600">{c.data.table_preview}</pre> : null}
                  {c.data.sql ? <pre className="overflow-x-auto rounded bg-slate-900/90 p-2 text-[11px] text-slate-100">{c.data.sql}</pre> : null}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- 结果卡片 ----------------------------- */
function ResultCard({ result }: { result: ExpertTeamResult }) {
  if (!result.ok) return <div className="qq-card px-4 py-3 text-sm text-rose-500">{result.error || "调度失败"}</div>;
  return (
    <div className="space-y-3">
      {result.plan ? (
        <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-3.5 py-2.5">
          <div className="flex items-center gap-1.5 text-[12px] font-medium text-blue-600">
            <span>🧭 决策调度总监</span>
            <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px]">{result.route === "fast" ? "快通道" : "慢通道·多专家"}</span>
          </div>
          <div className="mt-1 text-[12.5px] text-slate-600">{result.plan}</div>
        </div>
      ) : null}

      {result.experts.length > 0 ? (
        <div className="space-y-2">
          <div className="text-[11px] font-medium text-slate-400">专家产出（{result.experts.length}）</div>
          {result.experts.map((c) => <ExpertBlock key={c.id} c={c} />)}
        </div>
      ) : null}

      <div className="qq-card px-4 py-3">
        <div className="mb-1 flex items-center gap-1.5 text-[12px] font-semibold text-slate-700">📋 合成报告</div>
        <MiniMarkdown text={result.report} />
      </div>
      {result.elapsed_ms ? <div className="text-right text-[10px] text-slate-300">耗时 {(result.elapsed_ms / 1000).toFixed(1)}s</div> : null}
    </div>
  );
}

/* ===================================================================== 主页面 */
export function ExpertPanelPage({ user, llmProvider }: Props) {
  const [boot, setBoot] = useState<ExpertTeamBootstrap | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set()); // 空 = 自动调度
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [wantReport, setWantReport] = useState(false);
  const [composing, setComposing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", profession: "", instructions: "", emoji: "✨" });
  const [busyMsg, setBusyMsg] = useState("");
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const reload = useCallback(async () => {
    try { setBoot(await api.expertTeamBootstrap()); } catch { /* 静默：仍可问数 */ }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    const el = taRef.current; if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(140, Math.max(44, el.scrollHeight)) + "px";
  }, [input]);

  const scrollDown = useCallback(() => {
    requestAnimationFrame(() => { const el = viewportRef.current; if (el) el.scrollTop = el.scrollHeight; });
  }, []);

  const allExperts: ExpertCard[] = boot ? [...boot.experts, ...boot.user_skills] : [];

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const submit = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || loading) return;
    setInput("");
    const turnId = uid();
    setTurns((p) => [...p, { id: turnId, question, pending: true }]);
    setLoading(true);
    setBusyMsg("决策调度总监正在规划…");
    scrollDown();
    try {
      const result = await api.expertTeamChat({
        question,
        expert_ids: selected.size ? [...selected] : null,
        want_report: wantReport,
        llm_provider: llmProvider ?? undefined,
      });
      setTurns((p) => p.map((t) => (t.id === turnId ? { ...t, pending: false, result } : t)));
    } catch (e: any) {
      setTurns((p) => p.map((t) => (t.id === turnId ? { ...t, pending: false, error: friendlyError(e) } : t)));
    } finally {
      setLoading(false); setBusyMsg(""); scrollDown();
    }
  }, [input, loading, selected, wantReport, llmProvider, scrollDown]);

  const createSkill = useCallback(async () => {
    if (!form.name.trim()) return;
    try {
      const r = await api.expertTeamCreateSkill(form);
      if (r.ok) { setCreateOpen(false); setForm({ name: "", profession: "", instructions: "", emoji: "✨" }); reload(); }
    } catch { /* ignore */ }
  }, [form, reload]);

  const deleteSkill = useCallback(async (id: string) => {
    try { await api.expertTeamDeleteSkill(id); setSelected((p) => { const n = new Set(p); n.delete(id); return n; }); reload(); } catch { /* ignore */ }
  }, [reload]);

  const empty = turns.length === 0;

  return (
    <>
      <section ref={viewportRef} className="flex-1 overflow-y-auto px-4 py-5 sm:px-8 lg:px-16">
        {empty ? (
          <div className="mx-auto w-full max-w-3xl pt-6">
            <div className="qq-avatar mb-4 !h-11 !w-11 !rounded-2xl !text-lg">专</div>
            <h1 className="text-[26px] font-semibold tracking-tight text-slate-800">专家团</h1>
            <p className="mt-2 text-sm text-slate-500">
              决策调度总监统筹，多领域专家协同问数与出报告。默认<b>自动调度</b>；也可勾选指定专家，或自建技能任意组合。
            </p>

            {/* 专家花名册（可勾选） */}
            <div className="mt-6 flex items-center justify-between">
              <div className="text-[12px] font-medium text-slate-400">
                参与专家 {selected.size ? `（已选 ${selected.size}）` : "（未选 = 自动调度）"}
              </div>
              <button type="button" onClick={() => setCreateOpen(true)} className="qq-chip !text-[12px]">+ 新建技能</button>
            </div>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {allExperts.map((e) => {
                const on = selected.has(e.id);
                return (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => toggle(e.id)}
                    className={`flex items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left transition ${
                      on ? "border-blue-300 bg-blue-50/60" : "border-slate-100 bg-white hover:border-slate-200"
                    }`}
                  >
                    <div className="text-xl leading-none">{e.emoji}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[13px] font-medium text-slate-800">{e.name}</span>
                        {!e.is_builtin ? <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-600">自建</span> : null}
                        {on ? <span className="text-[11px] text-blue-500">✓</span> : null}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-slate-500">{e.profession}</div>
                    </div>
                    {!e.is_builtin ? (
                      <span role="button" tabIndex={0} onClick={(ev) => { ev.stopPropagation(); deleteSkill(e.id); }}
                        className="text-[11px] text-slate-300 hover:text-rose-400">删除</span>
                    ) : null}
                  </button>
                );
              })}
            </div>

            {/* 预设工作流 */}
            {boot?.workflows?.length ? (
              <>
                <div className="mt-7 mb-2 text-[12px] font-medium text-slate-400">常用编排</div>
                <div className="space-y-1.5">
                  {boot.workflows.map((w) => (
                    <div key={w.name} className="flex items-center gap-2 text-[12px] text-slate-500">
                      <span className="font-medium text-slate-700">{w.name}</span>
                      <span className="text-slate-300">·</span>
                      <span>{w.flow}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}

            <div className="mt-7 mb-2 text-[12px] font-medium text-slate-400">试试这样问</div>
            <div className="flex flex-wrap gap-2">
              {["东一区销售额为什么下滑？出诊断报告", "本月各大区达成率，低于90%的列出来", "做一份月度综合经营报告"].map((q) => (
                <button key={q} type="button" className="qq-chip" onClick={() => submit(q)}>{q}</button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-3xl space-y-5 pb-3">
            {turns.map((t) => (
              <div key={t.id} className="space-y-2">
                <div className="flex w-full justify-end"><div className="qq-bubble-user">{t.question}</div></div>
                {t.pending ? (
                  <div className="flex items-center gap-2 text-sm text-slate-400">
                    <span className="qq-loading-dot" /><span className="qq-loading-dot" /><span className="qq-loading-dot" />
                    <span className="ml-1">{busyMsg || "专家团协同中…"}</span>
                  </div>
                ) : t.error ? (
                  <div className="qq-card px-4 py-3 text-sm text-rose-500">{t.error}</div>
                ) : t.result ? (
                  <ResultCard result={t.result} />
                ) : null}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 输入区：专家勾选状态条 + 出报告开关 + 文本框 */}
      <div className="border-t bg-white py-3" style={{ borderColor: "#eef1f8", paddingLeft: 64, paddingRight: 64 }}>
        <div className="mx-auto w-full max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
            <span>{selected.size ? `已选 ${selected.size} 位专家` : "自动调度"}</span>
            {!empty ? <button type="button" onClick={() => setCreateOpen(true)} className="text-slate-400 hover:text-blue-500">+ 新建技能</button> : null}
            <label className="ml-auto flex cursor-pointer items-center gap-1 text-slate-500">
              <input type="checkbox" checked={wantReport} onChange={(e) => setWantReport(e.target.checked)} />
              📋 出完整报告
            </label>
          </div>
          <div className="qq-card flex items-end gap-2 px-3 py-2">
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (!composing && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!loading && input.trim()) submit(); } }}
              onCompositionStart={() => setComposing(true)}
              onCompositionEnd={() => setComposing(false)}
              rows={1}
              placeholder="向专家团提问，例如：东一区达成率低于90%的省区和渠道，做归因诊断"
              className="max-h-[140px] min-h-[44px] flex-1 resize-none bg-transparent px-1 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-300"
            />
            <button
              type="button"
              onClick={() => submit()}
              disabled={loading || !input.trim()}
              className="qq-btn-primary shrink-0 px-4 py-2 text-sm disabled:opacity-40"
            >
              {loading ? "调度中…" : "发送"}
            </button>
          </div>
          <div className="mt-1.5 text-center text-[11px] text-slate-300">总监调度 · 多专家协同 · 数据可追溯</div>
        </div>
      </div>

      {/* 新建技能弹窗 */}
      {createOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onClick={() => setCreateOpen(false)}>
          <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 text-[15px] font-semibold text-slate-800">新建技能 / 专家</div>
            <div className="space-y-3">
              <div className="flex gap-2">
                <input value={form.emoji} onChange={(e) => setForm((f) => ({ ...f, emoji: e.target.value }))}
                  className="w-16 rounded-lg border border-slate-200 px-2 py-2 text-center text-sm" placeholder="✨" />
                <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="名称，如：促销ROI专家" />
              </div>
              <input value={form.profession} onChange={(e) => setForm((f) => ({ ...f, profession: e.target.value }))}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="角色定位，如：促销活动效果分析" />
              <textarea value={form.instructions} onChange={(e) => setForm((f) => ({ ...f, instructions: e.target.value }))}
                rows={5} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="该专家的分析方法论 / 关注点 / 输出要求（作为 system 指令）" />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setCreateOpen(false)} className="qq-btn px-3 py-1.5 text-sm">取消</button>
              <button type="button" onClick={createSkill} disabled={!form.name.trim()} className="qq-btn-primary px-3 py-1.5 text-sm disabled:opacity-40">创建</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
