/** 专家团结果卡片 —— 调度说明 + 各专家产出（可展开取数明细）+ 合成报告 + 降级提示。 */
import { useState } from "react";

import { MiniMarkdown } from "../MiniMarkdown";
import type { ExpertTeamResult } from "../../types";

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

export function ResultCard({ result }: { result: ExpertTeamResult }) {
  if (!result.ok) return <div className="qq-card px-4 py-3 text-sm text-rose-500">{result.error || "调度失败"}</div>;
  const warnings = result.warnings || [];
  return (
    <div className="space-y-3">
      {warnings.length > 0 ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-3.5 py-2 text-[12px] text-amber-700">
          ⚠️ 部分环节降级：{warnings.join("；")}
        </div>
      ) : null}
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
