"""Accuracy Critic 单元回归（2026-06-24）。

直接构造 QueryPlan + rule_seed 喂给 Critic，验证其确定性复核/修复：
  · 误澄清放行（仅当用户已显式点名指标 + 结构完整）；
  · 真歧义（未点名）不被放行；
  · 漏选的显式指标补为附带列；TopN 漏截补 LIMIT；排序方向纠正；越表过滤丢弃；
  · 鹤礼3.0 上下文不被"补"回通用首购/复购；
  · 已正确的 plan 复核为 no-op（零回归）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")

from app.core.nl2sql.plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange  # noqa: E402

HS = "ads_bi_hs_sale_info_df"
SUMMARY = "ads_bi_month_shop_item_dan_summary_df"
POTENTIAL = "ads_precision_nutrition_potential_total_df"
MEMBER = "ads_member_first_purchase_new_customer_total_df"


@pytest.fixture(scope="module")
def critic():
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    pipe.warmup()
    return pipe.critic


def _seed(**kw):
    base = {"calculation": "", "rank_n": 0, "metric_hits": [], "group_by_hint": [],
            "order_hint": None, "filter_hits": [], "having_hints": []}
    base.update(kw)
    return base


def _abs_time():
    return TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"])


# ----------------------------------------------------- 漏选指标补齐

def test_adds_missing_named_metrics(critic):
    plan = QueryPlan(metric="shop_sale_amount_total", extra_metrics=[], table=HS,
                     group_by=["shop"], time_range=_abs_time())
    seed = _seed(metric_hits=["shop_sale_qty_total", "shop_sale_amount_total", "gd_amount_total"])
    rep = critic.review("各门店销售数量、销售金额和过单金额", plan, rule_seed=seed)
    assert "shop_sale_qty_total" in rep.missing_metrics
    assert "gd_amount_total" in rep.missing_metrics
    plan2 = critic.repair(plan, rep)
    assert "shop_sale_qty_total" in plan2.extra_metrics
    assert "gd_amount_total" in plan2.extra_metrics


def test_sales_amount_role_equiv_not_flagged(critic):
    """问句"销售金额"（terminal 别名）落到明细表，已选 shop_sale_amount_total → 不应判缺。"""
    plan = QueryPlan(metric="shop_sale_amount_total", table=HS, group_by=["shop"], time_range=_abs_time())
    seed = _seed(metric_hits=["terminal_sale_amount_total"])  # 销售金额 → terminal，等价到明细 = shop_sale_amount
    rep = critic.review("各门店销售金额", plan, rule_seed=seed)
    assert rep.missing_metrics == []


# ----------------------------------------------------- TopN / 排序

def test_adds_missing_topn_limit(critic):
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     order_by=[OrderBy(field="terminal_sale_amount_total", dir="desc")],
                     limit=0, time_range=_abs_time())
    seed = _seed(rank_n=10, metric_hits=["terminal_sale_amount_total"])
    plan2, rep, repaired = critic.critique_and_repair("销售额最高的10个大区", plan, rule_seed=seed)
    assert repaired and plan2.limit == 10


def test_fixes_wrong_order_direction(critic):
    """转新率最低 → 应 asc；LLM 给了 desc → Critic 纠正。"""
    plan = QueryPlan(metric="potential_to_new_rate", table=POTENTIAL, group_by=["region"],
                     order_by=[OrderBy(field="potential_to_new_rate", dir="desc")], time_range=_abs_time())
    seed = _seed(metric_hits=["potential_to_new_rate"],
                 order_hint={"field": "potential_to_new_rate", "dir": "asc"})
    plan2, rep, repaired = critic.critique_and_repair("转新率最低的大区", plan, rule_seed=seed)
    assert plan2.order_by[0].dir == "asc"


def test_delta_order_not_touched(critic):
    """派生口径 delta 的规范排序（diff_abs）不应被 order_hint 干预。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", extra_metrics=["reduction_gd_sale_amount_total"],
                     table=SUMMARY, group_by=["region"], calculation="delta",
                     order_by=[OrderBy(field="diff_abs", dir="desc")], time_range=_abs_time())
    seed = _seed(calculation="delta", metric_hits=["terminal_sale_amount_total", "reduction_gd_sale_amount_total"],
                 order_hint={"field": "reduction_gd_sale_amount_total", "dir": "desc"})
    rep = critic.review("差异最大的大区", plan, rule_seed=seed)
    assert rep.wrong_order_by == ""  # delta 跳过排序复核


# ----------------------------------------------------- 误澄清放行

def test_suppresses_false_clarify_when_named(critic):
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     time_range=_abs_time(), needs_clarify=True, clarify_reason="多口径请选择")
    seed = _seed(metric_hits=["terminal_sale_amount_total"])
    plan2, rep, repaired = critic.critique_and_repair("各大区终端销售额", plan, rule_seed=seed)
    assert rep.clarify_should_be_suppressed
    assert plan2.needs_clarify is False


def test_keeps_clarify_when_metric_not_named(critic):
    """真歧义（用户未点名指标）→ 绝不放行，尊重澄清。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     time_range=_abs_time(), needs_clarify=True, clarify_reason="多口径请选择")
    seed = _seed(metric_hits=[])  # 未点名
    plan2, rep, repaired = critic.critique_and_repair("帮我看下各大区情况", plan, rule_seed=seed)
    assert not rep.clarify_should_be_suppressed
    assert plan2.needs_clarify is True


def test_no_suppress_when_structurally_incomplete(critic):
    """点名了指标但结构不完整（无任何维度/过滤/算子/时间）→ 不放行。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=[],
                     time_range=TimeRange(kind=TimeKind.NONE), needs_clarify=True)
    seed = _seed(metric_hits=["terminal_sale_amount_total"])
    plan2, rep, repaired = critic.critique_and_repair("终端销售额", plan, rule_seed=seed)
    assert plan2.needs_clarify is True


# ----------------------------------------------------- 越表过滤 / 鹤礼

def test_drops_off_table_filter(critic):
    """terminal 在 summary，但过滤维度 guide 只在明细表 → 丢弃该越表过滤。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     filters=[PlanFilter(dimension="guide", op="eq", values=["张三"])], time_range=_abs_time())
    seed = _seed(metric_hits=["terminal_sale_amount_total"])
    plan2, rep, repaired = critic.critique_and_repair("各大区终端销售额", plan, rule_seed=seed)
    assert all(f.dimension != "guide" for f in plan2.filters)


def test_heli30_not_reverted_to_generic(critic):
    """鹤礼上下文：plan 已用鹤礼专属指标，rule_seed 里是通用首购/复购 →
    Critic 必须按鹤礼重映射比对，绝不把通用 first_purchase 补回去。"""
    plan = QueryPlan(metric="heli30_new_customer_num_total",
                     extra_metrics=["heli30_repurchase_in_60_days_num_total"],
                     table=MEMBER, group_by=["big_system_channel"], time_range=_abs_time())
    seed = _seed(metric_hits=["heli30_new_customer_num_total", "heli30_repurchase_in_60_days_num_total"])
    rep = critic.review("各渠道鹤礼3.0新客数、鹤礼3.060天复购数", plan, rule_seed=seed)
    assert "first_purchase_num_total" not in rep.missing_metrics
    assert rep.missing_metrics == []


# ----------------------------------------------------- 触发门 / no-op

def test_should_review_gating(critic):
    from app.core.nl2sql.accuracy_critic import AccuracyCritic
    simple = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                       confidence=0.7, time_range=_abs_time())
    assert AccuracyCritic.should_review(simple, _seed(), inherit=False) is False
    assert AccuracyCritic.should_review(simple, _seed(rank_n=5), inherit=False) is True
    assert AccuracyCritic.should_review(simple, _seed(metric_hits=["a", "b"]), inherit=False) is True
    assert AccuracyCritic.should_review(simple, _seed(), inherit=True) is True


def test_noop_on_correct_plan(critic):
    """已正确的多指标 plan → 复核 ok，不产生任何修复指令（零回归保证）。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", extra_metrics=["reduction_gd_sale_amount_total"],
                     table=SUMMARY, group_by=["region"], time_range=_abs_time())
    seed = _seed(metric_hits=["terminal_sale_amount_total", "reduction_gd_sale_amount_total"],
                 group_by_hint=["region"])
    rep = critic.review("各大区终端销售额和还原过单金额", plan, rule_seed=seed)
    assert rep.ok and rep.severity == "none" and rep.repair_hints == []
