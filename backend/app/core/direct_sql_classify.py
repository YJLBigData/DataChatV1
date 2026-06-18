"""direct-SQL 结果列分类 + DB 错误摘要 —— 从 orchestrator.py 拆出（零行为变化）。

「走模型直出 SQL」的结果列名不可控（LLM 起的别名对不回语义层），这里**纯数据驱动**
地为每列推断 kind（dimension/metric/time）与展示格式，让直出结果也能切柱/线/饼。
"""
from __future__ import annotations

import re
from typing import Any

_TIME_COL_NAMES = {"month", "year", "acc_month", "period", "__period", "ym", "时间", "月份", "月", "年月"}
_YM_RE = re.compile(r"^\d{4}[-/]\d{1,2}")


def _classify_direct_columns(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    """给 direct-SQL 的结果列推断 kind（dimension/metric/time）+ 展示格式。

    纯数据驱动，不依赖列名能否对回语义层（LLM 起的别名不可控）：
      · 整列可解析为数字 → metric；列名形似时间或首值形如 YYYY-MM → time；否则 dimension。
      · format 再按列名里的"率/占比/金额/人数/件"等关键词猜，纯粹影响坐标轴/千分位显示。
    目的：让"走模型"的结果也能切柱/线/饼，而不是永远只能看列表。
    """
    meta: list[dict[str, Any]] = []
    for i, c in enumerate(columns):
        name = str(c)
        lname = name.lower()
        vals = [row[i] for row in rows if i < len(row)]
        non_null = [v for v in vals if v is not None]

        def _is_num(v: Any) -> bool:
            if isinstance(v, bool):
                return False
            if isinstance(v, (int, float)):
                return True
            try:
                float(str(v).replace(",", "").replace("%", "").strip())
                return True
            except (TypeError, ValueError):
                return False

        numeric = bool(non_null) and all(_is_num(v) for v in non_null)
        first = str(non_null[0]) if non_null else ""
        is_time = (
            lname in _TIME_COL_NAMES
            or any(tok in name for tok in ("月份", "年月"))
            or bool(_YM_RE.match(first))
        )
        if is_time:
            kind = "time"
        elif numeric:
            kind = "metric"
        else:
            kind = "dimension"

        fmt, unit, decimals = "", "", 2
        if kind == "metric":
            if any(k in name for k in ("率", "占比", "rate", "ratio", "achievement", "percent")):
                fmt, unit = "percent", "%"
            elif any(k in name for k in ("金额", "额", "amount", "sales", "target", "营收", "业绩")):
                fmt, unit = "currency_cn", "元"
            elif any(k in name for k in ("人数", "数量", "件", "count", "num", "qty", "_cnt", "人")):
                fmt, unit, decimals = "integer_cn", "", 0
        meta.append({"key": name, "label": name, "kind": kind, "unit": unit, "format": fmt, "decimals": decimals})
    return meta


def _summary_db_error(err: str) -> str:
    """把底层 SQL 异常提炼成用户可懂的简短描述。"""
    e = (err or "").lower()
    if "unknown column" in e:
        return "字段名不存在或表关联错误"
    if "table" in e and "doesn't exist" in e:
        return "表不存在"
    if "syntax" in e:
        return "SQL 语法错误"
    if "timeout" in e or "max_execution_time" in e:
        return "查询超时，请缩小数据范围"
    return "数据库执行出错"
