/**
 * EChartView — 一个组件按 ChartMode 渲染所有图表类型。
 * 14 种 mode 中的 12 种由 ECharts 渲染（其余 table/kpi 在外层）。
 *
 * 数据规约：
 *   columns + rows 直接来自 AnswerTable.rows（原始 number），
 *   format 用 display_columns 信息（千分位/万亿/百分号）做 axis label/tooltip。
 */
import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";

import type { AnswerTable, ChartMode, DisplayColumn } from "../types";

interface Props {
  mode: ChartMode;
  table: AnswerTable;
  height?: number;
}

const COLOR_PALETTE = [
  "#5B8DEF", "#8DD2FF", "#FFC761", "#FF8A65", "#A78BFA",
  "#34D399", "#F472B6", "#FACC15", "#60A5FA", "#22D3EE",
  "#FB7185", "#94A3B8",
];

function fmtNumber(value: any, col?: DisplayColumn): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "number" ? value : Number(String(value).replace(/[, %万亿元]/g, ""));
  if (!Number.isFinite(n)) return String(value);
  const f = col?.format || "";
  const d = col?.decimals ?? 2;
  if (f === "percent") return `${(n * 100).toFixed(d)}%`;
  if (f === "currency_cn") {
    if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)} 亿元`;
    if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)} 万元`;
    return `${n.toFixed(d)} 元`;
  }
  if (f === "integer_cn") {
    if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)} 万`;
    return `${Math.round(n).toLocaleString()}`;
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: d });
}

function asNumber(value: any): number {
  if (typeof value === "number") return value;
  const n = Number(String(value).replace(/[, %万亿元]/g, ""));
  return Number.isFinite(n) ? n : 0;
}

export function EChartView({ mode, table, height = 320 }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  const option = useMemo(() => buildOption(mode, table), [mode, table]);

  useEffect(() => {
    if (!ref.current) return;
    if (!chart.current) chart.current = echarts.init(ref.current);
    chart.current.setOption(option, true);
    const onResize = () => chart.current?.resize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [option]);

  useEffect(() => {
    return () => {
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  return <div ref={ref} style={{ width: "100%", height }} />;
}

/* =========================================================================
 * Build ECharts option for each mode
 * ========================================================================= */
function buildOption(mode: ChartMode, table: AnswerTable): any {
  const cols = table.display_columns || [];
  const rows = table.rows || [];
  const dimCols = cols.filter((c) => c.kind === "dimension");
  const timeCols = cols.filter((c) => c.kind === "time");
  const metricCols = cols.filter((c) => c.kind === "metric");
  const xCol = timeCols[0] || dimCols[0] || cols[0];
  const xIdx = xCol ? cols.findIndex((c) => c.key === xCol.key) : 0;

  const xLabels: string[] = rows.map((r) => String(r[xIdx] ?? ""));
  const sharedTooltip: any = {
    trigger: ["pie", "rose", "scatter", "funnel", "map"].includes(mode) ? "item" : "axis",
    confine: true,
    valueFormatter: (v: any) => fmtNumber(v, metricCols[0]),
  };
  const sharedLegend = { type: "scroll" as const, top: 4 };
  const sharedGrid = { top: 36, left: 60, right: 24, bottom: 32 };

  switch (mode) {
    case "bar":
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: sharedGrid,
        legend: sharedLegend,
        xAxis: { type: "category", data: xLabels, axisLabel: { rotate: xLabels.length > 6 ? 30 : 0 } },
        yAxis: { type: "value", axisLabel: { formatter: (v: any) => fmtNumber(v, metricCols[0]) } },
        series: metricCols.map((c) => ({
          name: c.label,
          type: "bar",
          data: rows.map((r) => asNumber(r[cols.findIndex((cc) => cc.key === c.key)])),
          barMaxWidth: 28,
        })),
      };
    case "bar_horizontal":
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: { ...sharedGrid, left: 80 },
        legend: sharedLegend,
        yAxis: { type: "category", data: xLabels, inverse: true },
        xAxis: { type: "value", axisLabel: { formatter: (v: any) => fmtNumber(v, metricCols[0]) } },
        series: metricCols.map((c) => ({
          name: c.label,
          type: "bar",
          data: rows.map((r) => asNumber(r[cols.findIndex((cc) => cc.key === c.key)])),
          barMaxWidth: 22,
        })),
      };
    case "line":
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: sharedGrid,
        legend: sharedLegend,
        xAxis: { type: "category", data: xLabels, boundaryGap: false },
        yAxis: { type: "value", axisLabel: { formatter: (v: any) => fmtNumber(v, metricCols[0]) } },
        series: metricCols.map((c) => ({
          name: c.label,
          type: "line",
          smooth: true,
          symbol: "circle",
          data: rows.map((r) => asNumber(r[cols.findIndex((cc) => cc.key === c.key)])),
        })),
      };
    case "area":
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: sharedGrid,
        legend: sharedLegend,
        xAxis: { type: "category", data: xLabels, boundaryGap: false },
        yAxis: { type: "value", axisLabel: { formatter: (v: any) => fmtNumber(v, metricCols[0]) } },
        series: metricCols.map((c, i) => ({
          name: c.label,
          type: "line",
          smooth: true,
          areaStyle: { opacity: 0.2 + 0.1 * i },
          data: rows.map((r) => asNumber(r[cols.findIndex((cc) => cc.key === c.key)])),
        })),
      };
    case "stacked_bar":
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: sharedGrid,
        legend: sharedLegend,
        xAxis: { type: "category", data: xLabels },
        yAxis: { type: "value", axisLabel: { formatter: (v: any) => fmtNumber(v, metricCols[0]) } },
        series: metricCols.map((c) => ({
          name: c.label,
          type: "bar",
          stack: "total",
          data: rows.map((r) => asNumber(r[cols.findIndex((cc) => cc.key === c.key)])),
        })),
      };
    case "dual_axis": {
      const m1 = metricCols[0]; const m2 = metricCols[1];
      const i1 = cols.findIndex((c) => c.key === m1.key);
      const i2 = cols.findIndex((c) => c.key === m2.key);
      return {
        color: COLOR_PALETTE,
        tooltip: sharedTooltip,
        grid: { ...sharedGrid, right: 60 },
        legend: sharedLegend,
        xAxis: { type: "category", data: xLabels },
        yAxis: [
          { type: "value", name: m1.label, axisLabel: { formatter: (v: any) => fmtNumber(v, m1) } },
          { type: "value", name: m2.label, axisLabel: { formatter: (v: any) => fmtNumber(v, m2) } },
        ],
        series: [
          { name: m1.label, type: "bar", yAxisIndex: 0, data: rows.map((r) => asNumber(r[i1])) },
          { name: m2.label, type: "line", yAxisIndex: 1, smooth: true, data: rows.map((r) => asNumber(r[i2])) },
        ],
      };
    }
    case "pie":
    case "rose": {
      const mIdx = cols.findIndex((c) => c.key === metricCols[0].key);
      return {
        color: COLOR_PALETTE,
        tooltip: { ...sharedTooltip, trigger: "item" },
        legend: { type: "scroll", left: "center", bottom: 4 },
        series: [
          {
            name: metricCols[0].label,
            type: "pie",
            radius: mode === "rose" ? ["18%", "70%"] : ["38%", "70%"],
            roseType: mode === "rose" ? "radius" : undefined,
            center: ["50%", "45%"],
            label: { formatter: "{b}\n{d}%" },
            data: rows.map((r, i) => ({
              name: String(r[xIdx] ?? `项${i + 1}`),
              value: asNumber(r[mIdx]),
            })),
          },
        ],
      };
    }
    case "funnel": {
      const mIdx = cols.findIndex((c) => c.key === metricCols[0].key);
      const data = rows
        .map((r, i) => ({ name: String(r[xIdx] ?? `项${i + 1}`), value: asNumber(r[mIdx]) }))
        .sort((a, b) => b.value - a.value);
      return {
        color: COLOR_PALETTE,
        tooltip: { ...sharedTooltip, trigger: "item" },
        series: [{ type: "funnel", data, sort: "descending", gap: 2, label: { position: "inside" } }],
      };
    }
    case "scatter": {
      const m1 = metricCols[0]; const m2 = metricCols[1];
      const i1 = cols.findIndex((c) => c.key === m1.key);
      const i2 = cols.findIndex((c) => c.key === m2.key);
      return {
        color: COLOR_PALETTE,
        tooltip: { ...sharedTooltip, trigger: "item",
          formatter: (p: any) => `${p.data.name}<br/>${m1.label}: ${fmtNumber(p.data.value[0], m1)}<br/>${m2.label}: ${fmtNumber(p.data.value[1], m2)}` },
        grid: sharedGrid,
        xAxis: { type: "value", name: m1.label, axisLabel: { formatter: (v: any) => fmtNumber(v, m1) } },
        yAxis: { type: "value", name: m2.label, axisLabel: { formatter: (v: any) => fmtNumber(v, m2) } },
        series: [{
          type: "scatter",
          symbolSize: 14,
          data: rows.map((r, i) => ({
            name: String(r[xIdx] ?? `点${i + 1}`),
            value: [asNumber(r[i1]), asNumber(r[i2])],
          })),
        }],
      };
    }
    case "heatmap": {
      const xDim = dimCols[0];
      const yDim = dimCols[1];
      const mCol = metricCols[0];
      const xI = cols.findIndex((c) => c.key === xDim.key);
      const yI = cols.findIndex((c) => c.key === yDim.key);
      const mI = cols.findIndex((c) => c.key === mCol.key);
      const xs = Array.from(new Set(rows.map((r) => String(r[xI] ?? ""))));
      const ys = Array.from(new Set(rows.map((r) => String(r[yI] ?? ""))));
      const data: any[] = rows.map((r) => [
        xs.indexOf(String(r[xI] ?? "")),
        ys.indexOf(String(r[yI] ?? "")),
        asNumber(r[mI]),
      ]);
      const max = data.reduce((m, d) => Math.max(m, d[2]), 0);
      return {
        color: COLOR_PALETTE,
        tooltip: { ...sharedTooltip, trigger: "item",
          formatter: (p: any) => `${xs[p.data[0]]} × ${ys[p.data[1]]}<br/>${mCol.label}: ${fmtNumber(p.data[2], mCol)}` },
        grid: { ...sharedGrid, left: 100, bottom: 60 },
        xAxis: { type: "category", data: xs, splitArea: { show: true } },
        yAxis: { type: "category", data: ys, splitArea: { show: true } },
        visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 6,
          inRange: { color: ["#e6ecff", "#5B8DEF", "#1E3A8A"] } },
        series: [{ type: "heatmap", data }],
      };
    }
    case "map": {
      // 简化版地图：用 ECharts 内置的"雷达式"分布展示。完整中国地图需注册 GeoJSON，
      // 此处用 treemap 形态做"区域分布"近似展示，避免引入大型地图资源。
      const mIdx = cols.findIndex((c) => c.key === metricCols[0].key);
      return {
        color: COLOR_PALETTE,
        tooltip: { ...sharedTooltip, trigger: "item",
          formatter: (p: any) => `${p.name}<br/>${metricCols[0].label}: ${fmtNumber(p.value, metricCols[0])}` },
        series: [{
          type: "treemap",
          roam: false,
          breadcrumb: { show: false },
          label: { formatter: "{b}\n{c}" },
          data: rows.map((r, i) => ({
            name: String(r[xIdx] ?? `区域${i + 1}`),
            value: asNumber(r[mIdx]),
          })),
        }],
      };
    }
    default:
      return {};
  }
}
