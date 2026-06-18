/**
 * 专家团 —— 多 skill 编排问数/报告。
 *
 * 决策调度总监（卓见全）理解问题 → 从已选/全部专家里调度 → 各专家基于知识库+真实数据分析
 * → 总监合成报告。用户可勾选参与的专家、开关「出报告」、自建 skill 任意组合。
 * 内置专家与自建 skill 一视同仁支持增删改查：内置「改/删」以覆盖落库（删=隐藏，可还原默认）。
 * 页面布局沿用问数页观感（顶部介绍 + 中部消息流 + 底部输入），但承载多专家协同结果。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, friendlyError } from "../../api";
import { toast } from "../../shared/toast";
import { confirmDialog } from "../../shared/dialog";
import { MemberCard } from "../expert/MemberCard";
import { MemberEditor, EMPTY_EDITOR, type EditorState } from "../expert/MemberEditor";
import { ResultCard } from "../expert/ResultCard";
import type { ExpertTeamHook } from "../../hooks/useExpertTeam";
import type { AuthUser, ExpertCard, ExpertTeamBootstrap, ExpertTurn } from "../../types";

interface Props { user: AuthUser; llmProvider?: string | null; expert: ExpertTeamHook }

/** 把后台 job 的进度事件翻译成一句中文忙碌提示。 */
function busyText(t: ExpertTurn): string {
  const ev = t.events && t.events[t.events.length - 1];
  if (!ev) return "专家团协同中…";
  if (ev.stage === "director" && ev.status === "routing") return "决策调度总监正在规划…";
  if (ev.stage === "director" && ev.status === "planned") return "已规划调度，专家开始分析…";
  if (ev.stage === "expert" && ev.status === "start") return `${ev.name || "专家"} 分析中…`;
  if (ev.stage === "expert" && ev.status === "data") return "正在取数…";
  if (ev.stage === "director" && ev.status === "synthesizing") return "总监正在合成报告…";
  return "专家团协同中…";
}

/* ===================================================================== 主页面 */
export function ExpertPanelPage({ user, llmProvider, expert }: Props) {
  void user;  // 暂未在本页直接使用，保留以备权限/个性化扩展
  const [boot, setBoot] = useState<ExpertTeamBootstrap | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [input, setInput] = useState("");
  const [wantReport, setWantReport] = useState(false);
  const [composing, setComposing] = useState(false);
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  // 会话轮次/进行态来自 App 级 hook（切页/刷新不中断；红点统一管理）。
  const turns = expert.turns;
  const activeBusy = turns.some((t) => t.pending);

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
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next;
  });

  /* ----- 编辑器：新建 / 编辑（含内置专家拉取完整内容） ----- */
  const openCreate = () => setEditor({ ...EMPTY_EDITOR, open: true, mode: "create" });
  const openEdit = useCallback(async (m: ExpertCard) => {
    setEditor({ ...EMPTY_EDITOR, open: true, mode: "edit", id: m.id, loading: true,
      isBuiltin: m.is_builtin, isDirector: m.is_director, hasOverride: !!m.has_override });
    try {
      const r = await api.expertTeamGetMember(m.id);
      if (r.ok && r.member) {
        const d = r.member;
        setEditor((e) => ({ ...e, loading: false, name: d.name, profession: d.profession,
          emoji: d.emoji, instructions: d.instructions || "", hasOverride: !!d.has_override }));
      } else {
        setEditor((e) => ({ ...e, loading: false, err: r.error || "加载失败" }));
      }
    } catch (err: any) { setEditor((e) => ({ ...e, loading: false, err: friendlyError(err) })); }
  }, []);

  const saveEditor = useCallback(async () => {
    if (!editor.name.trim()) { setEditor((e) => ({ ...e, err: "名称不能为空" })); return; }
    setEditor((e) => ({ ...e, saving: true, err: "" }));
    try {
      const payload = { name: editor.name, profession: editor.profession, instructions: editor.instructions, emoji: editor.emoji };
      const r = editor.mode === "create"
        ? await api.expertTeamCreateSkill(payload)
        : await api.expertTeamUpdateMember(editor.id!, payload);
      if (r.ok) { setEditor(EMPTY_EDITOR); reload(); }
      else setEditor((e) => ({ ...e, saving: false, err: r.error || "保存失败" }));
    } catch (err: any) { setEditor((e) => ({ ...e, saving: false, err: friendlyError(err) })); }
  }, [editor, reload]);

  const resetMember = useCallback(async () => {
    if (!editor.id) return;
    setEditor((e) => ({ ...e, saving: true, err: "" }));
    try { await api.expertTeamResetMember(editor.id); setEditor(EMPTY_EDITOR); reload(); }
    catch (err: any) { setEditor((e) => ({ ...e, saving: false, err: friendlyError(err) })); }
  }, [editor.id, reload]);

  const deleteMember = useCallback(async (m: ExpertCard) => {
    const msg = m.is_builtin ? `隐藏内置专家「${m.name}」？（可在编辑里还原默认）` : `删除自建技能「${m.name}」？`;
    if (!(await confirmDialog({ title: "确认", message: msg, danger: true }))) return;
    try { await api.expertTeamDeleteMember(m.id); setSelected((p) => { const n = new Set(p); n.delete(m.id); return n; }); reload(); }
    catch (e: any) { toast.error("删除失败：" + (e?.message || e)); }
  }, [reload]);

  /* ----- 提问编排（提交到服务端后台任务，切页/刷新不中断） ----- */
  const submit = useCallback(async (q?: string) => {
    const question = (q ?? input).trim();
    if (!question || activeBusy) return;
    setInput("");
    scrollDown();
    const r = await expert.submit(question, {
      expert_ids: selected.size ? [...selected] : null,
      want_report: wantReport,
      llm_provider: llmProvider ?? undefined,
    });
    if (!r.ok && r.error) toast.error(r.error);
    scrollDown();
  }, [input, activeBusy, selected, wantReport, llmProvider, expert, scrollDown]);

  // 轮次变化（含后台轮询写回结果）时滚到底
  useEffect(() => { scrollDown(); }, [turns, scrollDown]);

  const empty = turns.length === 0;

  return (
    <>
      <section ref={viewportRef} className="flex-1 overflow-y-auto px-4 py-5 sm:px-8 lg:px-16">
        {empty ? (
          <div className="mx-auto w-full max-w-3xl pt-6">
            <div className="qq-avatar mb-4 !h-11 !w-11 !rounded-2xl !text-lg">专</div>
            <h1 className="text-[26px] font-semibold tracking-tight text-slate-800">专家团</h1>
            <p className="mt-2 text-sm text-slate-500">
              决策调度总监统筹，多领域专家协同问数与出报告。默认<b>自动调度</b>；可勾选指定专家，也可编辑/隐藏内置专家、自建技能任意组合。
            </p>

            {/* 决策调度总监（可编辑、不可删） */}
            {boot?.director ? (
              <div className="mt-5 flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/50 px-3.5 py-2.5">
                <div className="text-xl">{boot.director.emoji}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-medium text-slate-800">{boot.director.name}</span>
                    <span className="rounded bg-blue-100 px-1.5 text-[10px] text-blue-600">决策调度总监</span>
                    {boot.director.has_override ? <span className="rounded bg-violet-100 px-1 text-[10px] text-violet-600">已改</span> : null}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-slate-500">{boot.director.profession} · 编排调度与报告合成（必选，不可删）</div>
                </div>
                <button type="button" onClick={() => openEdit(boot!.director!)} className="rounded p-1.5 text-slate-400 hover:bg-white hover:text-blue-500" title="编辑总监">✎</button>
              </div>
            ) : null}

            <div className="mt-5 flex items-center justify-between">
              <div className="text-[12px] font-medium text-slate-400">
                参与专家 {selected.size ? `（已选 ${selected.size}）` : "（未选 = 自动调度）"}
              </div>
              <button type="button" onClick={openCreate} className="qq-chip !text-[12px]">+ 新建技能</button>
            </div>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {allExperts.map((e) => (
                <MemberCard key={e.id} m={e} selected={selected.has(e.id)}
                  onToggle={() => toggle(e.id)} onEdit={() => openEdit(e)} onDelete={() => deleteMember(e)} />
              ))}
            </div>

            {boot?.workflows?.length ? (
              <>
                <div className="mt-7 mb-2 text-[12px] font-medium text-slate-400">常用编排</div>
                <div className="space-y-1.5">
                  {boot.workflows.map((w) => (
                    <div key={w.name} className="flex items-center gap-2 text-[12px] text-slate-500">
                      <span className="font-medium text-slate-700">{w.name}</span><span className="text-slate-300">·</span><span>{w.flow}</span>
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
                    <span className="ml-1">{busyText(t)}</span>
                    <span className="ml-1 text-[11px] text-slate-300">· 可切到别的页面，分析在后台继续</span>
                    {expert.activeId ? (
                      <button type="button" onClick={() => expert.cancel(expert.activeId!)}
                        className="ml-1 rounded border border-slate-200 px-1.5 py-0.5 text-[11px] text-slate-400 hover:border-rose-200 hover:text-rose-500">终止</button>
                    ) : null}
                  </div>
                ) : t.error ? (
                  <div className="qq-card px-4 py-3 text-sm text-rose-500">{t.error}</div>
                ) : t.result ? <ResultCard result={t.result} /> : null}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 输入区 */}
      <div className="border-t bg-white py-3" style={{ borderColor: "#eef1f8", paddingLeft: 64, paddingRight: 64 }}>
        <div className="mx-auto w-full max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
            <span>{selected.size ? `已选 ${selected.size} 位专家` : "自动调度"}</span>
            {!empty ? <button type="button" onClick={openCreate} className="text-slate-400 hover:text-blue-500">+ 新建技能</button> : null}
            <label className="ml-auto flex cursor-pointer items-center gap-1 text-slate-500">
              <input type="checkbox" checked={wantReport} onChange={(e) => setWantReport(e.target.checked)} />
              📋 出完整报告
            </label>
          </div>
          <div className="qq-card flex items-end gap-2 px-3 py-2">
            <textarea
              ref={taRef} value={input} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (!composing && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!activeBusy && input.trim()) submit(); } }}
              onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)}
              rows={1} placeholder="向专家团提问，例如：东一区达成率低于90%的省区和渠道，做归因诊断"
              className="max-h-[140px] min-h-[44px] flex-1 resize-none bg-transparent px-1 py-2 text-sm text-slate-700 outline-none placeholder:text-slate-300"
            />
            <button type="button" onClick={() => submit()} disabled={activeBusy || !input.trim()}
              className="qq-btn-primary shrink-0 px-4 py-2 text-sm disabled:opacity-40">{activeBusy ? "调度中…" : "发送"}</button>
          </div>
          <div className="mt-1.5 text-center text-[11px] text-slate-300">总监调度 · 多专家协同 · 数据可追溯</div>
        </div>
      </div>

      {/* 成员编辑器（新建 / 编辑内置专家或自建技能） */}
      <MemberEditor
        editor={editor}
        setEditor={setEditor}
        onSave={saveEditor}
        onReset={resetMember}
        onCancel={() => setEditor(EMPTY_EDITOR)}
      />
    </>
  );
}
