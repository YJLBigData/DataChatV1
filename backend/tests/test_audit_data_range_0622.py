"""时间口径回归（2026-06-22 审计 P1）：相对时间按**表级真实最新分区**解析。

审计发现：semantic.yaml 写死 data_range.latest=2026-05，但本地核心表只到 2026-04，
"本月各大区销售额排名"生成了 2026-05 的查询、静默返回 0 行。修复：compiler 用注入的
latest_month_provider（真实分区）解析相对时间，取不到再回退 semantic 口径。

全部离线：用注入的假 provider，不触达真实 MySQL。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(scope="module")
def semantic():
    from app.core.semantic import SemanticLayer
    return SemanticLayer(str(BACKEND / "config" / "semantic.yaml"))


def _this_month_plan():
    from app.core.nl2sql.plan import QueryPlan, TimeRange, TimeKind
    return QueryPlan(
        metric="terminal_sale_amount_total",
        table="ads_bi_month_shop_item_dan_summary_df",
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
    )


def test_relative_period_uses_table_latest_partition(semantic):
    """provider 返回真实最新分区 2026-04 → "本月"解析成 2026-04，而非 semantic 写死的 2026-05。"""
    from app.core.nl2sql.compiler import PlanCompiler
    compiler = PlanCompiler(semantic, default_limit=500, latest_month_provider=lambda t: "2026-04")
    sql, _ = compiler.compile(_this_month_plan())
    assert "`year` = '2026'" in sql
    assert "`month` = '04'" in sql
    assert "`month` = '05'" not in sql


def test_relative_period_falls_back_to_semantic_when_provider_none(semantic):
    """provider 取不到（无 DB/异常）→ 安全回退到 semantic.data_range_latest。"""
    from app.core.nl2sql.compiler import PlanCompiler
    assert semantic.data_range_latest == "2026-05"
    compiler = PlanCompiler(semantic, default_limit=500, latest_month_provider=lambda t: None)
    sql, _ = compiler.compile(_this_month_plan())
    assert "`month` = '05'" in sql


def test_provider_exception_does_not_break_compile(semantic):
    """provider 抛错也不能让编译崩（回退 semantic 口径）。"""
    from app.core.nl2sql.compiler import PlanCompiler

    def boom(_t):
        raise RuntimeError("db down")

    compiler = PlanCompiler(semantic, default_limit=500, latest_month_provider=boom)
    sql, _ = compiler.compile(_this_month_plan())
    assert "`month` = '05'" in sql  # 回退 semantic


def test_pipeline_emits_data_range_note_on_empty_relative(monkeypatch):
    """相对时间却 0 行 → answer 叙述里必须给出"最新可用月份"提示，并发 data_range 事件。"""
    from app.core.orchestrator import get_pipeline
    from app.core.exec.mysql_exec import ExecResult

    pipe = get_pipeline()
    pipe.warmup()

    def boom(*a, **k):
        raise RuntimeError("force rule-only")
    pipe.planner.llm.chat_json = boom

    # 强制缓存 miss，确保走到 answer 阶段（否则命中 L1/L2 会跳过提示注入）
    monkeypatch.setattr(pipe.cache, "get_question", lambda *a, **k: None)
    monkeypatch.setattr(pipe.cache, "get_plan", lambda *a, **k: None)
    monkeypatch.setattr(pipe.cache, "get_sql_result", lambda *a, **k: None)

    # 模拟执行返回 0 行 + 表最新分区为 2026-04
    monkeypatch.setattr(pipe.executor, "run_select",
                        lambda *a, **k: ExecResult(columns=["region", "terminal_sale_amount_total"],
                                                   rows=[], row_count=0, elapsed_ms=1, sql=a[0] if a else ""))
    monkeypatch.setattr(pipe, "_table_latest_month", lambda t: "2026-04")

    result = pipe.run("本月各大区销售额排名", user_id="u", is_admin=True, force_refresh=False)
    narrative = str((result.answer or {}).get("narrative") or "")
    assert "最新可用月份" in narrative and "2026-04" in narrative, narrative
    assert any(e.get("stage") == "data_range" for e in result.events)
