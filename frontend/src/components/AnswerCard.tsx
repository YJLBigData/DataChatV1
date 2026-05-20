import { lazy, Suspense, useMemo, useState } from "react";

import type { ChartMode, ChatTurn } from "../types";
import { detectChartModes } from "../utils/chartDetect";
import { ChartSwitcher } from "./ChartSwitcher";
// 懒加载：echarts(chart-vendor ~1.1MB) 只在真正切到图表时才下载，
// 默认"列表"视图首屏不再背负这块体积（P2 构建体积优化）。
const EChartView = lazy(() => import("./EChartView").then((m) => ({ default: m.EChartView })));
import { KpiCards } from "./KpiCards";
import { StagePill } from "./StagePill";
import { TableView } from "./TableView";

interface Props {
  turn: ChatTurn;
  onPickSuggestion: (q: string) => void;
  onPickClarify: (label: string) => void;
  onPushFeishu: () => Promise<{ ok: boolean; msg: string }>;
  onDownloadReport: () => Promise<{ ok: boolean; msg: string }>;
  onCopySql: () => void;
}

/**
 * 高管经营分析卡片：
 *   - 顶部：narrative + highlights + risk_notes
 *   - 中部：图表中心（默认列表，可切换 13 种图表，灰色 = 不支持）
 *   - 底部：口径详情、SQL、操作（复制 SQL / 推送飞书 / 下载 DOCX）+ 推荐追问
 */
export function AnswerCard({ turn, onPickSuggestion, onPickClarify, onPushFeishu, onDownloadReport, onCopySql }: Props) {
  const result = turn.result;
  const answer = result?.answer;

  /** 始终默认列表 */
  const [mode, setMode] = useState<ChartMode>("table");
  const [showDetail, setShowDetail] = useState(false);
  const [pushState, setPushState] = useState<{ msg: string; ok: boolean } | null>(null);
  const [reportState, setReportState] = useState<{ msg: string; ok: boolean } | null>(null);

  const modes = useMemo(
    () => detectChartModes(answer?.table as any, answer?.chart),
    [answer?.table, answer?.chart],
  );

  if (turn.error) {
    return (
      <div className="qq-card w-full border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600">
        <div className="font-medium">问数失败</div>
        <div className="mt-1 text-xs">{turn.error}</div>
      </div>
    );
  }
  if (turn.pending && !result) {
    return (
      <div className="qq-card w-full px-4 py-3">
        <StagePill events={turn.events} pending />
      </div>
    );
  }
  if (!answer || !result) return null;

  /* ---------- Clarify ---------- */
  if (answer.needs_clarify) {
    return (
      <div className="qq-card w-full px-4 py-4">
        <div className="flex items-start gap-3">
          <div className="qq-avatar !h-9 !w-9 !rounded-xl !text-base">Q</div>
          <div className="flex-1">
            <div className="text-[13px] font-semibold text-slate-700">需要确认</div>
            <div className="mt-1 text-sm leading-7 text-slate-700">{answer.narrative}</div>
            {(answer.clarify_options || []).length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {answer.clarify_options!.map((opt, i) => (
                  <button key={i} className="qq-chip" onClick={() => onPickClarify(opt.label || opt.key || "")}>
                    {opt.label || opt.key}
                    {opt.hint ? <span className="text-slate-400">（{opt.hint}）</span> : null}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="qq-card w-full">
      {/* head */}
      <div className="flex items-start gap-3 px-4 pt-4">
        <div className="qq-avatar !h-9 !w-9 !rounded-xl !text-base">Q</div>
        <div className="flex-1">
          <div className="text-[13px] font-semibold text-slate-700">飞鹤小Q · 经营分析</div>
          <div className="mt-1 text-[15px] leading-7 text-slate-800">{answer.narrative}</div>
          {answer.highlights?.length ? (
            <ul className="mt-2 space-y-1 text-sm leading-7 text-slate-600">
              {answer.highlights.map((h, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500" />
                  <span>{h}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {answer.risk_notes?.length ? (
            <div className="mt-3 rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2 text-[12.5px] leading-6 text-amber-700">
              <div className="mb-0.5 font-medium">风险与提示</div>
              {answer.risk_notes.map((r, i) => <div key={i}>· {r}</div>)}
            </div>
          ) : null}
        </div>
      </div>

      {/* chart center */}
      <div className="mt-3 border-t" style={{ borderColor: "#eef1f8" }}>
        <div className="flex items-center justify-between gap-2 px-4 py-2">
          <div className="flex items-center gap-2">
            <ChartSwitcher modes={modes} current={mode} onChange={setMode} />
            <span className="text-xs text-slate-400">共 {answer.table?.row_count ?? 0} 行</span>
          </div>
          <div className="flex items-center gap-1.5">
            {result.cached ? (
              <span className="qq-pill-grey">命中缓存</span>
            ) : (
              (() => {
                const totalS = (result.elapsed_ms / 1000).toFixed(2);
                // 把所有"等待 LLM"阶段（plan / answer）的耗时累加
                const llmMs = (turn.events || []).reduce((acc, ev) => {
                  const w = (ev.payload as any)?.llm_wait_ms;
                  return acc + (typeof w === "number" ? w : 0);
                }, 0);
                const llmS = (llmMs / 1000).toFixed(2);
                return (
                  <>
                    <span className="qq-pill-grey" title="后端从收到问题到返回答案的总耗时">总耗时 {totalS}s</span>
                    {llmMs > 0 && (
                      <span className="qq-pill-grey" title="其中等待大模型反馈（规划 + 整理）累计耗时">🤖 LLM {llmS}s</span>
                    )}
                  </>
                );
              })()
            )}
            {result.plan?.calculation && <span className="qq-pill-blue">{result.plan.calculation}</span>}
          </div>
        </div>
        <div className="px-4 pb-3">
          {(() => {
            // 兜底：保证 answer.table 至少是有效结构，避免子组件 .map(undefined) 崩溃
            const safeTable = {
              columns: answer.table?.columns || [],
              rows: answer.table?.rows || [],
              display_columns: answer.table?.display_columns || [],
              display_rows: answer.table?.display_rows || [],
              row_count: answer.table?.row_count ?? 0,
              elapsed_ms: answer.table?.elapsed_ms ?? 0,
            };
            if (mode === "table") return <TableView table={safeTable} />;
            if (mode === "kpi") return <KpiCards table={safeTable} />;
            return (
              <Suspense fallback={<div className="py-10 text-center text-xs text-slate-400">图表加载中…</div>}>
                <EChartView mode={mode} table={safeTable} height={340} />
              </Suspense>
            );
          })()}
        </div>
      </div>

      {/* explainability + actions */}
      <div className="border-t px-4 py-2" style={{ borderColor: "#eef1f8" }}>
        <div className="flex flex-wrap items-center gap-2">
          <button className="qq-btn-ghost px-2 py-1 text-xs" onClick={() => setShowDetail((s) => !s)}>
            {showDetail ? "收起细节" : "展开口径与 SQL"}
          </button>
          {result.plan?.metric ? <span className="qq-pill-grey">{result.plan.metric}</span> : null}
          {(answer.explainability?.used_tables || []).map((t) => (
            <span key={t} className="qq-pill-grey">{t.split(".").slice(-1)[0]}</span>
          ))}
          {typeof answer.explainability?.confidence === "number" && (
            <span className="qq-pill-grey">置信度 {(answer.explainability.confidence * 100).toFixed(0)}%</span>
          )}

          <div className="ml-auto flex items-center gap-1.5">
            <button className="qq-btn px-2 py-1 text-xs" onClick={() => onCopySql()} title="复制 SQL">
              复制 SQL
            </button>
            <button
              className="qq-btn px-2 py-1 text-xs"
              onClick={async () => {
                setPushState({ msg: "推送中…", ok: true });
                const r = await onPushFeishu();
                setPushState(r);
                setTimeout(() => setPushState(null), 4000);
              }}
              title="推送到飞书"
            >
              {pushState?.msg ?? "推送飞书"}
            </button>
            <button
              className="qq-btn px-2 py-1 text-xs"
              onClick={async () => {
                setReportState({ msg: "生成中…", ok: true });
                const r = await onDownloadReport();
                setReportState(r);
                setTimeout(() => setReportState(null), 4000);
              }}
              title="下载 DOCX 报告"
            >
              {reportState?.msg ?? "下载报告"}
            </button>
          </div>
        </div>

        {showDetail && (
          <div className="mt-2 rounded-xl border bg-slate-50 px-3 py-2 text-[12px] leading-6 text-slate-600" style={{ borderColor: "#eef1f8" }}>
            <div className="mb-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
              {answer.explainability?.metric_definition?.label && (
                <div>
                  <span className="text-slate-400">指标定义：</span>
                  <span className="text-slate-700">
                    {answer.explainability.metric_definition?.label}
                    （{answer.explainability.metric_definition?.expression}）
                  </span>
                </div>
              )}
              {answer.explainability?.metric_definition?.table && (
                <div>
                  <span className="text-slate-400">来源表：</span>
                  <span className="text-slate-700">{answer.explainability.metric_definition.table}</span>
                </div>
              )}
              <div>
                <span className="text-slate-400">维度筛选：</span>
                <span className="text-slate-700">
                  {(answer.explainability?.filters_applied || []).length
                    ? (answer.explainability!.filters_applied || []).map((f: any) => `${f.dimension}=${(f.values || []).join(",")}`).join(" / ")
                    : "（无）"}
                </span>
              </div>
              <div>
                <span className="text-slate-400">分组：</span>
                <span className="text-slate-700">{(answer.explainability?.group_by || []).join(", ") || "（无）"}</span>
              </div>
              <div className="sm:col-span-2">
                <span className="text-slate-400">规划理由：</span>
                <span className="text-slate-700">{answer.explainability?.reasoning || result.plan?.reasoning || "（无）"}</span>
              </div>
            </div>
            <details className="mt-2">
              <summary className="cursor-pointer text-blue-500">查看生成的 SQL</summary>
              <pre className="mt-2 overflow-auto rounded-lg bg-white px-3 py-2 text-[11.5px] text-slate-700">{result.sql || "（无 SQL）"}</pre>
            </details>
          </div>
        )}
      </div>

      {/* trace stages */}
      <div className="border-t px-4 py-2" style={{ borderColor: "#eef1f8" }}>
        <StagePill events={turn.events} pending={false} />
      </div>

      {/* suggestions */}
      {answer.suggestions?.length ? (
        <div className="border-t px-4 py-2.5" style={{ borderColor: "#eef1f8" }}>
          <div className="text-[11px] text-slate-400">推荐继续问</div>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {answer.suggestions.map((s, i) => (
              <button key={i} className="qq-chip" onClick={() => onPickSuggestion(s)}>{s}</button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
