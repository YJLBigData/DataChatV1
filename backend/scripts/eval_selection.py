"""选表/选指标准确率回归评测（P2）。

背景：本项目"不准"的头号来源是选错表/选错指标（schema linking）。这个脚本把它
变成可测量、可回归的两个数字——**选表准确率 / 选指标准确率**。每次改动
prompt / 语义层 / 检索 / 换模型后跑一遍，防止"修一个坏三个"。

用法（在 backend/ 目录下）：

  # ① 零成本起步：从 semantic.yaml 的 few_shots 生成种子评测集
  python -m scripts.eval_selection seed

  # ② 从 query_log 导出近期真实问题 → 待标注模板（预填线上预测，人工只改错的）
  python -m scripts.eval_selection export --limit 100

  # ③ 跑评测
  #    retrieval 模式：只测召回 top1/top3（不调 LLM，秒级，适合每次提交跑）
  #    planner   模式：完整 planner 链路（调 LLM，温度 0，适合发版前跑）
  python -m scripts.eval_selection run --mode retrieval
  python -m scripts.eval_selection run --mode planner --min-table-acc 0.85 --min-metric-acc 0.75

评测集格式 backend/eval/selection_eval.yaml：
  cases:
    - question: 上月各大区终端动销量
      expected_metric: terminal_sale_qty_total     # 语义层 metric key
      expected_table: ""                           # 可省略，默认取 expected_metric 所属表
      allowed_tables: []                           # 可选：模拟该用户的表范围（分域）
      user_id: ""                                  # 可选：直接按该用户的真实权限分域
      note: ""

退出码：传了 --min-*-acc 且未达标 → 1（可接 CI / 部署前置检查）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EVAL_DIR = BACKEND / "eval"
EVAL_FILE = EVAL_DIR / "selection_eval.yaml"
REPORT_DIR = EVAL_DIR / "reports"


def _load_stack():
    """加载语义层 + 检索器（优先持久化索引；无向量时自动退化 BM25-only，仍可评）。"""
    from app.core.config import load_config
    from app.core.semantic import SemanticLayer
    from app.core.retrieval import HybridRetriever
    from app.core.llm.router import get_llm_router

    cfg = load_config()
    semantic = SemanticLayer(cfg.app.semantic_path)
    retriever = HybridRetriever(semantic, get_llm_router())
    retriever.build()
    return cfg, semantic, retriever


def _scope_for_case(case: dict[str, Any], semantic) -> Any:
    """按 case 构造数据域：allowed_tables 显式给 > user_id 查权限库 > 不分域。"""
    from app.core.permissions import UserScope, get_user_scope
    allowed = [str(t) for t in (case.get("allowed_tables") or []) if t]
    if allowed:
        known = set(semantic.tables.keys())
        tables = frozenset(t for t in allowed if t in known)
        return UserScope(user_id="eval", allowed_tables=tables, fingerprint="eval")
    uid = str(case.get("user_id") or "")
    if uid:
        return get_user_scope(uid, is_admin=False, semantic_layer=semantic)
    return None


# ============================================================ seed / export

def cmd_seed(_: argparse.Namespace) -> int:
    """semantic.yaml few_shots → 种子评测集（intent 里就有正确答案，零标注成本）。"""
    _, semantic, _r = _load_stack()
    cases: list[dict[str, Any]] = []
    for fs in semantic.few_shots:
        metric = str((fs.intent or {}).get("metric") or "")
        md = semantic.metric(metric)
        if not metric or not md:
            continue
        cases.append({
            "question": fs.question,
            "expected_metric": metric,
            "expected_table": str((fs.intent or {}).get("table") or md.table),
            "allowed_tables": [],
            "user_id": "",
            "note": "seed:semantic.few_shots",
        })
    if not cases:
        print("semantic.yaml 没有可用 few_shots，未生成。")
        return 1
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if EVAL_FILE.exists():
        existing = yaml.safe_load(EVAL_FILE.read_text(encoding="utf-8")) or {}
        known_q = {c.get("question") for c in (existing.get("cases") or [])}
        merged = list(existing.get("cases") or []) + [c for c in cases if c["question"] not in known_q]
        cases = merged
    EVAL_FILE.write_text(
        yaml.safe_dump({"cases": cases}, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(f"评测集已写入 {EVAL_FILE}（{len(cases)} 条）")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """query_log 近期真实问题 → 待标注模板。预填线上预测值：标注者只需改错的。"""
    from app.core.query_log import get_query_log_store
    _, semantic, _r = _load_stack()
    items, _total = get_query_log_store().list(limit=int(args.limit), status="ok")
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for it in items:
        q = " ".join(str(it.get("question") or "").split())
        metric = str(it.get("metric") or "")
        if not q or not metric or q in seen or not semantic.metric(metric):
            continue
        seen.add(q)
        cases.append({
            "question": q,
            "expected_metric": metric,                       # ← 预填线上预测，错了请人工改
            "expected_table": str(it.get("table") or ""),
            "allowed_tables": [],
            "user_id": str(it.get("user_id") or ""),
            "note": f"export:trace={str(it.get('trace_id') or '')[:8]} 需人工复核",
        })
    if not cases:
        print("query_log 暂无可导出的成功问数记录。")
        return 1
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    out = EVAL_DIR / f"selection_labeling_{time.strftime('%Y%m%d_%H%M%S')}.yaml"
    out.write_text(
        yaml.safe_dump({"cases": cases}, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    print(f"待标注模板已写入 {out}（{len(cases)} 条）。人工复核后合并进 {EVAL_FILE.name}。")
    return 0


# ============================================================ run

def cmd_run(args: argparse.Namespace) -> int:
    eval_path = Path(args.file) if args.file else EVAL_FILE
    if not eval_path.exists():
        print(f"评测集不存在：{eval_path}。先运行 seed / export 生成。")
        return 1
    data = yaml.safe_load(eval_path.read_text(encoding="utf-8")) or {}
    cases = [c for c in (data.get("cases") or []) if c.get("question") and c.get("expected_metric")]
    if not cases:
        print("评测集为空。")
        return 1

    _, semantic, retriever = _load_stack()
    mode = args.mode
    planner = None
    if mode == "planner":
        from app.core.nl2sql.planner import Planner
        from app.core.llm.router import get_llm_router
        planner = Planner(semantic, retriever, get_llm_router())

    rows: list[dict[str, Any]] = []
    n_table_ok = n_metric_ok = n_metric_top3 = 0
    n_clarify = n_oos = 0
    answered = 0

    for case in cases:
        q = str(case["question"])
        exp_metric = str(case["expected_metric"])
        exp_md = semantic.metric(exp_metric)
        exp_table = str(case.get("expected_table") or (exp_md.table if exp_md else ""))
        scope = _scope_for_case(case, semantic)
        allowed = scope.allowed_tables if (scope is not None and getattr(scope, "restricted", False)) else None

        pred_metric = pred_table = ""
        outcome = "answered"
        top3: list[str] = []

        if mode == "retrieval":
            bundle = retriever.search(q, allowed_tables=allowed)
            top3 = [c.name for c in bundle.metrics[:3]]
            if bundle.metrics:
                pred_metric = bundle.metrics[0].name
                pmd = semantic.metric(pred_metric)
                pred_table = pmd.table if pmd else ""
        else:
            result = planner.plan(q, scope=scope)  # type: ignore[union-attr]
            plan = result.plan
            top3 = [c.name for c in result.bundle.metrics[:3]]
            if plan.out_of_scope:
                outcome = "out_of_scope"
                n_oos += 1
            elif plan.needs_clarify:
                outcome = "clarify"
                n_clarify += 1
            else:
                pred_metric, pred_table = plan.metric, plan.table

        metric_ok = table_ok = False
        if outcome == "answered":
            answered += 1
            metric_ok = (pred_metric == exp_metric)
            table_ok = bool(exp_table) and (pred_table == exp_table)
            n_metric_ok += int(metric_ok)
            n_table_ok += int(table_ok)
            n_metric_top3 += int(exp_metric in top3)
        rows.append({
            "question": q, "outcome": outcome,
            "expected_metric": exp_metric, "pred_metric": pred_metric, "metric_ok": metric_ok,
            "expected_table": exp_table, "pred_table": pred_table, "table_ok": table_ok,
            "metric_top3": exp_metric in top3,
        })

    total = len(cases)
    table_acc = n_table_ok / answered if answered else 0.0
    metric_acc = n_metric_ok / answered if answered else 0.0
    top3_acc = n_metric_top3 / answered if answered else 0.0

    print(f"\n===== 选表/选指标评测（mode={mode}，{total} 条）=====")
    for r in rows:
        flag = "✓" if (r["metric_ok"] and r["table_ok"]) else ("…" if r["outcome"] != "answered" else "✗")
        print(f" {flag} [{r['outcome']:^12}] {r['question'][:36]:<38} "
              f"metric: {r['pred_metric'] or '—'} (期望 {r['expected_metric']}) "
              f"table: {r['pred_table'] or '—'}")
    print("-" * 72)
    print(f" 已作答 {answered}/{total}  澄清 {n_clarify}  超范围 {n_oos}")
    print(f" 选表准确率   table_acc  = {table_acc:.1%}")
    print(f" 选指标准确率 metric_acc = {metric_acc:.1%}   (top3 召回 {top3_acc:.1%})")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"selection_{mode}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps({
        "mode": mode, "total": total, "answered": answered,
        "clarify": n_clarify, "out_of_scope": n_oos,
        "table_acc": round(table_acc, 4), "metric_acc": round(metric_acc, 4),
        "metric_top3_acc": round(top3_acc, 4),
        "cases": rows, "ts": time.time(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f" 报告已写入 {report_path}")

    failed = False
    if args.min_table_acc is not None and table_acc < float(args.min_table_acc):
        print(f" ❌ table_acc {table_acc:.1%} < 门槛 {float(args.min_table_acc):.1%}")
        failed = True
    if args.min_metric_acc is not None and metric_acc < float(args.min_metric_acc):
        print(f" ❌ metric_acc {metric_acc:.1%} < 门槛 {float(args.min_metric_acc):.1%}")
        failed = True
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description="选表/选指标准确率回归评测")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("seed", help="从 semantic.yaml few_shots 生成种子评测集")
    sp.set_defaults(func=cmd_seed)

    ep = sub.add_parser("export", help="从 query_log 导出待标注模板")
    ep.add_argument("--limit", type=int, default=100)
    ep.set_defaults(func=cmd_export)

    rp = sub.add_parser("run", help="跑评测")
    rp.add_argument("--mode", choices=("retrieval", "planner"), default="retrieval")
    rp.add_argument("--file", default="", help="评测集路径（默认 eval/selection_eval.yaml）")
    rp.add_argument("--min-table-acc", type=float, default=None)
    rp.add_argument("--min-metric-acc", type=float, default=None)
    rp.set_defaults(func=cmd_run)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
