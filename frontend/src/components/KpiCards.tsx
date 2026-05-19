import type { AnswerTable } from "../types";

interface Props { table: AnswerTable }

/** 指标卡 — 把第 1 行的所有 metric 列展示成大数字卡片。 */
export function KpiCards({ table }: Props) {
  const cols = table.display_columns || [];
  const row0 = table.rows?.[0];
  const display0 = table.display_rows?.[0];
  if (!row0) return <div className="rounded-xl bg-slate-50 p-6 text-center text-sm text-slate-400">暂无数据</div>;
  const metrics = cols.map((c, i) => ({ col: c, idx: i })).filter((x) => x.col.kind === "metric");
  if (!metrics.length) return <div className="rounded-xl bg-slate-50 p-6 text-center text-sm text-slate-400">无可展示的指标</div>;
  return (
    <div className={`grid gap-3 ${metrics.length === 1 ? "grid-cols-1" : metrics.length === 2 ? "grid-cols-2" : "grid-cols-2 md:grid-cols-3"}`}>
      {metrics.map((m) => (
        <div key={m.col.key} className="rounded-2xl border bg-white px-5 py-4" style={{ borderColor: "#e6ecf6" }}>
          <div className="text-xs text-slate-500">{m.col.label}</div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-slate-800">
            {display0?.[m.idx] ?? row0[m.idx] ?? "—"}
          </div>
          {m.col.unit && <div className="mt-1 text-[11px] text-slate-400">单位：{m.col.unit}</div>}
        </div>
      ))}
    </div>
  );
}
