"""End-to-end Golden Cases for DataChat.

These DO call the LLM and DO touch MySQL. Mark with `e2e`. Run with:
    pytest -m e2e tests/test_e2e.py -s

If `DASHSCOPE_API_KEY` or MySQL is unavailable, the tests are skipped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def pipeline():
    from app.core.config import load_config, reset_for_tests
    reset_for_tests()
    cfg = load_config(reload=True)
    if not cfg.llm.bailian_api_key:
        pytest.skip("DASHSCOPE_API_KEY not set")
    from app.core.exec import get_executor
    if not get_executor().health().get("ok"):
        pytest.skip("MySQL chatbi not reachable")
    from app.core.orchestrator import get_pipeline
    p = get_pipeline()
    return p


# Golden Case definitions: (question, asserts)
GOLDEN: list[tuple[str, dict]] = [
    (
        "本月各大区销售额排名",
        {
            "metric": "terminal_sale_amount_total",
            "group_by_includes": ["region"],
            "calculation": "rank",
            "min_rows": 6,           # 8 大区
            "needs_clarify": False,
        },
    ),
    (
        "卓睿系列最近3个月销售趋势",
        {
            "metric": "terminal_sale_amount_total",
            "filter_dim_includes": "item_series",
            "calculation": "trend",
            "min_rows": 3,
            "needs_clarify": False,
        },
    ),
    (
        "1段产品在各大区的销售情况",
        {
            "metric_in": ["terminal_sale_amount_total", "shop_sale_amount_total"],
            "filter_dim_includes": "item_dan",
            "group_by_includes": ["region"],
            "min_rows": 4,
            "needs_clarify": False,
        },
    ),
    (
        "销售目标完成率排前三的省区",
        {
            "metric": "shop_sale_achievement_rate",
            "group_by_includes": ["sub_region"],
            "calculation": "rank",
            "limit_max": 3,
            "needs_clarify": False,
        },
    ),
    (
        "北一区60天复购率",
        {
            "metric": "repurchase_rate_60d",
            "filter_dim_includes": "region",
            "min_rows": 1,
            "needs_clarify": False,
        },
    ),
]


@pytest.mark.parametrize("question,asserts", GOLDEN)
def test_golden_case(pipeline, question: str, asserts: dict):
    res = pipeline.run(question, user_id="exec_test", force_refresh=True, skip_llm_narrative=True)

    plan = res.plan
    answer = res.answer

    if "needs_clarify" in asserts:
        assert plan.get("needs_clarify") == asserts["needs_clarify"], f"clarify mismatch for {question!r}: plan={plan}"

    if "metric" in asserts:
        assert plan.get("metric") == asserts["metric"], f"wrong metric for {question!r}: got {plan.get('metric')}"
    if "metric_in" in asserts:
        assert plan.get("metric") in asserts["metric_in"], f"metric not in expected list for {question!r}: got {plan.get('metric')}"
    if "calculation" in asserts:
        assert plan.get("calculation") == asserts["calculation"], f"wrong calc for {question!r}"
    if "group_by_includes" in asserts:
        for d in asserts["group_by_includes"]:
            assert d in (plan.get("group_by") or []), f"group_by missing {d} for {question!r}: got {plan.get('group_by')}"
    if "filter_dim_includes" in asserts:
        dims = [f["dimension"] for f in plan.get("filters", [])]
        assert asserts["filter_dim_includes"] in dims, f"filter dim missing for {question!r}: got {dims}"
    if "min_rows" in asserts:
        assert res.rows >= asserts["min_rows"], f"too few rows for {question!r}: {res.rows}"
    if "limit_max" in asserts:
        assert plan.get("limit", 0) <= asserts["limit_max"], f"limit too large for {question!r}: {plan.get('limit')}"

    # Universal accuracy guards
    assert res.sql, f"empty SQL for {question!r}"
    assert "DROP" not in res.sql.upper()
    assert "INSERT" not in res.sql.upper()
    assert "DELETE" not in res.sql.upper()


def test_smoke_full_narrative(pipeline):
    """Single full-narrative test to verify LLM summarization works."""
    res = pipeline.run("本月各大区销售额排名", user_id="exec_test", force_refresh=True, skip_llm_narrative=False)
    a = res.answer
    assert a.get("narrative")
    assert isinstance(a.get("highlights"), list)
    assert a.get("table") and a["table"]["row_count"] >= 6
    # Narrative shouldn't reference fake data
    assert "示例" not in a["narrative"]
    assert "假设" not in a["narrative"]
