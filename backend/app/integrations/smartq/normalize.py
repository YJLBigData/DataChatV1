"""把 SmartqQueryAbility 的返回归一化成 DataChat 现有的 answer/ChatResult 形态。

映射（按官方字段，尽量容错多种返回形态）：
  · narrative ← ConclusionText
  · table     ← Columns/Headers + Values/DataList（list[dict] 或 list[list] 都支持）
  · sql       ← LogicSql
  · chart     ← ChartType → DataChat 图表类型
不依赖真实 LLM/DB —— 纯字段搬运，便于单测覆盖。
"""
from __future__ import annotations

from typing import Any

_CHART_MAP = {
    "table": "table", "line": "line", "column": "bar", "bar": "bar_horizontal",
    "pie": "pie", "funnel": "funnel", "scatter": "scatter", "area": "area",
    "kpi": "kpi", "card": "kpi", "number": "kpi",
}


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
        # 容错大小写
        for dk in d:
            if dk.lower() == k.lower() and d[dk] not in (None, ""):
                return d[dk]
    return default


def _columns(payload: dict[str, Any]) -> list[dict[str, str]]:
    cols = _first(payload, "Columns", "Headers", "ColumnList", default=None)
    out: list[dict[str, str]] = []
    if isinstance(cols, list):
        for c in cols:
            if isinstance(c, dict):
                key = str(_first(c, "Name", "Column", "Field", "Key", default="") or "")
                label = str(_first(c, "Label", "Alias", "Caption", "Name", default=key) or key)
            else:
                key = label = str(c)
            out.append({"key": key or label, "label": label or key})
    return out


def _rows(payload: dict[str, Any], col_keys: list[str]) -> list[list[str]]:
    data = _first(payload, "Values", "DataList", "Data", "Rows", default=None)
    rows: list[list[str]] = []
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict):
                if col_keys:
                    rows.append([_cell(r.get(k)) for k in col_keys])
                else:
                    rows.append([_cell(v) for v in r.values()])
            elif isinstance(r, (list, tuple)):
                rows.append([_cell(v) for v in r])
    return rows


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def normalize_smartq_answer(payload: dict[str, Any], *, question: str = "") -> dict[str, Any]:
    """SmartQ Result 字段 → DataChat answer payload（AnswerPayload 形态）。"""
    payload = payload or {}
    columns = _columns(payload)
    col_keys = [c["key"] for c in columns]
    rows = _rows(payload, col_keys)
    if not columns and rows and isinstance(rows[0], list):
        columns = [{"key": f"c{i}", "label": f"列{i + 1}"} for i in range(len(rows[0]))]
        col_keys = [c["key"] for c in columns]

    narrative = str(_first(payload, "ConclusionText", "Conclusion", "Summary", "Answer", default="") or "")
    sql = str(_first(payload, "LogicSql", "Sql", default="") or "")
    chart_type_raw = str(_first(payload, "ChartType", "Chart", default="") or "").lower()
    chart_type = _CHART_MAP.get(chart_type_raw, "table" if rows else "none")

    display_columns = [
        {"key": c["key"], "label": c["label"], "kind": "value", "unit": "", "format": "", "decimals": 2}
        for c in columns
    ]
    table = {
        "columns": col_keys,
        "rows": rows,
        "display_columns": display_columns,
        "display_rows": rows,
        "row_count": len(rows),
        "elapsed_ms": 0,
    }
    return {
        "needs_clarify": False,
        "narrative": narrative or (f"已通过智能小Q查询：{question}" if question else "查询完成"),
        "highlights": [],
        "risk_notes": [],
        "table": table,
        "chart": {"type": chart_type},
        "suggestions": [],
        "clarify_options": [],
        "explainability": {"sql": sql, "row_count": len(rows), "reasoning": "SmartQ（Quick BI 智能小Q）"},
    }


def smartq_answer_is_substantive(answer: dict[str, Any]) -> bool:
    """SmartQ 归一化结果是否含实质内容（行 / 结论 / SQL）。

    用于判定一次 SmartQ 调用是否"真的回答了"——无行、无结论、无 SQL 的空壳响应
    （典型：越权 cube / 数据集未开启 SmartQ / 无数据）绝不能被当成成功结果展示。
    """
    if not isinstance(answer, dict):
        return False
    table = answer.get("table") or {}
    if int(table.get("row_count") or 0) > 0:
        return True
    if str((answer.get("explainability") or {}).get("sql") or "").strip():
        return True
    narrative = str(answer.get("narrative") or "").strip()
    # 兜底文案（"已通过智能小Q查询…" / "查询完成"）不算实质结论。
    return bool(narrative) and not (narrative.startswith("已通过智能小Q查询") or narrative == "查询完成")
