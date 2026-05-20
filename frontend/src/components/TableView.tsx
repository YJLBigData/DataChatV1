import type { AnswerTable } from "../types";

interface Props {
  table: AnswerTable;
  /** 默认 200 行，足够大并支持滚动；不再做硬截断提示。 */
  max?: number;
  /** 高度，默认 420px，超过则内容滚动。 */
  height?: number;
}

/** 列表（透视表）— 滚动到底加载完整 display_rows。
 * 永远展示 display_rows（已格式化为人类可读：万元/%/千分位）。 */
export function TableView({ table, max = 500, height = 420 }: Props) {
  if (!table.display_rows?.length) {
    return <div className="px-3 py-6 text-center text-xs text-slate-400">无数据</div>;
  }
  const cols = table.display_columns;
  const rows = table.display_rows.slice(0, max);
  return (
    <div
      className="overflow-auto rounded-xl border"
      style={{ borderColor: "#eef1f8", maxHeight: height }}
    >
      <table className="qq-table">
        <thead className="sticky top-0 bg-white">
          <tr>
            {cols.map((c) => {
              // metric 列右对齐：和数据右对齐保持一致，避免视觉错位
              const isMetric = c.kind === "metric";
              return (
                <th key={c.key} className={isMetric ? "text-right whitespace-nowrap" : "whitespace-nowrap"}>
                  {c.label}
                  {c.unit ? <span className="ml-1 text-[10px] text-slate-300">({c.unit})</span> : null}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((cell, j) => (
                <td
                  key={j}
                  className={cols[j]?.kind === "metric" ? "tabular-nums text-right whitespace-nowrap" : ""}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.row_count > rows.length && (
        <div className="border-t bg-white px-3 py-2 text-center text-[11px] text-slate-400" style={{ borderColor: "#eef1f8" }}>
          已展示 {rows.length} / {table.row_count} 行（更多请下载报告）
        </div>
      )}
    </div>
  );
}
