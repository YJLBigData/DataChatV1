"""QueryPlan -> SQL compiler.

Pure deterministic Python code, no LLM. Supports:
- base aggregation
- group_by + filters + order_by + limit
- time_range (relative / absolute / range) — handles year/month split tables
- calculations: yoy_growth, mom_growth, ratio, rank (limit), trend, delta, cumulative

Achievement-rate metrics are special: their `expression` already contains the ratio,
so we just SUM their parts as defined.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

from app.core.semantic import SemanticLayer
from app.core.semantic.layer import MetricDef, TableDef

from .plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange


class CompileError(ValueError):
    pass


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _qident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise CompileError(f"非法标识符：{name!r}")
    return f"`{name}`"


def _qstring(value: str) -> str:
    # MySQL-safe single-quote escaping
    s = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


class PlanCompiler:
    def __init__(self, semantic: SemanticLayer, *, default_limit: int = 100, today: date | None = None):
        self.semantic = semantic
        self.default_limit = default_limit
        self.today = today or date.today()

    # ------------------------------------------------------------ public

    def compile(self, plan: QueryPlan) -> tuple[str, dict[str, Any]]:
        """Returns (sql, mapping_metadata).

        mapping_metadata explains which physical column each output column came from,
        useful for chart axis selection and Chinese display.
        """
        metric_def = self.semantic.metric(plan.metric)
        if not metric_def:
            raise CompileError(f"未知指标：{plan.metric}")
        table_def = self.semantic.table(plan.table or metric_def.table)
        if not table_def:
            raise CompileError(f"未知数据表：{plan.table}")

        if plan.calculation == "yoy_growth":
            return self._compile_yoy(plan, metric_def, table_def)
        if plan.calculation == "mom_growth":
            return self._compile_mom(plan, metric_def, table_def)
        if plan.calculation == "ratio":
            return self._compile_ratio(plan, metric_def, table_def)
        if plan.calculation == "delta":
            return self._compile_delta(plan, metric_def, table_def)
        return self._compile_basic(plan, metric_def, table_def)

    # ------------------------------------------------------------- basic

    def _compile_basic(self, plan: QueryPlan, metric: MetricDef, table: TableDef) -> tuple[str, dict[str, Any]]:
        select_parts: list[str] = []
        meta: dict[str, Any] = {"columns": {}, "metric": metric.name, "table": table.full_name}
        group_cols = self._group_columns(plan, table, meta, select_parts)

        select_parts.append(f"{metric.expression} AS `{metric.name}`")
        meta["columns"][metric.name] = {
            "kind": "metric", "label": metric.label, "unit": metric.unit,
            "format": metric.display_format, "decimals": metric.decimals,
        }
        for extra in plan.extra_metrics:
            ed = self.semantic.metric(extra)
            if not ed or ed.table != table.name:
                continue
            select_parts.append(f"{ed.expression} AS `{ed.name}`")
            meta["columns"][ed.name] = {
                "kind": "metric", "label": ed.label, "unit": ed.unit,
                "format": ed.display_format, "decimals": ed.decimals,
            }

        where = self._build_where(plan, table)
        order_clause = self._build_order(plan, metric)
        limit = plan.limit or (self.default_limit if plan.group_by or plan.calculation == "rank" else 0)

        sql = self._assemble_sql(
            select_parts=select_parts,
            table=table,
            where=where,
            group_cols=group_cols,
            order_clause=order_clause,
            limit=limit,
        )
        return sql, meta

    # ------------------------------------------------------------ ratio (occupancy share)

    def _compile_ratio(self, plan: QueryPlan, metric: MetricDef, table: TableDef) -> tuple[str, dict[str, Any]]:
        if not plan.group_by:
            return self._compile_basic(plan, metric, table)
        select_parts: list[str] = []
        meta: dict[str, Any] = {"columns": {}, "metric": metric.name, "table": table.full_name, "calculation": "ratio"}
        group_cols = self._group_columns(plan, table, meta, select_parts)

        sub = metric.expression
        select_parts.append(f"{sub} AS `{metric.name}`")
        select_parts.append(f"{sub} / NULLIF(SUM({sub}) OVER (), 0) AS `{metric.name}_ratio`")
        meta["columns"][metric.name] = {
            "kind": "metric", "label": metric.label, "unit": metric.unit,
            "format": metric.display_format, "decimals": metric.decimals,
        }
        meta["columns"][f"{metric.name}_ratio"] = {
            "kind": "metric", "label": f"{metric.label} 占比", "unit": "%",
            "format": "percent", "decimals": 2,
        }

        where = self._build_where(plan, table)
        order_clause = f"`{metric.name}` DESC"
        sql = self._assemble_sql(select_parts, table, where, group_cols, order_clause, plan.limit or self.default_limit)
        return sql, meta

    # ------------------------------------------------------ delta (two-metric difference)

    def _compile_delta(self, plan: QueryPlan, metric: MetricDef, table: TableDef) -> tuple[str, dict[str, Any]]:
        """两个指标的差异（被减数 metric − 减数 extra_metrics[0]），可按差异排序取 TopN。

        找不到同表的第二个指标时退回 _compile_basic（绝不报错），由上层当普通
        多指标表展示，避免"差异类问题"直接失败或被迫澄清。
        """
        second: MetricDef | None = None
        for name in plan.extra_metrics:
            ed = self.semantic.metric(name)
            if ed and ed.table == table.name and ed.name != metric.name:
                second = ed
                break
        if second is None:
            return self._compile_basic(plan, metric, table)

        select_parts: list[str] = []
        meta: dict[str, Any] = {"columns": {}, "metric": metric.name, "table": table.full_name, "calculation": "delta"}
        group_cols = self._group_columns(plan, table, meta, select_parts)

        diff_col = "metric_diff"
        select_parts.append(f"{metric.expression} AS `{metric.name}`")
        select_parts.append(f"{second.expression} AS `{second.name}`")
        select_parts.append(f"({metric.expression}) - ({second.expression}) AS `{diff_col}`")
        for m in (metric, second):
            meta["columns"][m.name] = {
                "kind": "metric", "label": m.label, "unit": m.unit,
                "format": m.display_format, "decimals": m.decimals,
            }
        meta["columns"][diff_col] = {
            "kind": "metric",
            "label": f"{metric.label}与{second.label}差异",
            "unit": metric.unit,
            "format": metric.display_format,
            "decimals": metric.decimals,
        }
        meta["diff_metrics"] = [metric.name, second.name]

        where = self._build_where(plan, table)
        # "差异最大" → 默认按差异降序；用户显式要"最小/升序"时尊重 asc
        direction = "ASC" if (plan.order_by and (plan.order_by[0].dir or "").lower() == "asc") else "DESC"
        order_clause = f"`{diff_col}` {direction}"
        limit = plan.limit or (self.default_limit if plan.group_by else 0)
        sql = self._assemble_sql(select_parts, table, where, group_cols, order_clause, limit)
        return sql, meta

    # ----------------------------------------------------------- yoy_growth

    def _compile_yoy(self, plan: QueryPlan, metric: MetricDef, table: TableDef) -> tuple[str, dict[str, Any]]:
        return self._compile_compare(plan, metric, table, kind="yoy")

    def _compile_mom(self, plan: QueryPlan, metric: MetricDef, table: TableDef) -> tuple[str, dict[str, Any]]:
        return self._compile_compare(plan, metric, table, kind="mom")

    def _compile_compare(
        self,
        plan: QueryPlan,
        metric: MetricDef,
        table: TableDef,
        *,
        kind: str,
    ) -> tuple[str, dict[str, Any]]:
        # current period
        cur_year, cur_months = self._resolve_period_year_months(plan.time_range, table)
        if not cur_year or not cur_months:
            return self._compile_basic(plan, metric, table)
        if kind == "yoy":
            prev_year = str(int(cur_year) - 1)
            prev_months = list(cur_months)
            prev_label = "去年同期"
        else:  # mom
            prev_year, prev_months = _shift_months(cur_year, cur_months, -1)
            prev_label = "上月"

        # build subqueries
        meta: dict[str, Any] = {"columns": {}, "metric": metric.name, "table": table.full_name, "calculation": kind}
        select_parts_cur: list[str] = []
        select_parts_prev: list[str] = []
        group_cols_cur: list[str] = []
        group_cols_prev: list[str] = []

        # group columns must be same in both legs
        for dim in plan.group_by:
            d = self.semantic.dimension(dim)
            if not d:
                continue
            col = d.column_in(table.name)
            if not col:
                continue
            select_parts_cur.append(f"{_qident(col)} AS `{dim}`")
            select_parts_prev.append(f"{_qident(col)} AS `{dim}`")
            group_cols_cur.append(_qident(col))
            group_cols_prev.append(_qident(col))
            meta["columns"][dim] = {"kind": "dimension", "label": d.label, "column": col}

        select_parts_cur.append(f"{metric.expression} AS `{metric.name}_current`")
        select_parts_prev.append(f"{metric.expression} AS `{metric.name}_previous`")
        meta["columns"][f"{metric.name}_current"] = {"kind": "metric", "label": f"{metric.label}（本期）", "unit": metric.unit, "format": metric.display_format, "decimals": metric.decimals}
        meta["columns"][f"{metric.name}_previous"] = {"kind": "metric", "label": f"{metric.label}（{prev_label}）", "unit": metric.unit, "format": metric.display_format, "decimals": metric.decimals}

        where_cur = self._build_where_with_period(plan, table, cur_year, cur_months)
        where_prev = self._build_where_with_period(plan, table, prev_year, prev_months)

        cur_sql = self._assemble_sql(select_parts_cur, table, where_cur, group_cols_cur, "", 0, no_limit=True)
        prev_sql = self._assemble_sql(select_parts_prev, table, where_prev, group_cols_prev, "", 0, no_limit=True)

        join_keys = [d for d in plan.group_by if self.semantic.dimension(d) and self.semantic.dimension(d).column_in(table.name)]
        if not join_keys:
            sql = (
                "SELECT cur.`" + f"{metric.name}_current" + "` AS `" + f"{metric.name}_current" + "`,\n"
                "       prev.`" + f"{metric.name}_previous" + "` AS `" + f"{metric.name}_previous" + "`,\n"
                f"       (cur.`{metric.name}_current` - prev.`{metric.name}_previous`) / NULLIF(prev.`{metric.name}_previous`, 0) AS `{metric.name}_growth`\n"
                f"FROM ({cur_sql}) cur\n"
                f"CROSS JOIN ({prev_sql}) prev"
            )
            meta["columns"][f"{metric.name}_growth"] = {"kind": "metric", "label": f"{metric.label}（{ '同比' if kind == 'yoy' else '环比' }增长率）", "unit": "%", "format": "percent", "decimals": 2}
            return sql, meta

        join_clause = " AND ".join(f"cur.`{k}` = prev.`{k}`" for k in join_keys)
        select_keys = ",\n       ".join(f"cur.`{k}` AS `{k}`" for k in join_keys)
        sql = (
            f"SELECT {select_keys},\n"
            f"       cur.`{metric.name}_current` AS `{metric.name}_current`,\n"
            f"       prev.`{metric.name}_previous` AS `{metric.name}_previous`,\n"
            f"       (cur.`{metric.name}_current` - prev.`{metric.name}_previous`) / NULLIF(prev.`{metric.name}_previous`, 0) AS `{metric.name}_growth`\n"
            f"FROM ({cur_sql}) cur\n"
            f"LEFT JOIN ({prev_sql}) prev ON {join_clause}\n"
            f"ORDER BY `{metric.name}_growth` DESC\n"
            f"LIMIT {plan.limit or self.default_limit}"
        )
        meta["columns"][f"{metric.name}_growth"] = {"kind": "metric", "label": f"{metric.label}（{ '同比' if kind == 'yoy' else '环比' }增长率）", "unit": "%", "format": "percent", "decimals": 2}
        return sql, meta

    # ------------------------------------------------------------ helpers

    def _group_columns(
        self,
        plan: QueryPlan,
        table: TableDef,
        meta: dict[str, Any],
        select_parts: list[str],
    ) -> list[str]:
        cols: list[str] = []
        for dim in plan.group_by:
            d = self.semantic.dimension(dim)
            if not d:
                continue
            col = d.column_in(table.name)
            if not col:
                continue
            select_parts.append(f"{_qident(col)} AS `{dim}`")
            cols.append(_qident(col))
            meta["columns"][dim] = {"kind": "dimension", "label": d.label, "column": col}
        # if calc == trend, add time dimension auto
        if plan.calculation == "trend":
            time_alias = "__period"
            if table.is_year_month_split():
                expr = f"CONCAT({_qident(table.time_field_year)}, '-', {_qident(table.time_field_month)})"
            else:
                expr = _qident(table.time_field or "acc_month")
            select_parts.append(f"{expr} AS `{time_alias}`")
            cols.append(expr)
            meta["columns"][time_alias] = {"kind": "time", "label": "时间"}
        return cols

    def _build_where(self, plan: QueryPlan, table: TableDef) -> list[str]:
        clauses: list[str] = []
        # filters
        for f in plan.filters:
            d = self.semantic.dimension(f.dimension)
            if not d:
                continue
            col = d.column_in(table.name)
            if not col:
                continue
            if not f.values:
                continue
            if f.op == "in" or len(f.values) > 1:
                values = ", ".join(_qstring(v) for v in f.values)
                clauses.append(f"{_qident(col)} IN ({values})")
            elif f.op == "like":
                clauses.append(f"{_qident(col)} LIKE {_qstring('%' + f.values[0] + '%')}")
            else:
                clauses.append(f"{_qident(col)} = {_qstring(f.values[0])}")
        # time
        time_clauses = self._build_time_clauses(plan.time_range, table)
        clauses.extend(time_clauses)
        return clauses

    def _build_where_with_period(self, plan: QueryPlan, table: TableDef, year: str, months: list[str]) -> list[str]:
        # filters minus time, plus explicit period
        clauses: list[str] = []
        for f in plan.filters:
            d = self.semantic.dimension(f.dimension)
            if not d:
                continue
            col = d.column_in(table.name)
            if not col:
                continue
            if not f.values:
                continue
            if f.op == "in" or len(f.values) > 1:
                values = ", ".join(_qstring(v) for v in f.values)
                clauses.append(f"{_qident(col)} IN ({values})")
            elif f.op == "like":
                clauses.append(f"{_qident(col)} LIKE {_qstring('%' + f.values[0] + '%')}")
            else:
                clauses.append(f"{_qident(col)} = {_qstring(f.values[0])}")
        clauses.extend(self._period_clauses(table, year, months))
        return clauses

    def _build_time_clauses(self, tr: TimeRange, table: TableDef) -> list[str]:
        if tr.kind == TimeKind.NONE:
            return []
        year, months = self._resolve_period_year_months(tr, table)
        if not year and not months:
            return []
        return self._period_clauses(table, year, months)

    def _period_clauses(self, table: TableDef, year: str, months: list[str]) -> list[str]:
        # Support cross-year months by accepting either:
        # - year + months (single year window)
        # - months prefixed with year, like ["2025-11", "2025-12", "2026-01"...]
        cross_year_pairs: list[tuple[str, str]] = []
        plain_months: list[str] = []
        for m in months:
            if isinstance(m, str) and "-" in m and len(m) >= 7:
                y, mm = m.split("-", 1)
                cross_year_pairs.append((y, mm.zfill(2)))
            else:
                plain_months.append(str(m).zfill(2))

        if table.is_year_month_split():
            year_col = _qident(table.time_field_year or "year")
            month_col = _qident(table.time_field_month or "month")
            if cross_year_pairs:
                tuples = ", ".join(
                    f"({_qstring(y)}, {_qstring(m)})" for y, m in cross_year_pairs
                )
                return [f"({year_col}, {month_col}) IN ({tuples})"]
            clauses = []
            if year:
                clauses.append(f"{year_col} = {_qstring(year)}")
            if plain_months:
                if len(plain_months) == 1:
                    clauses.append(f"{month_col} = {_qstring(plain_months[0])}")
                else:
                    clauses.append(f"{month_col} IN ({', '.join(_qstring(m) for m in plain_months)})")
            return clauses

        # acc_month string column "YYYY-MM"
        col = _qident(table.time_field or "acc_month")
        if cross_year_pairs:
            values = [f"{y}-{m}" for y, m in cross_year_pairs]
            return [f"{col} IN ({', '.join(_qstring(v) for v in values)})"]
        if year and plain_months:
            values = [f"{year}-{m}" for m in plain_months]
            return [f"{col} IN ({', '.join(_qstring(v) for v in values)})"]
        if year:
            return [f"{col} LIKE {_qstring(year + '-%')}"]
        return []

    def _resolve_period_year_months(self, tr: TimeRange, table: TableDef) -> tuple[str, list[str]]:
        latest = self.semantic.data_range_latest or f"{self.today.year}-{self.today.month:02d}"
        latest_year, latest_month = (latest.split("-") + ["12"])[:2]

        if tr.kind == TimeKind.ABSOLUTE:
            year = tr.year or latest_year
            months = list(tr.months) if tr.months else []
            return year, months

        if tr.kind == TimeKind.RANGE:
            sy, sm = (tr.start_ym.split("-") + ["01"])[:2] if tr.start_ym else (latest_year, "01")
            ey, em = (tr.end_ym.split("-") + ["12"])[:2] if tr.end_ym else (latest_year, latest_month)
            if sy == ey:
                months = [f"{i:02d}" for i in range(int(sm), int(em) + 1)]
                return sy, months
            # cross-year not common; collapse to ey for our small-data scenario
            months = [f"{i:02d}" for i in range(1, int(em) + 1)]
            return ey, months

        # RELATIVE
        period = (tr.period or "this_month").lower()
        if period in ("this_month", "current_month"):
            return latest_year, [latest_month]
        if period == "last_month":
            y, ms = _shift_months(latest_year, [latest_month], -1)
            return y, ms
        if period == "this_year":
            return latest_year, [f"{i:02d}" for i in range(1, int(latest_month) + 1)]
        if period == "last_year":
            return str(int(latest_year) - 1), []
        if period == "ytd":
            return latest_year, [f"{i:02d}" for i in range(1, int(latest_month) + 1)]
        if period == "last_n_months":
            n = max(1, tr.n or 3)
            # Build a contiguous list of n YYYY-MM strings ending at latest.
            ly = int(latest_year); lm = int(latest_month)
            ym_list: list[str] = []
            for i in range(n - 1, -1, -1):
                total = ly * 12 + (lm - 1) - i
                yy = total // 12
                mm = total % 12 + 1
                ym_list.append(f"{yy:04d}-{mm:02d}")
            # When all in same year we keep year + months; else use cross-year
            years_in_set = {x.split("-")[0] for x in ym_list}
            if len(years_in_set) == 1:
                return ym_list[0].split("-")[0], [x.split("-")[1] for x in ym_list]
            return "", ym_list  # cross-year — months carry their own year prefix
        return latest_year, [latest_month]

    def _build_order(self, plan: QueryPlan, metric: MetricDef) -> str:
        if plan.order_by:
            parts = []
            for o in plan.order_by:
                direction = "DESC" if (o.dir or "desc").lower() == "desc" else "ASC"
                parts.append(f"`{o.field}` {direction}")
            return ", ".join(parts)
        if plan.calculation == "rank" or plan.group_by:
            return f"`{metric.name}` " + ("ASC" if not metric.higher_is_better else "DESC")
        return ""

    def _assemble_sql(
        self,
        select_parts: list[str],
        table: TableDef,
        where: list[str],
        group_cols: list[str],
        order_clause: str,
        limit: int,
        *,
        no_limit: bool = False,
    ) -> str:
        select_clause = ",\n       ".join(select_parts)
        sql = f"SELECT {select_clause}\nFROM {_qident(table.schema)}.{_qident(table.name)}"
        if where:
            sql += "\nWHERE " + "\n  AND ".join(where)
        if group_cols:
            sql += "\nGROUP BY " + ", ".join(group_cols)
        if order_clause:
            sql += "\nORDER BY " + order_clause
        if not no_limit and limit > 0:
            sql += f"\nLIMIT {int(limit)}"
        return sql


def _shift_months(year: str, months: list[str], delta: int) -> tuple[str, list[str]]:
    """Shift a month list backward/forward by delta months. Single-month focus only."""
    if not months:
        return year, months
    base_year = int(year)
    base_month = int(months[0])
    total = base_year * 12 + (base_month - 1) + delta
    new_year = total // 12
    new_month = total % 12 + 1
    return str(new_year), [f"{new_month:02d}"]
