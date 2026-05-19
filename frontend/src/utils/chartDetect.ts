/**
 * 图表能力检测 — 根据 AnswerTable / AnswerChart 自动判定 14 种图表的可用性。
 *
 * 输入：后端返回的 AnswerPayload.table + AnswerPayload.chart
 * 输出：
 *   modes:     所有 14 种 mode 的 meta（label + 可用性 + 灰色原因）
 *   defaultMode: 默认展示的 mode（永远是 "table"，可滚动）
 *
 * 设计原则：
 *   - 列表（table）永远可用
 *   - 单值（KPI）：1 行且至少 1 个 metric 列
 *   - 条形 / 柱：≥1 维度列 + ≥1 metric 列 + 行数 ≤ 200
 *   - 折线 / 面积：有时间维度（kind=time）或行数 ≥ 2
 *   - 堆叠柱：≥2 个 metric 列 OR 1 维度 + 1 metric + ≥2 重复维度键值
 *   - 双轴：≥2 metric 且单位/format 至少有一个不同
 *   - 饼 / 玫瑰：1 维度 + 1 metric + 行数 ≤ 12
 *   - 漏斗：1 维度 + 1 metric + 行数 ≤ 8
 *   - 散点：≥2 个数值列
 *   - 热力图：2 个维度 + 1 metric
 *   - 地图：维度名匹配 region/sub_region/省/市
 */
import type { AnswerChart, AnswerTable, ChartMode, DisplayColumn } from "../types";

export interface ChartModeMeta {
  id: ChartMode;
  label: string;
  description: string;
  enabled: boolean;
  reason: string;          // 置灰时的提示
}

const MAP_DIM_PATTERN = /(region|sub_region|province|city|district|大区|省区|城市|地区|区域)/i;

interface Shape {
  dimCols: DisplayColumn[];
  timeCols: DisplayColumn[];
  metricCols: DisplayColumn[];
  rowCount: number;
}

function shape(table: AnswerTable | undefined): Shape {
  const cols = table?.display_columns || [];
  return {
    dimCols: cols.filter((c) => c.kind === "dimension"),
    timeCols: cols.filter((c) => c.kind === "time"),
    metricCols: cols.filter((c) => c.kind === "metric"),
    rowCount: table?.row_count ?? 0,
  };
}

export function detectChartModes(table: AnswerTable | undefined, _chart?: AnswerChart): ChartModeMeta[] {
  const s = shape(table);
  const noData = s.rowCount === 0 || (s.metricCols.length === 0);

  const ALL: Array<{ id: ChartMode; label: string; description: string; eval: () => { ok: boolean; reason: string } }> = [
    {
      id: "table", label: "列表", description: "完整数据，可滚动",
      eval: () => ({ ok: (table?.rows?.length ?? 0) > 0 || (table?.display_rows?.length ?? 0) > 0, reason: "暂无数据行" }),
    },
    {
      id: "kpi", label: "指标卡", description: "突出关键数值",
      eval: () => ({
        ok: !noData && s.rowCount === 1 && s.metricCols.length >= 1,
        reason: "适用于结果只有 1 行的指标汇总",
      }),
    },
    {
      id: "bar", label: "柱图", description: "维度对比",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 && s.rowCount >= 2 && s.rowCount <= 200,
        reason: "需要 ≥1 维度 + ≥1 指标 + 2-200 行",
      }),
    },
    {
      id: "bar_horizontal", label: "条形图", description: "类目较多时更易读",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 && s.rowCount >= 2 && s.rowCount <= 200,
        reason: "需要 ≥1 维度 + ≥1 指标 + 2-200 行",
      }),
    },
    {
      id: "line", label: "折线图", description: "时间趋势",
      eval: () => ({
        ok: !noData && (s.timeCols.length >= 1 || (s.dimCols.length >= 1 && s.rowCount >= 3)) && s.metricCols.length >= 1,
        reason: "需要时间维度（或 ≥3 行的有序维度）+ 指标",
      }),
    },
    {
      id: "area", label: "面积图", description: "突出累计",
      eval: () => ({
        ok: !noData && (s.timeCols.length >= 1 || s.rowCount >= 3) && s.metricCols.length >= 1,
        reason: "需要时间或 ≥3 行 + 指标",
      }),
    },
    {
      id: "stacked_bar", label: "堆叠柱图", description: "多指标同维度叠加",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 2,
        reason: "需要 1 维度 + ≥2 指标",
      }),
    },
    {
      id: "dual_axis", label: "双轴图", description: "金额 + 比率混排",
      eval: () => {
        const ok =
          !noData && s.dimCols.length >= 1 && s.metricCols.length >= 2 &&
          new Set(s.metricCols.slice(0, 2).map((c) => c.format || c.unit || "")).size >= 2;
        return { ok, reason: "需要 1 维度 + 2 个不同单位/格式的指标" };
      },
    },
    {
      id: "pie", label: "饼图", description: "份额占比",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 && s.rowCount >= 2 && s.rowCount <= 12,
        reason: "需要 1 维度 + 1 指标 + 2-12 类",
      }),
    },
    {
      id: "rose", label: "玫瑰图", description: "份额 + 强调差异",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 && s.rowCount >= 2 && s.rowCount <= 12,
        reason: "需要 1 维度 + 1 指标 + 2-12 类",
      }),
    },
    {
      id: "funnel", label: "漏斗图", description: "转化漏斗",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 && s.rowCount >= 2 && s.rowCount <= 8,
        reason: "需要 1 维度 + 1 指标 + 2-8 行",
      }),
    },
    {
      id: "scatter", label: "散点图", description: "两个指标的关系",
      eval: () => ({
        ok: !noData && s.metricCols.length >= 2 && s.rowCount >= 3,
        reason: "需要 ≥2 指标 + ≥3 行",
      }),
    },
    {
      id: "heatmap", label: "热力图", description: "二维交叉分布",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 2 && s.metricCols.length >= 1 && s.rowCount >= 4,
        reason: "需要 ≥2 维度 + 1 指标 + ≥4 行",
      }),
    },
    {
      id: "map", label: "地图", description: "区域分布",
      eval: () => ({
        ok: !noData && s.dimCols.length >= 1 && s.metricCols.length >= 1 &&
            s.dimCols.some((c) => MAP_DIM_PATTERN.test(c.key) || MAP_DIM_PATTERN.test(c.label)),
        reason: "需要 1 个区域维度（大区/省区/城市）+ 1 指标",
      }),
    },
  ];

  return ALL.map((m) => {
    const r = m.eval();
    return { id: m.id, label: m.label, description: m.description, enabled: r.ok, reason: r.reason };
  });
}

/* 根据 chart.type 返回的字符串选一个建议默认 mode；当前我们永远默认列表。 */
export function defaultChartMode(_modes: ChartModeMeta[]): ChartMode {
  return "table";
}
