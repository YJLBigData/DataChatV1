"""端到端集成回归（2026-06-24）：在 **真 Pipeline.run() 路径**上验证
Accuracy Critic（Stage 3.4）与 Result Validator（Stage 7.1）确实接入并生效。

本地无 MySQL/LLM：强制 planner 旁路 LLM（rule-only），并桩掉 executor 返回与编译列一致的
合成结果（按 ORDER BY 方向排好序），从而把 run() 跑通到答案定稿——这是本环境下对 SKILL
"用户视角重测"的最强等价验证：证明 critic/validator 事件真触发、误澄清被抑制、校验报告落地。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")

from app.core.exec import ExecResult  # noqa: E402

P0_MULTI = "2025年1月各大区精准潜客人数、转新人数和转新率是多少？转新率最低的5个大区列出来。"


@pytest.fixture()
def pipe_stubbed():
    """rule-only planner + 桩 executor（回显编译列、按 ORDER BY 排序）。"""
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    pipe.warmup()

    def _boom(*a, **k):
        raise RuntimeError("force rule-only path")
    pipe.planner.llm.chat_json = _boom

    orig = pipe.executor.run_select

    def _stub(sql, max_rows=None, timeout_ms=None):
        cols = re.findall(r"AS `([^`]+)`", sql)
        if not cols:  # latest-month probe 等
            return ExecResult(columns=["m"], rows=[["2026-05"]], row_count=1, elapsed_ms=1, sql=sql)
        rows = []
        for i in range(3):
            row = []
            for c in cols:
                if pipe.semantic.dimension(c) is not None or c == "__period":
                    row.append(f"{c}_{i}")
                else:
                    row.append(float(3 - i))  # 默认降序
            rows.append(row)
        m = re.search(r"ORDER BY `([^`]+)` (ASC|DESC)", sql)
        if m and m.group(1) in cols:
            idx = cols.index(m.group(1))
            if all(isinstance(r[idx], float) for r in rows):
                rows.sort(key=lambda r: r[idx], reverse=(m.group(2) == "DESC"))
        return ExecResult(columns=cols, rows=rows, row_count=len(rows), elapsed_ms=1, sql=sql)

    pipe.executor.run_select = _stub
    try:
        yield pipe
    finally:
        pipe.executor.run_select = orig


def _stages(res):
    return {e.get("stage") for e in res.events}


def test_p0_multi_metric_runs_clean_through_full_pipeline(pipe_stubbed):
    res = pipe_stubbed.run(P0_MULTI, user_id="default", is_admin=True,
                           force_refresh=True, skip_llm_narrative=True)
    assert res.ok, f"pipeline failed: {res.answer}"
    plan = res.plan
    assert not plan.get("needs_clarify"), "P0 多指标问句不得触发澄清"
    selected = {plan.get("metric"), *(plan.get("extra_metrics") or [])}
    # 三个指标都被选出
    assert {"potential_num_total", "potential_to_new_num_total", "potential_to_new_rate"} <= selected
    assert plan.get("limit") == 5  # 转新率最低的5个
    # critic + validate 阶段都真的跑了
    stages = _stages(res)
    assert "critic" in stages, f"critic stage missing: {stages}"
    assert "validate" in stages, f"validate stage missing: {stages}"
    # 干净路径：validate 事件为 ok
    vevt = next(e for e in res.events if e.get("stage") == "validate")
    assert vevt.get("status") == "ok", f"unexpected validation: {vevt}"


def test_validator_attaches_report_on_limit_overflow(pipe_stubbed):
    """桩 executor 返回多于 LIMIT 的行（模拟越限）→ 校验判 limit_overflow(fail)，
    落 explainability.validation，但绝不阻断主结果（res.ok 仍 True）。"""
    def _overflow(sql, max_rows=None, timeout_ms=None):
        cols = re.findall(r"AS `([^`]+)`", sql)
        if not cols:
            return ExecResult(columns=["m"], rows=[["2026-05"]], row_count=1, elapsed_ms=1, sql=sql)
        rows = []
        for i in range(8):  # 远多于 LIMIT 5
            row = [f"{c}_{i}" if (pipe_stubbed.semantic.dimension(c) is not None or c == "__period")
                   else float(8 - i) for c in cols]
            rows.append(row)
        return ExecResult(columns=cols, rows=rows, row_count=len(rows), elapsed_ms=1, sql=sql)
    pipe_stubbed.executor.run_select = _overflow

    res = pipe_stubbed.run(P0_MULTI, user_id="default", is_admin=True,
                           force_refresh=True, skip_llm_narrative=True)
    assert res.ok  # 校验不阻断主结果
    vevt = next(e for e in res.events if e.get("stage") == "validate")
    assert vevt.get("status") == "flagged"
    assert "limit_overflow" in (vevt.get("payload") or {}).get("issues", [])
    validation = (res.answer.get("explainability") or {}).get("validation")
    assert validation and validation.get("ok") is False
