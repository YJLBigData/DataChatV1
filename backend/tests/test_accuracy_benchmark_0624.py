"""扩展准确率基准（2026-06-24）——把"业务意图正确"钉成可回归的机器可读基准。

SKILL 要求：≥100 道高频业务问题，断言**业务意图**（表/指标/维度/过滤/时间/排序/LIMIT/澄清），
而非 HTTP ok=true；P0 审计失败 100% 通过；扩展集 ≥90% 通过。

测试策略（与 test_audit_nl2sql_0622 一致，离线、不依赖真 LLM/MySQL）：
  强制 planner 旁路 LLM → 走规则兜底（最坏下限）→ **经 Accuracy Critic 复核/修复** →
  确定性 compiler 生成 SQL。逐条断言 plan 字段 + SQL 片段。规则兜底是下限，生产 LLM 在位只会更准。
  追问类用例链式复用上一轮 plan；"这N个"集合延续注入上一轮结果行。

运行后把逐条结果写入 output/audit/accuracy_benchmark_result.json，并打印汇总（总数/通过/失败/准确率）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_ENV", "test")

from tests.accuracy_benchmark_cases import BENCH_CASES  # noqa: E402

DEFAULT_TODAY = date(2025, 2, 1)


@pytest.fixture(scope="module")
def bench_env():
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    pipe.warmup()

    def _boom(*a, **k):
        raise RuntimeError("force rule-only path for deterministic benchmark")
    pipe.planner.llm.chat_json = _boom
    return pipe


def _run_case(pipe, case, plan_cache):
    """跑一条用例的"准确率核心路径"：plan → critic 复核/修复 → compile。返回 (plan, sql)。"""
    q = case["question"]
    today = date.fromisoformat(case["today"]) if case.get("today") else DEFAULT_TODAY
    prev_plan = plan_cache.get(case["prev"]) if case.get("prev") else None
    hist = case.get("history")
    rows = None
    if case.get("rows_from"):
        rows = case["rows_from"] if isinstance(case["rows_from"], dict) else plan_cache.get("__rows__" + case["rows_from"])
    pr = pipe.planner.plan(q, today=today, previous_plan=prev_plan, history=hist, previous_rows=rows)
    plan = pr.plan
    followup = pipe.planner._looks_like_followup(q, prev_plan)
    if pipe.critic.should_review(plan, pr.rule_seed, followup):
        plan, _report, _repaired = pipe.critic.critique_and_repair(
            q, plan, rule_seed=pr.rule_seed, bundle=pr.bundle,
            previous_plan=prev_plan, inherit=followup,
        )
    sql = ""
    if not plan.needs_clarify and not plan.out_of_scope and plan.metric:
        sql, _ = pipe.compiler.compile(plan)
    return plan, sql


def _check_expect(plan, sql, expect):
    """只校验 expect 中给出的键，返回失败原因列表（空=通过）。"""
    fails: list[str] = []
    selected = {plan.metric, *plan.extra_metrics}

    if "table" in expect and plan.table != expect["table"]:
        fails.append(f"table={plan.table!r} != {expect['table']!r}")
    if "metric" in expect and plan.metric != expect["metric"]:
        fails.append(f"metric={plan.metric!r} != {expect['metric']!r}")
    if "metric_in" in expect and plan.metric not in expect["metric_in"]:
        fails.append(f"metric={plan.metric!r} not in {expect['metric_in']}")
    for m in expect.get("extra_superset", []):
        if m not in selected:
            fails.append(f"missing metric {m!r} (selected={sorted(selected)})")
    for d in expect.get("group_superset", []):
        if d not in plan.group_by:
            fails.append(f"missing group_by {d!r} (got={plan.group_by})")
    for d in expect.get("group_absent", []):
        if d in plan.group_by:
            fails.append(f"unexpected group_by {d!r}")
    for dim, vals in (expect.get("filters") or {}).items():
        match = next((f for f in plan.filters if f.dimension == dim), None)
        if not match:
            fails.append(f"missing filter {dim!r}")
        else:
            for v in vals:
                if v not in match.values:
                    fails.append(f"filter {dim!r} missing value {v!r} (got={match.values})")
    if "calculation" in expect and plan.calculation != expect["calculation"]:
        fails.append(f"calculation={plan.calculation!r} != {expect['calculation']!r}")
    if "time_kind" in expect:
        tk = getattr(plan.time_range.kind, "value", str(plan.time_range.kind))
        if tk != expect["time_kind"]:
            fails.append(f"time_kind={tk!r} != {expect['time_kind']!r}")
    if "order_field" in expect:
        of = plan.order_by[0].field if plan.order_by else None
        if of != expect["order_field"]:
            fails.append(f"order_field={of!r} != {expect['order_field']!r}")
    if "order_dir" in expect:
        od = (plan.order_by[0].dir if plan.order_by else "").lower()
        if od != expect["order_dir"]:
            fails.append(f"order_dir={od!r} != {expect['order_dir']!r}")
    if "limit" in expect and plan.limit != expect["limit"]:
        fails.append(f"limit={plan.limit} != {expect['limit']}")
    if "needs_clarify" in expect and bool(plan.needs_clarify) != bool(expect["needs_clarify"]):
        fails.append(f"needs_clarify={plan.needs_clarify} != {expect['needs_clarify']}")
    for frag in expect.get("sql_contains", []):
        if frag not in sql:
            fails.append(f"sql missing {frag!r}")
    for frag in expect.get("sql_not_contains", []):
        if frag in sql:
            fails.append(f"sql must NOT contain {frag!r}")
    return fails


def _evaluate_all(pipe):
    plan_cache: dict = {}
    results = []
    for case in BENCH_CASES:
        try:
            plan, sql = _run_case(pipe, case, plan_cache)
            if case.get("id"):
                plan_cache[case["id"]] = plan
            fails = _check_expect(plan, sql, case.get("expect") or {})
            results.append({
                "id": case.get("id"), "category": case.get("category"),
                "question": case["question"], "passed": not fails, "fails": fails,
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "id": case.get("id"), "category": case.get("category"),
                "question": case["question"], "passed": False, "fails": [f"EXCEPTION: {exc}"],
            })
    return results


def test_accuracy_benchmark(bench_env):
    results = _evaluate_all(bench_env)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    accuracy = passed / total if total else 0.0

    # 落机器可读结果 + 汇总，供修复报告引用。
    out_dir = BACKEND.parent / "output" / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total": total, "passed": passed, "failed": failed,
        "accuracy": round(accuracy, 4),
        "by_category": {},
        "failed_cases": [r for r in results if not r["passed"]],
    }
    for r in results:
        c = r["category"] or "uncat"
        slot = summary["by_category"].setdefault(c, {"total": 0, "passed": 0})
        slot["total"] += 1
        slot["passed"] += 1 if r["passed"] else 0
    (out_dir / "accuracy_benchmark_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== Accuracy Benchmark === total={total} passed={passed} "
          f"failed={failed} accuracy={accuracy:.1%}")
    for c, s in sorted(summary["by_category"].items()):
        print(f"  {c:<22} {s['passed']}/{s['total']}")
    if failed:
        print("--- FAILED ---")
        for r in results:
            if not r["passed"]:
                print(f"  [{r['category']}] {r['question'][:42]} -> {r['fails']}")

    assert total >= 100, f"benchmark must have >=100 cases, has {total}"
    assert accuracy >= 0.90, f"accuracy {accuracy:.1%} < 90% ({failed} failed)"


def test_p0_cases_all_pass(bench_env):
    """所有标记 p0=True 的审计核心用例必须 100% 通过。"""
    plan_cache: dict = {}
    p0_fail = []
    for case in BENCH_CASES:
        plan, sql = _run_case(bench_env, case, plan_cache)
        if case.get("id"):
            plan_cache[case["id"]] = plan
        if case.get("p0"):
            fails = _check_expect(plan, sql, case.get("expect") or {})
            if fails:
                p0_fail.append((case["question"], fails))
    assert not p0_fail, f"P0 cases failed: {p0_fail}"
