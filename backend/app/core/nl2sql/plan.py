"""QueryPlan IR — the normalized representation produced by the planner.

The compiler turns this into MySQL SQL deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TimeKind(str, Enum):
    NONE = "none"
    RELATIVE = "relative"      # period: this_month / last_month / this_year / last_year / ytd / last_n_months
    ABSOLUTE = "absolute"      # year + months
    RANGE = "range"            # explicit start/end (YYYY-MM)


@dataclass
class TimeRange:
    kind: TimeKind = TimeKind.NONE
    period: str = ""               # this_month, last_month, last_n_months
    n: int = 0                     # for last_n_months / yoy lookback
    year: str = ""                 # for ABSOLUTE
    months: list[str] = field(default_factory=list)  # for ABSOLUTE
    start_ym: str = ""             # for RANGE
    end_ym: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value if isinstance(self.kind, TimeKind) else str(self.kind),
            "period": self.period,
            "n": self.n,
            "year": self.year,
            "months": list(self.months),
            "start_ym": self.start_ym,
            "end_ym": self.end_ym,
        }


@dataclass
class PlanFilter:
    dimension: str           # logical name in the semantic layer (e.g. "region")
    op: str = "eq"           # eq | in | like
    values: list[str] = field(default_factory=list)
    raw: str = ""            # original tokens

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "op": self.op, "values": list(self.values), "raw": self.raw}


@dataclass
class OrderBy:
    field: str
    dir: str = "desc"

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "dir": self.dir}


@dataclass
class QueryPlan:
    metric: str = ""                                   # logical metric name
    extra_metrics: list[str] = field(default_factory=list)  # additional metrics on same table
    table: str = ""                                    # resolved physical table
    group_by: list[str] = field(default_factory=list)  # logical dimension names
    filters: list[PlanFilter] = field(default_factory=list)
    time_range: TimeRange = field(default_factory=TimeRange)
    calculation: str = ""           # yoy_growth | mom_growth | ratio | rank | trend | delta | cumulative | ""
    order_by: list[OrderBy] = field(default_factory=list)
    limit: int = 0
    needs_clarify: bool = False
    clarify_reason: str = ""
    clarify_options: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""             # human-readable explanation

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "extra_metrics": list(self.extra_metrics),
            "table": self.table,
            "group_by": list(self.group_by),
            "filters": [f.to_dict() for f in self.filters],
            "time_range": self.time_range.to_dict(),
            "calculation": self.calculation,
            "order_by": [o.to_dict() for o in self.order_by],
            "limit": self.limit,
            "needs_clarify": self.needs_clarify,
            "clarify_reason": self.clarify_reason,
            "clarify_options": list(self.clarify_options),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }

    @staticmethod
    def from_dict(data: Any) -> "QueryPlan":
        """Defensive parser. LLM sometimes returns strings/lists in places we
        expect dicts. We coerce silently and never raise — invalid sub-trees
        just become defaults, so the outer pipeline can still respond."""
        if not isinstance(data, dict) or not data:
            return QueryPlan()

        # ---- time_range (must be dict; LLM sometimes returns "2025-01") ----
        tr_raw = data.get("time_range")
        if not isinstance(tr_raw, dict):
            tr_raw = {}
        try:
            kind = TimeKind(str(tr_raw.get("kind") or "none"))
        except ValueError:
            kind = TimeKind.NONE
        months_raw = tr_raw.get("months")
        months = [str(m) for m in months_raw] if isinstance(months_raw, list) else []
        tr = TimeRange(
            kind=kind,
            period=str(tr_raw.get("period") or ""),
            n=_as_int(tr_raw.get("n"), 0),
            year=str(tr_raw.get("year") or ""),
            months=months,
            start_ym=str(tr_raw.get("start_ym") or ""),
            end_ym=str(tr_raw.get("end_ym") or ""),
        )

        # ---- filters (must be list of dict) ----
        filters: list[PlanFilter] = []
        raw_filters = data.get("filters")
        if isinstance(raw_filters, list):
            for f in raw_filters:
                if not isinstance(f, dict):
                    continue
                vals = f.get("values")
                if isinstance(vals, str):
                    vals = [vals]
                elif not isinstance(vals, list):
                    vals = []
                filters.append(PlanFilter(
                    dimension=str(f.get("dimension") or ""),
                    op=str(f.get("op") or "eq"),
                    values=[str(v) for v in vals],
                    raw=str(f.get("raw") or ""),
                ))

        # ---- order_by (must be list of dict) ----
        orders: list[OrderBy] = []
        raw_orders = data.get("order_by")
        if isinstance(raw_orders, list):
            for o in raw_orders:
                if not isinstance(o, dict):
                    continue
                orders.append(OrderBy(field=str(o.get("field") or ""), dir=str(o.get("dir") or "desc")))

        # ---- group_by / extra_metrics / clarify_options (must be lists) ----
        gb = data.get("group_by")
        group_by = [str(g) for g in gb] if isinstance(gb, list) else []
        em = data.get("extra_metrics")
        extra_metrics = [str(m) for m in em] if isinstance(em, list) else []
        # clarify_options 规范化为 [{label,key,hint}]：LLM 常返回 ["xxx"] 这种
        # 纯字符串列表，下游 Answerer 会 opt.get(...) → 'str' object has no attribute 'get'。
        co = data.get("clarify_options")
        clarify_options: list[dict[str, Any]] = []
        if isinstance(co, list):
            for item in co:
                if isinstance(item, dict):
                    label = str(item.get("label") or item.get("key") or item.get("name") or "").strip()
                    if not label:
                        continue
                    clarify_options.append({
                        "label": label,
                        "key": str(item.get("key") or label),
                        "hint": str(item.get("hint") or ""),
                        "type": str(item.get("type") or ""),
                    })
                elif isinstance(item, (str, int, float)):
                    s = str(item).strip()
                    if s:
                        clarify_options.append({"label": s, "key": s, "hint": "", "type": ""})

        return QueryPlan(
            metric=str(data.get("metric") or ""),
            extra_metrics=extra_metrics,
            table=str(data.get("table") or ""),
            group_by=group_by,
            filters=filters,
            time_range=tr,
            calculation=str(data.get("calculation") or ""),
            order_by=orders,
            limit=_as_int(data.get("limit"), 0),
            needs_clarify=bool(data.get("needs_clarify")),
            clarify_reason=str(data.get("clarify_reason") or ""),
            clarify_options=clarify_options,
            confidence=_as_float(data.get("confidence"), 0.0),
            reasoning=str(data.get("reasoning") or ""),
        )

    def signature(self) -> str:
        """SQL-shape stable hash. 只把"决定 SQL 形状 + 决定结果集"的字段纳入：
        metric / extra_metrics / table / group_by / filters / time_range /
        calculation / order_by / limit。

        故意剔除 LLM 每次都会抖动的字段：
          - confidence  ← LLM 给的置信度（每次不同）
          - reasoning   ← LLM 写的解释（每次不同）
          - needs_clarify / clarify_reason / clarify_options ← 澄清相关，不影响 SQL

        这样"同一道题在不同会话里第二次问"能命中 L2 plan-keyed cache。
        """
        import hashlib
        import json
        canonical = {
            "metric": self.metric,
            "extra_metrics": sorted(self.extra_metrics or []),
            "table": self.table,
            "group_by": list(self.group_by or []),
            "filters": sorted(
                ((f.dimension, sorted([str(v) for v in (f.values or [])]), f.op or "in")
                 for f in (self.filters or [])),
                key=lambda x: x[0],
            ),
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "calculation": self.calculation or "",
            "order_by": [(o.field, o.dir or "desc") for o in (self.order_by or [])],
            "limit": int(self.limit or 0),
        }
        raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
