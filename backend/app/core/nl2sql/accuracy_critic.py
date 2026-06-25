"""Accuracy Critic Agent — structured, auditable review of a QueryPlan.

它在 **确定性 plan 修复之后、编译之前**运行，对计划做一次独立的"业务意图复核"，
并产出结构化修复提示（绝不自己写 SQL）。设计原则严格遵循 SKILL：

  · Critic 只做"审稿人"，不做"作者"：检查问题/候选/计划/编译预览，返回结构化提示；
    真正的 SQL 仍由 compiler 从 QueryPlan 确定性生成。
  · 确定性检查优先；LLM 仅在"语义层面真歧义"时可选介入（默认关闭、取不到 LLM 静默跳过）。
  · 至多一次自动修复；修复后仍 fail → 交回澄清/安全报错，绝不瞎猜。

它为什么有价值（而不只是重复 planner 的规则）：
  planner 的多指标/列举维度/TopN 等确定性兜底，有些只在"非多轮 / rule-only"分支触发；
  当**真 LLM 在位**直接给出 plan 时，个别规则会被 LLM 的结果"绕过"。Critic 用与 planner
  同源的 `rule_seed`（纯从问句提取，与 LLM 无关）对**最终 plan**再独立比对一次：
  缺指标就补、缺维度就补、TopN 漏截就截、方向反了就纠、结构完整却误澄清就放行。
  在 rule-only 路径上 planner 已修好 → Critic 复核为 ok，零行为变化（不影响既有回归）；
  价值全部体现在生产的 LLM 路径，并为每个高风险问题留一条可审计记录。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .plan import OrderBy, PlanFilter, QueryPlan
# 与 planner 同源的口径常量：鹤礼3.0 专属重映射 + 列举型维度动词，保证 Critic 的
# "期望"与 planner 的修复完全一致，不会把 planner 正确改写掉的口径又"纠"回去。
from .planner import HELI30_METRIC_REMAP

logger = logging.getLogger("datachat.critic")

# 列举型输出维度动词（与 planner._extract_rule_seed 中保持一致）。
_FIELD_LIST_VERBS = ("列出", "列举", "给出", "显示", "展示", "罗列", "列示", "呈现", "输出", "包含", "包括")


@dataclass
class CriticReport:
    """结构化复核结论（与 SKILL 约定的 JSON 字段一一对应，可直接落审计日志）。

    `repair_hints` 是机器可执行的修复指令（供 deterministic repair 消费），
    其余字段是"哪里不对"的人读/审计描述。两者分离：诊断可读、修复可控。
    """
    ok: bool = True
    severity: str = "none"              # none | warn | fail
    reason: str = ""
    missing_metrics: list[str] = field(default_factory=list)
    missing_dimensions: list[str] = field(default_factory=list)
    wrong_table: str = ""
    wrong_filters: list[str] = field(default_factory=list)
    wrong_order_by: str = ""
    wrong_limit: str = ""
    clarify_should_be_suppressed: bool = False
    repair_hints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "severity": self.severity,
            "reason": self.reason,
            "missing_metrics": list(self.missing_metrics),
            "missing_dimensions": list(self.missing_dimensions),
            "wrong_table": self.wrong_table,
            "wrong_filters": list(self.wrong_filters),
            "wrong_order_by": self.wrong_order_by,
            "wrong_limit": self.wrong_limit,
            "clarify_should_be_suppressed": self.clarify_should_be_suppressed,
            "repair_hints": list(self.repair_hints),
        }


class AccuracyCritic:
    """确定性优先的计划复核器。复用 planner 的语义层与等价指标解析，避免口径漂移。"""

    def __init__(self, planner: Any):
        self.planner = planner
        self.semantic = planner.semantic

    # ------------------------------------------------------------ trigger

    @staticmethod
    def should_review(plan: QueryPlan, rule_seed: dict[str, Any], inherit: bool) -> bool:
        """只对高风险问题触发复核（SKILL：multi-metric / field-list / TopN /
        派生指标 / 追问 / 低置信 / 已计划澄清）。其余问题信任 planner，零额外开销。"""
        if plan.needs_clarify:
            return True
        if inherit:
            return True
        if plan.calculation in ("ratio", "delta", "yoy_growth", "mom_growth", "rank"):
            return True
        if len(rule_seed.get("metric_hits") or []) >= 2:
            return True
        if rule_seed.get("rank_n"):
            return True
        if rule_seed.get("order_hint"):
            return True
        if (plan.confidence or 0) and plan.confidence < 0.5:
            return True
        return False

    # ------------------------------------------------------------ review

    def review(
        self,
        question: str,
        plan: QueryPlan,
        *,
        rule_seed: dict[str, Any],
        bundle: Any | None = None,
        previous_plan: QueryPlan | None = None,
        inherit: bool = False,
        sql_preview: str = "",
    ) -> CriticReport:
        """对最终 plan 做一次确定性业务复核，返回结构化结论 + 修复指令。"""
        report = CriticReport()
        if not plan.metric or not self.semantic.metric(plan.metric):
            # 无主指标本就由 planner 澄清；Critic 不重复判定。
            return report
        table = plan.table
        q = question or ""
        heli_ctx = ("鹤礼" in q) or ("heli" in q.lower())

        selected = {plan.metric, *plan.extra_metrics}
        reasons: list[str] = []

        # 1) 显式点名的指标必须都被 SELECT（缺则补为附带列）。鹤礼上下文先做与 planner 同源的
        #    专属重映射，避免把已正确改写的鹤礼口径又"补"回通用首购/复购。
        for hit in (rule_seed.get("metric_hits") or []):
            want = HELI30_METRIC_REMAP.get(hit, hit) if heli_ctx else hit
            mapped = self._equiv_on_table(want, table)
            if not mapped or mapped in selected:
                continue
            report.missing_metrics.append(mapped)
            report.repair_hints.append({"action": "add_extra_metric", "metric": mapped})
            selected.add(mapped)
        if report.missing_metrics:
            reasons.append(f"问句点名但未输出的指标：{report.missing_metrics}")

        # 2) 规则命中的分组维度必须都在 group_by（仅当该维度在当前表可用）。
        for dim in (rule_seed.get("group_by_hint") or []):
            if dim in plan.group_by:
                continue
            if self._dim_on_table(dim, table):
                report.missing_dimensions.append(dim)
                report.repair_hints.append({"action": "add_group_by", "dimension": dim})
        if report.missing_dimensions:
            reasons.append(f"问句要求但缺失的输出维度：{report.missing_dimensions}")

        # 3) TopN 漏截：问句给了"最高/最低的 N 个"(rank_n) 但 plan 没有 limit → 补 LIMIT N。
        rank_n = int(rule_seed.get("rank_n") or 0)
        if rank_n > 0 and not plan.limit:
            report.wrong_limit = f"expected LIMIT {rank_n}, got none"
            report.repair_hints.append({"action": "set_limit", "limit": rank_n})
            reasons.append(f"TopN 未截断：应为 LIMIT {rank_n}")

        # 4) 排序方向：仅对普通聚合 / rank 复核（派生口径 delta/ratio/yoy/mom 由 compiler
        #    钉死规范排序，不在此干预）。最值/显式方向词就近绑定的指标 = 期望排序列。
        if plan.calculation in ("", "rank"):
            oh = rule_seed.get("order_hint") or {}
            want_field = self._equiv_on_table(oh.get("field") or "", table) or (oh.get("field") or "")
            want_dir = (oh.get("dir") or "").lower()
            if want_field and want_dir and want_field in {plan.metric, *plan.extra_metrics, *plan.group_by}:
                cur = plan.order_by[0] if plan.order_by else None
                if (cur is None) or (cur.field != want_field) or ((cur.dir or "").lower() != want_dir):
                    report.wrong_order_by = f"expected ORDER BY {want_field} {want_dir}"
                    report.repair_hints.append({"action": "set_order_by", "field": want_field, "dir": want_dir})
                    reasons.append(f"排序应为 {want_field} {want_dir.upper()}")

        # 5) 维度不在当前表的过滤 → 丢弃（LLM 偶发注入"另一张表的维度"形成无效/错误过滤）。
        #    与 compiler._build_where 行为一致（它对取不到列的过滤静默跳过），这里把它显式化、
        #    可审计。单字符值不在此判定：is_guide_shop 的 是/否、value_dict 编码值都是合法单字。
        for f in plan.filters:
            if not self._dim_on_table(f.dimension, table):
                report.wrong_filters.append(f"{f.dimension}（维度不在当前表 {table}）")
                report.repair_hints.append({"action": "drop_filter", "dimension": f.dimension})
        if report.wrong_filters:
            reasons.append(f"可疑过滤条件：{report.wrong_filters}")

        # 6) 误澄清放行（P0 反模式）：plan 结构完整 + 用户已显式点名指标/列举多指标，
        #    却仍 needs_clarify=true → 抑制澄清，直接执行。条件互斥于 planner 的
        #    `_maybe_ambiguity_clarify`（后者仅在"用户**未**点名"时才澄清），不会打架。
        if plan.needs_clarify and self._structurally_complete(plan):
            chosen_named = self._metric_explicitly_named(plan.metric, rule_seed, heli_ctx)
            field_list_multi = bool(
                any(v in q for v in _FIELD_LIST_VERBS)
                and len(rule_seed.get("metric_hits") or []) >= 2
            )
            if chosen_named or field_list_multi:
                report.clarify_should_be_suppressed = True
                report.repair_hints.append({"action": "suppress_clarify"})
                reasons.append("结构完整且用户已显式点名指标，澄清应被抑制")

        if report.repair_hints:
            # 误澄清放行属于"纠正"，归 warn；其余补漏也按 warn（可一次确定性修复）。
            report.ok = False
            report.severity = "warn"
            report.reason = "；".join(reasons)
        return report

    # ------------------------------------------------------------ repair

    def repair(self, plan: QueryPlan, report: CriticReport) -> QueryPlan:
        """按 repair_hints 做**一次**确定性修复。全部为"补齐/纠正/放行"，绝不删指标列。"""
        for hint in report.repair_hints:
            action = hint.get("action")
            if action == "add_extra_metric":
                m = hint.get("metric")
                md = self.semantic.metric(m) if m else None
                if md and md.table == plan.table and m != plan.metric and m not in plan.extra_metrics:
                    plan.extra_metrics.append(m)
            elif action == "add_group_by":
                d = hint.get("dimension")
                if d and d not in plan.group_by and self._dim_on_table(d, plan.table):
                    plan.group_by.append(d)
            elif action == "set_limit":
                try:
                    plan.limit = int(hint.get("limit") or 0)
                except (TypeError, ValueError):
                    pass
            elif action == "set_order_by":
                fld = hint.get("field")
                if fld and fld in {plan.metric, *plan.extra_metrics, *plan.group_by}:
                    plan.order_by = [OrderBy(field=fld, dir=(hint.get("dir") or "desc"))]
            elif action == "drop_filter":
                d = hint.get("dimension")
                plan.filters = [f for f in plan.filters if f.dimension != d]
            elif action == "suppress_clarify":
                plan.needs_clarify = False
                plan.clarify_reason = ""
                plan.clarify_options = []
        return plan

    # --------------------------------------------------- critique_and_repair

    def critique_and_repair(
        self,
        question: str,
        plan: QueryPlan,
        *,
        rule_seed: dict[str, Any],
        bundle: Any | None = None,
        previous_plan: QueryPlan | None = None,
        inherit: bool = False,
    ) -> tuple[QueryPlan, CriticReport, bool]:
        """复核 → (至多一次) 确定性修复 → 复核。返回 (plan, 最终报告, 是否修复过)。

        若一次修复后仍有未解决的 fail（如表无法提供主诉求），保持安全：
        交回 needs_clarify（带可读理由），绝不带病编译。
        """
        # 初次复核：report 记录"检测到什么 + 打算怎么修"（保留作审计轨迹）。
        report = self.review(question, plan, rule_seed=rule_seed, bundle=bundle,
                             previous_plan=previous_plan, inherit=inherit)
        repaired = False
        if report.repair_hints:
            plan = self.repair(plan, report)
            repaired = True
            report.repair_hints = []  # 已执行，避免下游重复消费（诊断字段保留供审计）
            # 修复后再复核一次（SKILL：至多一次自动修复）：当前所有修复均为"补齐/纠正/放行"，
            # 理论上不会残留 fail；这里是防御式兜底——万一仍 fail，安全澄清而非带病编译。
            post = self.review(question, plan, rule_seed=rule_seed, bundle=bundle,
                              previous_plan=previous_plan, inherit=inherit)
            if post.severity == "fail":
                plan.needs_clarify = True
                if not plan.clarify_reason:
                    plan.clarify_reason = "无法确定查询口径，请补充指标或维度后重试"
                report.severity = "fail"
                report.ok = False
                report.reason = (report.reason + "；修复后仍存在问题，转澄清").strip("；")
        try:
            logger.info(
                "critic.review q=%r ok=%s sev=%s repaired=%s reason=%s",
                (question or "")[:60], report.ok, report.severity, repaired, report.reason,
            )
        except Exception:
            pass
        return plan, report, repaired

    # ------------------------------------------------------------ helpers

    def _equiv_on_table(self, metric_name: str, table: str) -> str | None:
        if not metric_name:
            return None
        try:
            return self.planner._equivalent_metric_on_table(metric_name, table)
        except Exception:
            md = self.semantic.metric(metric_name)
            return metric_name if (md and md.table == table) else None

    def _dim_on_table(self, dim_name: str, table: str) -> bool:
        d = self.semantic.dimension(dim_name)
        return bool(d and table in d.table_columns)

    def _structurally_complete(self, plan: QueryPlan) -> bool:
        from .plan import TimeKind
        if not (plan.metric and self.semantic.metric(plan.metric) and plan.table):
            return False
        has_signal = bool(plan.group_by or plan.filters or plan.calculation or plan.having)
        has_time = bool(plan.time_range and plan.time_range.kind != TimeKind.NONE)
        return has_signal and has_time

    def _metric_explicitly_named(self, metric_name: str, rule_seed: dict[str, Any], heli_ctx: bool) -> bool:
        hits = rule_seed.get("metric_hits") or []
        if heli_ctx:
            hits = [HELI30_METRIC_REMAP.get(h, h) for h in hits]
        if metric_name in hits:
            return True
        md = self.semantic.metric(metric_name)
        if not md:
            return False
        # 角色等价（销售额跨表）：命中的别名等价到当前表即算"点名"。
        for h in hits:
            if self._equiv_on_table(h, md.table) == metric_name:
                return True
        return False
