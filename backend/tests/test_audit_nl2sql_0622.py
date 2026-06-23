"""智能问数口径回归（2026-06-22 严格审计 12 条样例 / 26 轮）。

审计结论：26 轮 HTTP 全 ok=true，但业务判定 13 FAIL / 2 WARN / 11 PASS —— "SQL 跑通但答错业务
问题"是 P0。本测试把 13 个 FAIL 钉成回归，并守住已 PASS 的口径不被改回。

测试策略（离线、不依赖真实 LLM/MySQL）：
  强制 planner 的 LLM 抛错 → 走**规则兜底**路径（rule-only + 确定性校验/修复），再用
  确定性 compiler 生成 SQL，断言 SQL 形状/口径符合 references/positive_sql_examples.json。
  规则兜底是"最坏情形下限"——生产里 LLM 在位时只会更准（同一套校验/修复同样兜底 LLM 误差）。
  集合延续类（"这3个大区"）需要上一轮结果，本地用模拟结果行注入。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")

TODAY = date(2025, 2, 1)  # 样例都问 2025-01，固定 today 杜绝相对时间漂移


@pytest.fixture(scope="module")
def planner_env():
    """规则兜底 planner + 确定性 compiler（强制旁路 LLM）。"""
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    pipe.warmup()

    def _boom(*a, **k):
        raise RuntimeError("force rule-only path for deterministic audit test")
    pipe.planner.llm.chat_json = _boom
    return pipe


def _plan_sql(pipe, q, *, prev=None, hist=None, rows=None):
    pr = pipe.planner.plan(q, today=TODAY, previous_plan=prev, history=hist, previous_rows=rows)
    sql, _ = pipe.compiler.compile(pr.plan)
    return pr.plan, sql


# =============================================================== P0 FAIL→PASS

def test_case3_difference_computes_diff_and_sorts_by_abs(planner_env):
    """case 3-1：终端销售额与还原过单金额差异，差异最大前10。必须算差值并按差值绝对值降序。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各大区终端销售金额和还原过单金额差异是多少？差异金额最大的前10个大区列出来。")
    assert plan.calculation == "delta"
    assert "SUM(terminal_sale_amount) - SUM(reduction_gd_sale_amount) AS `diff_amount`" in sql
    assert "ABS(SUM(terminal_sale_amount) - SUM(reduction_gd_sale_amount)) AS `diff_abs`" in sql
    assert "ORDER BY `diff_abs` DESC" in sql
    assert "LIMIT 10" in sql
    # 旧 bug：按还原过单金额排序，不是按差异
    assert "ORDER BY `reduction_gd_sale_amount_total`" not in sql


def test_case3_followup_drill_keeps_diff(planner_env):
    """case 3-2：'继续下钻到省区' 必须延续差值口径并下钻 sub_region。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区终端销售金额和还原过单金额差异是多少？差异金额最大的前10个大区列出来。")
    plan, sql = _plan_sql(planner_env, "继续下钻到省区。", prev=p1, hist=[{"role": "user", "content": "差异最大前10大区"}])
    assert plan.calculation == "delta"
    assert "`lev3_name` AS `sub_region`" in sql
    assert "AS `diff_abs`" in sql and "ORDER BY `diff_abs` DESC" in sql


def test_case6_followup_multi_stage_in_and_group(planner_env):
    """case 6-2：'只看1段、2段、3段分别' 必须 IN 三段且把段位作为输出维度。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区首购人数、60天复购人数和60天复购率是多少？按复购率从高到低排序。")
    plan, sql = _plan_sql(planner_env, "只看1段、2段、3段分别是多少。", prev=p1, hist=[{"role": "user", "content": "各大区复购率"}])
    assert "item_dan_name` IN ('1段', '2段', '3段')" in sql
    assert "`item_dan_name` AS `item_dan`" in sql
    assert "item_dan" in plan.group_by
    # 旧 bug：只筛 1 段
    assert "`item_dan_name` = '1段'" not in sql


def test_case7_heli30_uses_dedicated_fields(planner_env):
    """case 7-1：鹤礼3.0 新客/复购/复购率必须用鹤礼专属字段，不用通用首购/复购。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各渠道鹤礼3.0新客数、鹤礼3.060天复购数和复购率是多少？")
    assert "SUM(heli30_new_customer_num)" in sql
    assert "SUM(heli30_repurchase_in_60_days_num)" in sql
    assert "SUM(heli30_repurchase_in_60_days_num) / NULLIF(SUM(heli30_new_customer_num), 0)" in sql
    # 旧 bug：错用通用 repurchase/first_purchase
    assert "SUM(first_purchase_num)" not in sql
    assert "/ NULLIF(SUM(first_purchase_num), 0)" not in sql


def test_case7_followup_inherits_heli30(planner_env):
    """case 7-2：'东一区表现怎么样' 必须继承鹤礼3.0口径（不退化成通用复购）。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各渠道鹤礼3.0新客数、鹤礼3.060天复购数和复购率是多少？")
    plan, sql = _plan_sql(planner_env, "东一区表现怎么样？", prev=p1, hist=[{"role": "user", "content": "各渠道鹤礼3.0"}])
    assert "SUM(heli30_new_customer_num)" in sql and "SUM(heli30_repurchase_in_60_days_num)" in sql
    assert "`lev2_name` = '东一区'" in sql
    assert "SUM(first_purchase_num)" not in sql


def test_case8_potential_rate_bottom5(planner_env):
    """case 8-1：转新率最低的5个大区。必须算转新率、按率升序、LIMIT 5。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？转新率最低的5个大区列出来。")
    assert "SUM(potential_to_new_num) / NULLIF(SUM(potential_num), 0)" in sql
    assert "ORDER BY `potential_to_new_rate` ASC" in sql
    assert "LIMIT 5" in sql
    # 旧 bug：按潜客人数排序、LIMIT 500
    assert "ORDER BY `potential_num_total`" not in sql


def test_case8_followup_subregion_east1(planner_env):
    """case 8-2：'下钻到省区，并只看东一区' 仍要输出转新率并按率排序。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？转新率最低的5个大区列出来。")
    plan, sql = _plan_sql(planner_env, "下钻到省区，并只看东一区。", prev=p1, hist=[{"role": "user", "content": "转新率最低5大区"}])
    assert "SUM(potential_to_new_num) / NULLIF(SUM(potential_num), 0)" in sql
    assert "`lev3_name` AS `sub_region`" in sql
    assert "`lev2_name` = '东一区'" in sql
    assert "ORDER BY `potential_to_new_rate` ASC" in sql


def test_case9_top20_shops_detail(planner_env):
    """case 9-1：东一区销售额最高20门店，列门店/经销商/城市/导购+数量/金额/过单。"""
    plan, sql = _plan_sql(planner_env, "2025年1月东一区销售金额最高的20个门店是哪些？列出门店名称、经销商、城市、导购姓名、销售数量、销售金额和过单金额。")
    assert plan.table == "ads_bi_hs_sale_info_df"
    for col in ("`shop_name`", "`dealer_name`", "`official_city`", "`guide_name`"):
        assert col in sql, f"缺少门店维度列 {col}"
    assert "SUM(shop_sale_amount) AS `shop_sale_amount_total`" in sql
    assert "SUM(shop_sale_qty)" in sql and "SUM(gd_amount)" in sql
    assert "ORDER BY `shop_sale_amount_total` DESC" in sql
    assert "LIMIT 20" in sql
    assert "`lev2_name` = '东一区'" in sql


def test_case10_guide_shop_grouping_and_ratio(planner_env):
    """case 10-1：有导/非导门店分组，返回金额/数量/过单 + 销售金额占比。"""
    plan, sql = _plan_sql(planner_env, "2025年1月有导门店和非导门店的销售金额、销售数量、过单金额分别是多少？销售金额占比分别是多少？")
    assert "GROUP BY `is_guide_shop`" in sql
    assert plan.calculation == "ratio"
    assert "SUM(shop_sale_amount) AS `shop_sale_amount_total`" in sql
    assert "SUM(shop_sale_qty)" in sql and "SUM(gd_amount)" in sql
    assert "NULLIF(SUM(SUM(shop_sale_amount)) OVER (), 0)" in sql  # 占比
    # 旧 bug：无分组、无销售金额、无占比
    assert "_ratio`" in sql


def test_case12_potential_rate_region(planner_env):
    """case 12-1：各大区转新率，按转新率从低到高。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？按转新率从低到高排序。")
    assert "SUM(potential_to_new_num) / NULLIF(SUM(potential_num), 0)" in sql
    assert "ORDER BY `potential_to_new_rate` ASC" in sql


def test_case12_followup_bottom3(planner_env):
    """case 12-2：'只看转新率最低的3个大区' → LIMIT 3、按率升序。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？按转新率从低到高排序。")
    plan, sql = _plan_sql(planner_env, "只看转新率最低的3个大区。", prev=p1, hist=[{"role": "user", "content": "各大区转新率"}])
    assert "ORDER BY `potential_to_new_rate` ASC" in sql
    assert "LIMIT 3" in sql


def test_case12_followup_set_carryover_subregion(planner_env):
    """case 12-3：'把这3个大区下钻到省区' 必须严格沿用上一轮3个大区（IN 过滤），不再 LIMIT 3。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？按转新率从低到高排序。")
    p2, _ = _plan_sql(planner_env, "只看转新率最低的3个大区。", prev=p1, hist=[{"role": "user", "content": "各大区转新率"}])
    rows = {"columns": ["region", "potential_to_new_rate", "potential_num_total", "potential_to_new_num_total"],
            "rows": [["东二区", 0.0339, 1181, 40], ["东一区", 0.0407, 1990, 81], ["中一区", 0.0411, 900, 37]]}
    plan, sql = _plan_sql(planner_env, "把这3个大区下钻到省区。", prev=p2, hist=[{"role": "user", "content": "最低3个大区"}], rows=rows)
    assert "`lev2_name` IN ('东二区', '东一区', '中一区')" in sql
    assert "`lev3_name` AS `sub_region`" in sql
    assert "region" in plan.group_by and "sub_region" in plan.group_by
    # 关键：不能再被截成 3 行
    assert "LIMIT 3" not in sql


def test_case12_followup_channel_having(planner_env):
    """case 12-4：再按渠道拆，潜客人数>50 且 转新率<5%。HAVING 必须是 rate<0.05，绝不是 potential_num<5。"""
    p1, _ = _plan_sql(planner_env, "2025年1月各大区精准潜客人数、转新人数和转新率是多少？按转新率从低到高排序。")
    p2, _ = _plan_sql(planner_env, "只看转新率最低的3个大区。", prev=p1, hist=[{"role": "user", "content": "各大区转新率"}])
    rows2 = {"columns": ["region", "potential_to_new_rate", "potential_num_total", "potential_to_new_num_total"],
             "rows": [["东二区", 0.0339, 1181, 40], ["东一区", 0.0407, 1990, 81], ["中一区", 0.0411, 900, 37]]}
    p3, _ = _plan_sql(planner_env, "把这3个大区下钻到省区。", prev=p2, hist=[{"role": "user", "content": "最低3个大区"}], rows=rows2)
    rows3 = {"columns": ["region", "sub_region", "potential_to_new_rate", "potential_num_total", "potential_to_new_num_total"],
             "rows": [["东二区", "鲁北", 0.0339, 1181, 40], ["东一区", "苏北", 0.0347, 779, 27]]}
    plan, sql = _plan_sql(planner_env, "再按大系统渠道拆开，找出潜客人数大于50但转新率低于5%的渠道。",
                          prev=p3, hist=[{"role": "user", "content": "下钻省区"}], rows=rows3)
    assert "`big_system_channel_name` AS `big_system_channel`" in sql
    assert "(SUM(potential_to_new_num) / NULLIF(SUM(potential_num), 0)) < 0.05" in sql
    assert "SUM(potential_num)) > 50" in sql
    # 旧 bug：转新率低于5% 被编译成 SUM(potential_num) < 5
    assert "< 5\n" not in sql and "(SUM(potential_num)) < 5" not in sql
    # 上一轮3个大区集合仍在
    assert "`lev2_name` IN (" in sql


# =============================================================== 守住已 PASS 口径

def test_case1_achievement_rate_asc_unchanged(planner_env):
    """case 1-1（PASS 守护）：各大区达成率从低到高，仍走 target 表达成率口径。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各大区门店销售金额、门店销售目标和销售达成率分别是多少？按达成率从低到高排序。")
    assert "SUM(shop_sale_amount) / NULLIF(SUM(shop_sale_target), 0)" in sql
    assert "ORDER BY `shop_sale_achievement_rate` ASC" in sql
    assert plan.table == "ads_bi_month_shop_item_dan_target_summary_df"


def test_case4_series_ratio_unchanged(planner_env):
    """case 4-1（PASS 守护）：东一区各产品系列终端销售额占比。"""
    plan, sql = _plan_sql(planner_env, "2025年1月东一区各产品系列的终端销售金额占比是多少？")
    assert plan.calculation == "ratio"
    assert "`item_series_new_name` AS `item_series`" in sql
    assert "`lev2_name` = '东一区'" in sql
    assert "_ratio`" in sql


def test_case5_stage_ratio_unchanged(planner_env):
    """case 5-1（PASS 守护）：各段位终端销售额与占比。"""
    plan, sql = _plan_sql(planner_env, "2025年1月各段位的终端销售金额和销售占比是多少？")
    assert plan.calculation == "ratio"
    assert "`item_dan_name` AS `item_dan`" in sql
    assert "SUM(terminal_sale_amount)" in sql
