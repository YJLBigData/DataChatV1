"""Result Validator — 执行结果与计划意图的一致性校验（在生成最终答案之前）。

SKILL 要求："Add post-execution validation before final answer generation"。
本模块纯确定性、无 LLM，可离线用合成 ExecResult 测试。它**不改写数据、不伪造成功**：
只产出结构化 ValidationReport（落 explainability 审计）+ 面向用户的提示（进 risk_notes），
让"答非所问 / 排序错 / 空结果"无法被静默吞掉。

校验项（对齐 SKILL）：
  · 请求的列是否都在结果里（DB 真把我们 SELECT 的列返回了）；
  · TopN 行数是否符合预期（不超过 LIMIT；不足时如实说明）；
  · 排序方向是否与请求一致（按排序列校验单调性）；
  · 主指标是否整列为空（并解释）；
  · 0 行结果区分"无数据 / 时间范围不对 / 权限过滤 / 过滤过窄"，不当成真答案。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.exec import ExecResult

from .plan import QueryPlan, TimeKind

logger = logging.getLogger("datachat.validator")


@dataclass
class ValidationIssue:
    code: str
    severity: str            # warn | fail
    message: str             # 面向开发/审计的描述
    user_note: str = ""      # 面向用户的提示（非空才会进 risk_notes）

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "message": self.message, "user_note": self.user_note}


@dataclass
class ValidationReport:
    ok: bool = True
    severity: str = "none"   # none | warn | fail
    issues: list[ValidationIssue] = field(default_factory=list)

    def user_notes(self) -> list[str]:
        out: list[str] = []
        for i in self.issues:
            if i.user_note and i.user_note not in out:
                out.append(i.user_note)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "severity": self.severity,
            "issues": [i.to_dict() for i in self.issues],
        }

    def _add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        # severity 取最高：fail > warn > none
        order = {"none": 0, "warn": 1, "fail": 2}
        if order[issue.severity] > order[self.severity]:
            self.severity = issue.severity
        if issue.severity == "fail":
            self.ok = False


class ResultValidator:
    def __init__(self, semantic: Any):
        self.semantic = semantic

    def validate(
        self,
        question: str,
        plan: QueryPlan,
        meta: dict[str, Any],
        exec_result: ExecResult | None,
    ) -> ValidationReport:
        report = ValidationReport()
        if exec_result is None:
            return report
        cols = list(exec_result.columns or [])
        col_idx = {c: i for i, c in enumerate(cols)}
        rows = list(exec_result.rows or [])

        # 1) 请求列齐全：编译期 meta["columns"] 即"我们打算输出的列"，应与 DB 返回列一致。
        expected_cols = list((meta or {}).get("columns") or {})
        missing = [c for c in expected_cols if c not in col_idx]
        if missing:
            report._add(ValidationIssue(
                code="missing_columns", severity="fail",
                message=f"结果缺少请求列：{missing}（请求 {expected_cols}，返回 {cols}）",
                user_note="部分请求的指标/维度未能返回，结果可能不完整，请重试或联系管理员。",
            ))

        # 2) 0 行：区分原因，绝不当成真答案。
        if exec_result.row_count == 0:
            tr_kind = plan.time_range.kind if plan.time_range else TimeKind.NONE
            if tr_kind == TimeKind.RELATIVE:
                report._add(ValidationIssue(
                    code="empty_relative_time", severity="warn",
                    message="相对时间范围返回 0 行，可能落到无数据月份。",
                    user_note="",  # 由 orchestrator 统一补"最新可用月份"提示，避免重复
                ))
            elif plan.filters:
                fdims = "、".join(f.dimension for f in plan.filters)
                report._add(ValidationIssue(
                    code="empty_filtered", severity="warn",
                    message=f"带过滤条件（{fdims}）返回 0 行，可能筛选过窄或值不存在。",
                    user_note="当前筛选条件下没有数据，请确认筛选值（如大区/渠道/段位名称）是否正确。",
                ))
            else:
                report._add(ValidationIssue(
                    code="empty_no_data", severity="warn",
                    message="该口径下返回 0 行。",
                    user_note="该口径下暂无数据。",
                ))
            return report  # 0 行无需再做排序/空值校验

        # 3) TopN 行数：不得超过 LIMIT；不足时如实说明（不是错误，只是信息）。
        if plan.limit and plan.limit > 0:
            if exec_result.row_count > plan.limit:
                report._add(ValidationIssue(
                    code="limit_overflow", severity="fail",
                    message=f"返回 {exec_result.row_count} 行超过 LIMIT {plan.limit}。",
                    user_note="",
                ))
            elif self._is_topn(plan) and exec_result.row_count < plan.limit:
                report._add(ValidationIssue(
                    code="topn_underfill", severity="warn",
                    message=f"请求前 {plan.limit}，实际仅 {exec_result.row_count} 行满足条件。",
                    user_note=f"满足条件的数据仅 {exec_result.row_count} 条（少于请求的 {plan.limit} 条）。",
                ))

        # 4) 排序方向：按主排序列校验单调性（派生口径的规范列如 diff_abs 也适用）。
        order_field, order_dir = self._primary_order(plan)
        if order_field and order_field in col_idx and len(rows) >= 2:
            if not self._is_monotonic(rows, col_idx[order_field], order_dir):
                report._add(ValidationIssue(
                    code="sort_mismatch", severity="warn",
                    message=f"结果未按 {order_field} {order_dir.upper()} 单调排序。",
                    user_note="",
                ))

        # 5) 主指标整列为空：结果在场但关键指标全 NULL，narrative 不应假装有结论。
        if plan.metric in col_idx:
            idx = col_idx[plan.metric]
            vals = [r[idx] for r in rows if isinstance(r, (list, tuple)) and idx < len(r)]
            if vals and all(v is None for v in vals):
                md = self.semantic.metric(plan.metric)
                label = md.label if md else plan.metric
                report._add(ValidationIssue(
                    code="metric_all_null", severity="warn",
                    message=f"主指标 {plan.metric} 整列为空。",
                    user_note=f"「{label}」在该范围内全部为空，请确认口径或时间范围。",
                ))

        return report

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _is_topn(plan: QueryPlan) -> bool:
        return bool(plan.limit and (plan.calculation == "rank" or plan.order_by))

    @staticmethod
    def _primary_order(plan: QueryPlan) -> tuple[str, str]:
        if plan.order_by:
            o = plan.order_by[0]
            return o.field, (o.dir or "desc").lower()
        return "", ""

    @staticmethod
    def _is_monotonic(rows: list[Any], idx: int, direction: str) -> bool:
        """结果按 idx 列是否单调（容忍相等与 NULL）。NULL 视为跳过比较，不算违反。"""
        prev = None
        asc = direction == "asc"
        for r in rows:
            if not isinstance(r, (list, tuple)) or idx >= len(r):
                continue
            v = r[idx]
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return True  # 非数值列不校验排序（如字符串维度），视为通过
            if prev is not None:
                if asc and fv < prev - 1e-9:
                    return False
                if (not asc) and fv > prev + 1e-9:
                    return False
            prev = fv
        return True
