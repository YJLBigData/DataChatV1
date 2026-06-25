"""Result Validator 单元回归（2026-06-24）。

用合成 ExecResult 验证执行后校验（无 LLM/DB）：缺列/超限/TopN 不足/排序不一致/主指标全空/
0 行分类。校验只产出结构化报告 + 用户提示，绝不改数据、不伪造成功。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")

from app.core.exec import ExecResult  # noqa: E402
from app.core.nl2sql.plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange  # noqa: E402
from app.core.nl2sql.result_validator import ResultValidator  # noqa: E402

SUMMARY = "ads_bi_month_shop_item_dan_summary_df"


@pytest.fixture(scope="module")
def validator():
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    pipe.warmup()
    return pipe.validator


def _meta(*cols):
    return {"columns": {c: {"kind": "metric"} for c in cols}}


def _exec(columns, rows):
    return ExecResult(columns=list(columns), rows=[list(r) for r in rows],
                      row_count=len(rows), elapsed_ms=1, sql="")


def _topn_plan(limit=3, direction="desc"):
    return QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     order_by=[OrderBy(field="terminal_sale_amount_total", dir=direction)],
                     limit=limit, time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]))


def test_happy_path_no_issues(validator):
    plan = _topn_plan(limit=3, direction="desc")
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"], [["A", 300], ["B", 200], ["C", 100]])
    rep = validator.validate("各大区销售额前3", plan, meta, ex)
    assert rep.ok and rep.severity == "none" and rep.issues == []


def test_missing_columns_fail(validator):
    plan = _topn_plan()
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region"], [["A"], ["B"]])  # DB 漏了指标列
    rep = validator.validate("q", plan, meta, ex)
    assert not rep.ok and rep.severity == "fail"
    assert any(i.code == "missing_columns" for i in rep.issues)


def test_limit_overflow_fail(validator):
    plan = _topn_plan(limit=3)
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"],
               [["A", 5], ["B", 4], ["C", 3], ["D", 2], ["E", 1]])  # 5 > LIMIT 3
    rep = validator.validate("q", plan, meta, ex)
    assert not rep.ok
    assert any(i.code == "limit_overflow" for i in rep.issues)


def test_topn_underfill_warn(validator):
    plan = _topn_plan(limit=10)
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"], [["A", 5], ["B", 4], ["C", 3]])  # 仅3 < 10
    rep = validator.validate("q", plan, meta, ex)
    assert rep.severity == "warn"
    issue = next(i for i in rep.issues if i.code == "topn_underfill")
    assert issue.user_note  # 有面向用户的提示
    assert "3" in issue.user_note


def test_sort_mismatch_warn(validator):
    plan = _topn_plan(limit=3, direction="desc")  # 要求降序
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"], [["A", 100], ["B", 200], ["C", 300]])  # 实际升序
    rep = validator.validate("q", plan, meta, ex)
    assert any(i.code == "sort_mismatch" for i in rep.issues)


def test_sort_ok_with_ties_and_null(validator):
    plan = _topn_plan(limit=5, direction="desc")
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"],
               [["A", 300], ["B", 300], ["C", 200], ["D", None], ["E", 100]])  # 相等+NULL 容忍
    rep = validator.validate("q", plan, meta, ex)
    assert not any(i.code == "sort_mismatch" for i in rep.issues)


def test_metric_all_null_warn(validator):
    plan = _topn_plan(limit=3)
    meta = _meta("region", "terminal_sale_amount_total")
    ex = _exec(["region", "terminal_sale_amount_total"], [["A", None], ["B", None]])
    rep = validator.validate("q", plan, meta, ex)
    assert any(i.code == "metric_all_null" for i in rep.issues)


def test_empty_relative_no_user_note(validator):
    """相对时间 0 行：warn 但不出 user_note（由 orchestrator 统一补最新月份提示，避免重复）。"""
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"))
    ex = _exec(["region", "terminal_sale_amount_total"], [])
    rep = validator.validate("本月各大区销售额", plan, _meta("region", "terminal_sale_amount_total"), ex)
    issue = next(i for i in rep.issues if i.code == "empty_relative_time")
    assert issue.user_note == ""


def test_empty_filtered_has_user_note(validator):
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     filters=[PlanFilter(dimension="region", op="eq", values=["不存在区"])],
                     time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]))
    ex = _exec(["region", "terminal_sale_amount_total"], [])
    rep = validator.validate("不存在区销售额", plan, _meta("region", "terminal_sale_amount_total"), ex)
    issue = next(i for i in rep.issues if i.code == "empty_filtered")
    assert issue.user_note and rep.user_notes()


def test_empty_no_data(validator):
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY, group_by=["region"],
                     time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]))
    ex = _exec(["region", "terminal_sale_amount_total"], [])
    rep = validator.validate("q", plan, _meta("region", "terminal_sale_amount_total"), ex)
    assert any(i.code == "empty_no_data" for i in rep.issues)


def test_none_exec_safe(validator):
    plan = _topn_plan()
    rep = validator.validate("q", plan, _meta("region"), None)
    assert rep.ok and rep.issues == []
