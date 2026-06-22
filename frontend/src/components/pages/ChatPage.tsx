/**
 * 问数页（chat）—— Hero / 历史轮次 / AnswerCard 操作 / Composer / 报告下载弹窗。
 * 数据范围（飞鹤数据库 / 智能小Q 数据集）统一由顶部「数据范围」入口选择，问数页不再有
 * 第二处 SmartQ 数据集选择条。状态全部来自 App 级 useChat hook；本组件只负责渲染与
 * "结果卡片上的动作"编排。
 */
import { useEffect, useRef, useState } from "react";

import { api, auth, friendlyError } from "../../api";
import { promptDialog } from "../../shared/dialog";
import { AnswerCard } from "../AnswerCard";
import { Composer } from "../Composer";
import { ErrorBoundary } from "../ErrorBoundary";
import { Hero } from "../Hero";
import { ReportDownloadModal } from "../ReportDownloadModal";
import type { AuthUser, ChatTurn } from "../../types";
import type { ChatHook } from "../../hooks/useChat";

const DRAFT_KEY = "__draft__";

interface Props {
  chat: ChatHook;
  user: AuthUser;
  suggestions: string[];
}

export function ChatPage({ chat, user, suggestions }: Props) {
  const {
    activeId, turns, streaming, input, setInput, forceRefresh, setForceRefresh,
    submit, abort,
  } = chat;
  const [reportFor, setReportFor] = useState<ChatTurn | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const v = viewportRef.current;
    if (!v) return;
    v.scrollTo({ top: v.scrollHeight, behavior: turns.length > 1 ? "smooth" : "auto" });
  }, [turns]);

  return (
    <>
      <section ref={viewportRef} className="flex-1 overflow-y-auto px-4 py-5 sm:px-8 lg:px-20">
        {turns.length === 0 ? (
          <Hero suggestions={suggestions} onPick={(q) => submit(q)} />
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
                      // 安全（P0）：推送内容由后端按 (conversation_id, trace_id) 取可信
                      // 结果生成，前端不再传 narrative/highlights/rows_preview。
                      const cid = r.conversation_id || activeId;
                      if (!r.trace_id || !cid || cid === DRAFT_KEY) return { ok: false, msg: "会话未保存，无法推送" };
                      // 推送策略：
                      //   · admin 账号（含 admin@feihe.com）通常不是飞鹤真人邮箱，
                      //     直接用自己的 email 去飞书 batch_get_id 拉不到 open_id → 必失败。
                      //     所以管理员每次推送必须显式输入目标飞书账号。
                      //   · 普通用户用自己绑定的飞书邮箱（user.email），后端兜底。
                      let target_email: string | undefined = undefined;
                      const isAdmin = user.role === "admin" || /^admin(@|$)/.test(user.username || "");
                      if (isAdmin) {
                        const seed = (user.email && user.email.includes("@") && !/^admin(@|$)/.test(user.email)) ? user.email : "";
                        const inputEmail = await promptDialog({
                          title: "推送到飞书",
                          label: "请输入目标飞书账号邮箱（用于推送到对方飞书私信）：",
                          defaultValue: seed, placeholder: "name@feihe.com",
                        });
                        if (inputEmail === null) return { ok: false, msg: "已取消" };
                        const trimmed = inputEmail.trim();
                        if (!trimmed) return { ok: false, msg: "未输入邮箱" };
                        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) {
                          return { ok: false, msg: "邮箱格式不合法" };
                        }
                        target_email = trimmed;
                      }
                      try {
                        const res = await api.feishuPush({
                          conversation_id: cid,
                          trace_id: r.trace_id,
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
                    onExportExcel={async () => {
                      const r = turn.result;
                      const cid = r?.conversation_id || activeId;
                      if (!r?.trace_id || !cid || cid === DRAFT_KEY) return { ok: false, msg: "会话未保存，无法导出" };
                      try {
                        const res = await api.createExport(cid, r.trace_id);
                        // 背压/队列上限：后端返回 HTTP 200 + ok:false，必须按 ok 判定，不能当成功
                        if (res && res.ok === false) return { ok: false, msg: res.error || "导出未受理，请稍后再试" };
                        window.dispatchEvent(new CustomEvent("datachat:export-submitted"));
                        return { ok: true, msg: "✓ 已加入导出队列" };
                      } catch (e: any) {
                        return { ok: false, msg: friendlyError(e) };
                      }
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

      <ReportDownloadModal
        open={!!reportFor}
        onClose={() => setReportFor(null)}
        onDownload={async (template_id) => {
          const r = reportFor?.result; if (!r) return;
          // 安全（P0）：报告内容由后端按 (conversation_id, trace_id) 取可信结果生成，
          // 前端不再传 question/answer/plan/sql。
          const cid = r.conversation_id || activeId;
          if (!r.trace_id || !cid || cid === DRAFT_KEY) {
            throw new Error("会话未保存，无法生成报告。请稍候重试。");
          }
          const tk = auth.getToken();
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (tk) headers["Authorization"] = "Bearer " + tk;
          const resp = await fetch(api.reportDownloadUrl(), {
            method: "POST", headers,
            body: JSON.stringify({
              conversation_id: cid, trace_id: r.trace_id,
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
    </>
  );
}
