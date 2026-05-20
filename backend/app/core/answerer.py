"""Answerer — turn execution results + plan into a high-quality executive answer.

Outputs a structured payload:
{
  "narrative": "<中文经营结论 + 关键发现>",
  "highlights": [...],           # bullet points
  "risk_notes": [...],
  "table": {"columns": [...], "rows": [[...]], "display": [...]},
  "chart": {"type": "bar|line|pie|none", "x": "...", "series": [...]},
  "suggestions": ["..."],
  "explainability": {
    "used_tables": [...],
    "metric_definition": {...},
    "filters_applied": [...],
    "time_range": {...},
    "sql": "..."
  }
}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.exec import ExecResult
from app.core.llm.router import LLMRouter, get_llm_router
from app.core.nl2sql.plan import QueryPlan
from app.core.semantic import SemanticLayer

logger = logging.getLogger("datachat.answerer")


def _format_value(value: Any, fmt: str, decimals: int) -> str:
    if value is None:
        return "—"
    try:
        if fmt == "percent":
            return f"{float(value) * 100:.{decimals}f}%"
        if fmt == "currency_cn":
            v = float(value)
            if abs(v) >= 100000000:
                return f"{v / 100000000:.2f} 亿元"
            if abs(v) >= 10000:
                return f"{v / 10000:.2f} 万元"
            return f"{v:,.{decimals}f} 元"
        if fmt == "integer_cn":
            v = float(value)
            if abs(v) >= 10000:
                return f"{v / 10000:.2f} 万"
            return f"{int(round(v)):,}"
        if isinstance(value, (int, float)):
            return f"{float(value):,.{decimals}f}"
    except Exception:
        pass
    return str(value)


class Answerer:
    def __init__(self, semantic: SemanticLayer, llm: LLMRouter | None = None):
        self.semantic = semantic
        self.llm = llm or get_llm_router()

    def build(
        self,
        question: str,
        plan: QueryPlan,
        meta: dict[str, Any],
        exec_result: ExecResult | None,
        guard_sql: str,
        *,
        skip_llm: bool = False,
    ) -> dict[str, Any]:
        if plan.needs_clarify:
            return self._build_clarify(question, plan)
        if exec_result is None:
            return {
                "narrative": "查询未执行成功，请稍后重试。",
                "highlights": [],
                "risk_notes": [],
                "table": {"columns": [], "rows": [], "display": []},
                "chart": {"type": "none"},
                "suggestions": [],
                "explainability": {"sql": guard_sql, "metric_definition": self._metric_def_dict(plan.metric)},
            }

        table_payload = self._format_table(exec_result, meta)
        chart_payload = self._infer_chart(plan, meta, exec_result)
        explain = self._explainability(plan, meta, guard_sql, exec_result)

        if skip_llm or exec_result.row_count == 0:
            narrative = self._template_narrative(question, plan, exec_result, meta)
            highlights = self._template_highlights(plan, exec_result, meta)
        else:
            narrative, highlights, risks = self._llm_narrative(question, plan, exec_result, meta, table_payload)
            return {
                "narrative": narrative,
                "highlights": highlights,
                "risk_notes": risks,
                "table": table_payload,
                "chart": chart_payload,
                "suggestions": self._suggest_followups(plan),
                "explainability": explain,
            }

        return {
            "narrative": narrative,
            "highlights": highlights,
            "risk_notes": [],
            "table": table_payload,
            "chart": chart_payload,
            "suggestions": self._suggest_followups(plan),
            "explainability": explain,
        }

    # -------------------------------------------------------------- clarify

    def _build_clarify(self, question: str, plan: QueryPlan) -> dict[str, Any]:
        return {
            "needs_clarify": True,
            "narrative": plan.clarify_reason or "请补充更具体的查询条件，例如时间、维度或指标。",
            "highlights": [],
            "risk_notes": [],
            "table": {"columns": [], "rows": [], "display": []},
            "chart": {"type": "none"},
            "clarify_options": [o for o in (plan.clarify_options or []) if isinstance(o, dict)],
            "suggestions": [
                str(opt.get("label") or "")
                for opt in (plan.clarify_options or [])
                if isinstance(opt, dict) and opt.get("label")
            ],
            "explainability": {
                "metric_definition": self._metric_def_dict(plan.metric) if plan.metric else None,
                "rule": "信息不足时优先澄清，避免错答",
            },
        }

    # -------------------------------------------------------------- table

    def _format_table(self, exec_result: ExecResult, meta: dict[str, Any]) -> dict[str, Any]:
        cols_meta = meta.get("columns") or {}
        display_cols: list[dict[str, Any]] = []
        for raw_col in exec_result.columns:
            cm = cols_meta.get(raw_col) or {}
            display_cols.append({
                "key": raw_col,
                "label": cm.get("label") or raw_col,
                "kind": cm.get("kind") or "value",
                "unit": cm.get("unit") or "",
                "format": cm.get("format") or "",
                "decimals": cm.get("decimals", 2),
            })
        display_rows: list[list[str]] = []
        for row in exec_result.rows:
            new_row = []
            for value, dc in zip(row, display_cols):
                fmt = dc.get("format") or ("integer_cn" if dc.get("kind") == "metric" else "")
                new_row.append(_format_value(value, fmt, dc.get("decimals", 2)))
            display_rows.append(new_row)
        return {
            "columns": exec_result.columns,
            "rows": exec_result.rows,
            "display_columns": display_cols,
            "display_rows": display_rows,
            "row_count": exec_result.row_count,
            "elapsed_ms": exec_result.elapsed_ms,
        }

    # ---------------------------------------------------------------- chart

    def _infer_chart(self, plan: QueryPlan, meta: dict[str, Any], exec_result: ExecResult) -> dict[str, Any]:
        if exec_result.row_count == 0:
            return {"type": "none"}
        cols_meta = meta.get("columns") or {}
        dim_cols = [c for c in exec_result.columns if (cols_meta.get(c) or {}).get("kind") == "dimension"]
        time_cols = [c for c in exec_result.columns if (cols_meta.get(c) or {}).get("kind") == "time"]
        metric_cols = [c for c in exec_result.columns if (cols_meta.get(c) or {}).get("kind") == "metric"]
        if not metric_cols:
            return {"type": "none"}
        if exec_result.row_count == 1 and not dim_cols and not time_cols:
            return {"type": "single_value", "metric": metric_cols[0]}
        if plan.calculation == "ratio" and dim_cols:
            return {"type": "pie", "x": dim_cols[0], "series": [metric_cols[0]]}
        if plan.calculation == "trend" or time_cols:
            x = time_cols[0] if time_cols else dim_cols[0]
            return {"type": "line", "x": x, "series": metric_cols}
        if plan.calculation == "rank" and dim_cols:
            return {"type": "bar", "x": dim_cols[0], "series": [metric_cols[0]], "orientation": "horizontal" if exec_result.row_count > 8 else "vertical"}
        if dim_cols:
            return {"type": "bar", "x": dim_cols[0], "series": metric_cols}
        return {"type": "bar", "x": exec_result.columns[0], "series": metric_cols}

    # --------------------------------------------------------- explainability

    def _explainability(
        self,
        plan: QueryPlan,
        meta: dict[str, Any],
        guard_sql: str,
        exec_result: ExecResult,
    ) -> dict[str, Any]:
        return {
            "used_tables": [meta.get("table") or plan.table],
            "metric_definition": self._metric_def_dict(plan.metric),
            "filters_applied": [f.to_dict() for f in plan.filters],
            "group_by": plan.group_by,
            "time_range": plan.time_range.to_dict(),
            "calculation": plan.calculation,
            "sql": guard_sql,
            "row_count": exec_result.row_count,
            "elapsed_ms": exec_result.elapsed_ms,
            "reasoning": plan.reasoning,
            "confidence": plan.confidence,
        }

    def _metric_def_dict(self, metric_name: str) -> dict[str, Any]:
        m = self.semantic.metric(metric_name)
        if not m:
            return {}
        return {
            "name": m.name, "label": m.label, "expression": m.expression,
            "table": m.table, "unit": m.unit, "domain": m.domain,
            "description": m.description,
        }

    # --------------------------------------------------------- narratives

    def _template_narrative(self, question: str, plan: QueryPlan, exec_result: ExecResult, meta: dict[str, Any]) -> str:
        m = self.semantic.metric(plan.metric)
        if exec_result.row_count == 0:
            return f"未查询到符合条件的{m.label if m else '数据'}。请确认时间范围或筛选条件。"
        if exec_result.row_count == 1 and len(exec_result.columns) == 1:
            value = exec_result.rows[0][0]
            cm = (meta.get("columns") or {}).get(exec_result.columns[0]) or {}
            v_str = _format_value(value, cm.get("format") or "", cm.get("decimals", 2))
            return f"{m.label if m else '查询结果'}为 {v_str}。"
        return f"{m.label if m else '查询结果'} 已返回 {exec_result.row_count} 条记录。"

    def _template_highlights(self, plan: QueryPlan, exec_result: ExecResult, meta: dict[str, Any]) -> list[str]:
        if exec_result.row_count == 0 or not exec_result.columns:
            return []
        cols_meta = meta.get("columns") or {}
        m = self.semantic.metric(plan.metric)
        metric_col = plan.metric if plan.metric in exec_result.columns else (exec_result.columns[-1] if exec_result.columns else "")
        if not metric_col:
            return []
        idx = exec_result.columns.index(metric_col)
        cm = cols_meta.get(metric_col) or {}
        rows = sorted(exec_result.rows, key=lambda r: (-(float(r[idx]) if r[idx] is not None else 0)))
        highlights = []
        if rows:
            top = rows[0]
            top_label = " / ".join(str(top[i]) for i in range(len(top)) if i != idx)
            highlights.append(f"最高：{top_label or '汇总'} = {_format_value(top[idx], cm.get('format') or '', cm.get('decimals',2))}")
        if len(rows) > 1:
            bot = rows[-1]
            bot_label = " / ".join(str(bot[i]) for i in range(len(bot)) if i != idx)
            highlights.append(f"最低：{bot_label or '汇总'} = {_format_value(bot[idx], cm.get('format') or '', cm.get('decimals',2))}")
        if len(rows) >= 3:
            total = sum(float(r[idx] or 0) for r in rows)
            avg = total / len(rows)
            highlights.append(f"平均：{_format_value(avg, cm.get('format') or '', cm.get('decimals',2))}（共 {len(rows)} 项）")
        return highlights

    def _llm_narrative(self, question: str, plan: QueryPlan, exec_result: ExecResult, meta: dict[str, Any], table_payload: dict[str, Any] | None = None) -> tuple[str, list[str], list[str]]:
        """Ask LLM to summarise the result for executives. JSON-mode for stability."""
        # truncate rows for prompt — prefer the *formatted* display rows so the
        # LLM uses Chinese display ("254.50 万元", "93.02%") in the narrative
        # and never invents numbers.
        max_rows = 30
        cols_meta = meta.get("columns") or {}
        if table_payload:
            display_rows = table_payload.get("display_rows") or []
            display_cols = [c.get("label") or c.get("key") for c in (table_payload.get("display_columns") or [])]
            sample = display_rows[:max_rows]
            head = "|".join(display_cols)
        else:
            sample = exec_result.rows[:max_rows]
            head = "|".join(exec_result.columns)
        body = "\n".join("|".join(str(_to_jsonable(c)) for c in row) for row in sample)
        m = self.semantic.metric(plan.metric)
        # resolve time_range to a human-readable label so narrative isn't vague
        time_label = self._human_time_label(plan)
        plan_brief = {
            "metric": m.label if m else plan.metric,
            "table": plan.table,
            "filters": [f"{f.dimension}={f.values}" for f in plan.filters],
            "group_by": plan.group_by,
            "calculation": plan.calculation,
            "time_label": time_label,
        }
        sys = (
            "你是飞鹤公司的高管经营分析师。请基于查询结果给出准确、克制、面向高管的分析。"
            "硬性要求："
            "1) 用中文；"
            "2) 数字必须从『数据』中读取，不得编造；"
            "3) 时间表述必须使用 plan.time_label，不要再说\"本年度/今年\"等模糊词；"
            "4) 不要使用 emoji；"
            "5) 只输出 JSON：{\"narrative\":\"...\",\"highlights\":[\"...\"],\"risk_notes\":[\"...\"]}；"
            "6) narrative 不超过 90 字；highlights 与 risk_notes 各不超过 4 条、每条不超过 30 字。"
            "7) 如果只有 1 行 1 列，narrative 就直接说出该数值，不要做趋势/同比推断；"
            "8) highlights 必须包含 Top1 / 末位 / 总计 等可验证事实。"
        )
        user = (
            f"问题：{question}\n"
            f"plan：{json.dumps(plan_brief, ensure_ascii=False)}\n"
            f"时间口径：{time_label}\n"
            f"返回 {exec_result.row_count} 行（截取 {len(sample)} 行）：\n"
            f"列：{head}\n"
            f"数据：\n{body}\n"
        )
        try:
            payload, _ = self.llm.chat_json(
                [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                schema_hint='{"narrative":"...","highlights":["..."],"risk_notes":["..."]}',
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning("LLM narrative failed: %s — fallback to template", exc)
            return (
                self._template_narrative(question, plan, exec_result, meta),
                self._template_highlights(plan, exec_result, meta),
                [],
            )
        if not isinstance(payload, dict):
            return (
                self._template_narrative(question, plan, exec_result, meta),
                self._template_highlights(plan, exec_result, meta),
                [],
            )
        return (
            str(payload.get("narrative") or self._template_narrative(question, plan, exec_result, meta)),
            list(payload.get("highlights") or []),
            list(payload.get("risk_notes") or []),
        )

    # ---------------------------------------------------------- followups

    def _human_time_label(self, plan: QueryPlan) -> str:
        tr = plan.time_range
        latest = self.semantic.data_range_latest or ""
        ly, lm = (latest.split("-") + ["01"])[:2] if latest else ("", "")
        from app.core.nl2sql.plan import TimeKind as _TK
        if tr.kind == _TK.NONE:
            return f"{ly}-{lm}（默认当前月）"
        if tr.kind == _TK.RELATIVE:
            mapping = {
                "this_month": f"{ly}-{lm}",
                "last_month": "上月",
                "this_year": f"{ly} 年",
                "last_year": f"{int(ly) - 1} 年" if ly else "去年",
                "ytd": f"{ly} 年累计至 {lm} 月",
                "last_n_months": f"近 {tr.n or 3} 个月",
            }
            return mapping.get(tr.period, tr.period or "近期")
        if tr.kind == _TK.ABSOLUTE:
            if tr.year and tr.months:
                return f"{tr.year} 年 " + "/".join(tr.months) + " 月"
            return tr.year or "指定时段"
        if tr.kind == _TK.RANGE:
            return f"{tr.start_ym} ~ {tr.end_ym}"
        return "近期"

    def _suggest_followups(self, plan: QueryPlan) -> list[str]:
        m = self.semantic.metric(plan.metric)
        if not m:
            return []
        suggestions: list[str] = []
        used_dims = set(plan.group_by) | {f.dimension for f in plan.filters}
        for dim in m.typical_dimensions:
            if dim in used_dims:
                continue
            d = self.semantic.dimension(dim)
            if not d:
                continue
            suggestions.append(f"按{d.label}下钻")
            if len(suggestions) >= 3:
                break
        if "trend" not in (plan.calculation or "") and m.typical_dimensions:
            suggestions.append(f"看{m.label}近 6 个月趋势")
        if not plan.calculation and m.domain == "sales":
            suggestions.append(f"对比{m.label}同比变化")
        return suggestions[:5]


def _to_jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)
