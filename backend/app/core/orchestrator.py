"""Pipeline orchestrator — the entry point for /api/chat.

Stages (mostly programmatic, single LLM stage for planning):

    1. cache lookup        (L1 question)
    2. retrieval            (hybrid embedding + BM25)
    3. plan                 (LLM JSON, with rule extraction)
    4. compile              (deterministic SQL build)
    5. guard                (AST-level read-only safety)
    6. execute              (MySQL adapter)
    7. answer               (LLM short executive narrative + table + chart)

SSE stream events: stage / progress / partial / answer / error
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Callable, Iterable

from app.core.answerer import Answerer
from app.core.cache import get_cache
from app.core.cache.redis_cache import _fingerprint
from app.core.config import V1Config, load_config
from app.core.exec import ExecError, get_executor
from app.core.guard import GuardError, SQLGuard
from app.core.llm.router import LLMError, get_llm_router
from app.core.nl2sql import PlanCompiler, Planner, QueryPlan
from app.core.retrieval import HybridRetriever
from app.core.semantic import SemanticLayer

logger = logging.getLogger("datachat.orchestrator")


@dataclass
class TraceEvent:
    stage: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "payload": self.payload,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": self.timestamp or datetime.utcnow().isoformat() + "Z",
        }


@dataclass
class PipelineResult:
    trace_id: str
    question: str
    answer: dict[str, Any]
    plan: dict[str, Any]
    sql: str
    rows: int
    elapsed_ms: int
    cached: bool
    events: list[dict[str, Any]] = field(default_factory=list)
    # 失败语义：编译/Guard/权限/执行失败时 ok=False，调用方据此返回 friendly_error，
    # 绝不把失败当正常答案展示。默认 True → happy path / cache / clarify 不受影响。
    ok: bool = True
    error_code: str = ""


class Pipeline:
    def __init__(self, *, semantic_path: str | None = None, cfg: V1Config | None = None):
        self.cfg = cfg or load_config()
        path = semantic_path or str(self.cfg.app.semantic_path)
        self.semantic = SemanticLayer(path)
        self.llm = get_llm_router()
        self.retriever = HybridRetriever(self.semantic, self.llm)
        self.planner = Planner(self.semantic, self.retriever, self.llm)
        self.compiler = PlanCompiler(self.semantic, default_limit=self.cfg.guard.max_rows)
        self.guard = SQLGuard(allowed_tables=self.semantic.tables.keys(), cfg=self.cfg.guard)
        self.executor = get_executor()
        self.answerer = Answerer(self.semantic, self.llm)
        self.cache = get_cache()

    def warmup(self) -> dict[str, Any]:
        try:
            self.retriever.build()
            return {"ok": True, "metrics": len(self.semantic.metrics), "dimensions": len(self.semantic.dimensions), "tables": len(self.semantic.tables)}
        except Exception as exc:
            logger.exception("warmup failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    # ----------------------------------------------------------- run

    def run(
        self,
        question: str,
        *,
        user_id: str = "default",
        is_admin: bool = False,
        history: list[dict[str, str]] | None = None,
        previous_plan: QueryPlan | None = None,
        on_event: Callable[[TraceEvent], None] | None = None,
        skip_llm_narrative: bool = False,
        force_refresh: bool = False,
    ) -> PipelineResult:
        trace_id = uuid.uuid4().hex
        events: list[TraceEvent] = []

        def emit(stage: str, status: str, payload: dict[str, Any] | None = None, elapsed_ms: int = 0) -> None:
            evt = TraceEvent(stage=stage, status=status, payload=payload or {}, elapsed_ms=elapsed_ms, timestamp=datetime.utcnow().isoformat() + "Z")
            events.append(evt)
            if on_event:
                try:
                    on_event(evt)
                except Exception as exc:
                    logger.warning("event sink failed: %s", exc)

        run_started = time.perf_counter()

        # Stage 0: question normalize (cheap)
        question_clean = (question or "").strip()
        if not question_clean:
            emit("input", "error", {"reason": "empty"}, 0)
            return PipelineResult(trace_id=trace_id, question="", answer={"narrative": "请输入问题。"}, plan={}, sql="", rows=0, elapsed_ms=0, cached=False, events=[e.to_dict() for e in events])

        # Stage 1: L1 cache  —  (question, user_id, ctx_fp) 精确匹配
        ctx_fp = ""
        if previous_plan and previous_plan.metric:
            ctx_fp = previous_plan.signature()
        cached_payload = None
        if not force_refresh:
            cached_payload = self.cache.get_question(question_clean, user_id, ctx_fp)
        if cached_payload:
            emit("cache", "hit", {"layer": "L1"}, 0)
            elapsed = int((time.perf_counter() - run_started) * 1000)
            cached_payload["__cached"] = True
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer=cached_payload.get("answer") or cached_payload,
                plan=cached_payload.get("plan") or {},
                sql=cached_payload.get("sql") or "",
                rows=int(cached_payload.get("rows") or 0),
                elapsed_ms=elapsed,
                cached=True,
                events=[e.to_dict() for e in events],
            )
        emit("cache", "miss", {"layer": "L1"}, 0)

        # Stage 1b: question→plan_sig 索引（跨会话/跨上下文加速）
        #
        # L1 必须把 ctx_fp 算进 key（多轮上下文里同一句话意图不同），导致"换个会话又问一遍同一题"
        # 永远 miss。这里加一道弱关联索引：用 (question, user_id) 反查上次问出来的 plan_sig，
        # 拿到 plan_sig 后到 L2 plan-keyed cache 取完整 answer。命中即跳过 planner LLM (~58s)
        # + answerer LLM (~120s)，整条请求 < 200ms 返回。
        # 上下文真变了？没事——这里只是预判，下面 planner 跑完后会再校验一次 plan_sig，
        # 校验不过就放弃这条加速、走完整流程。
        q2p_key = self.cache._k("q2p", _fingerprint(question_clean, user_id)) if hasattr(self.cache, "_k") else None
        prefetched_plan_sig = None
        if q2p_key and not force_refresh:
            try:
                prefetched_plan_sig = self.cache.get(q2p_key)
            except Exception:
                prefetched_plan_sig = None
            if prefetched_plan_sig:
                plan_cached = None
                try:
                    plan_cached = self.cache.get_plan(prefetched_plan_sig)
                except Exception:
                    plan_cached = None
                if plan_cached:
                    emit("cache", "hit", {"layer": "L2 (q2p)", "plan_sig": prefetched_plan_sig[:12]}, 0)
                    elapsed = int((time.perf_counter() - run_started) * 1000)
                    return PipelineResult(
                        trace_id=trace_id,
                        question=question_clean,
                        answer=plan_cached.get("answer") or {},
                        plan=plan_cached.get("plan") or {},
                        sql=plan_cached.get("sql") or "",
                        rows=int(plan_cached.get("rows") or 0),
                        elapsed_ms=elapsed,
                        cached=True,
                        events=[e.to_dict() for e in events],
                    )

        # Stage 1.5: 复杂多表分析 / 用户要求"直接返回 SQL" → 走 direct-SQL 路径
        try:
            from app.core.direct_sql import should_use_direct_sql
            if should_use_direct_sql(question_clean):
                emit("route", "direct_sql", {"reason": "complex_or_explicit_sql_request"}, 0)
                return self._run_direct_sql(
                    question_clean,
                    user_id=user_id, is_admin=is_admin,
                    run_started=run_started, trace_id=trace_id, events=events, emit=emit,
                    history=history, previous_plan=previous_plan,
                )
        except Exception as exc:
            logger.warning("direct_sql route check failed: %s — falling back to planner", exc)

        # Stage 2 + 3: retrieval + plan (planner internally calls retriever)
        plan_started = time.perf_counter()
        try:
            plan_result = self.planner.plan(question_clean, history=history, previous_plan=previous_plan)
        except LLMError as exc:
            emit("plan", "error", {"reason": str(exc)}, int((time.perf_counter() - plan_started) * 1000))
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer={"narrative": f"模型调用失败：{exc}"},
                plan={},
                sql="",
                rows=0,
                elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                cached=False,
                events=[e.to_dict() for e in events],
                ok=False,
                error_code="CHAT_FAILED",
            )
        emit("retrieval", "ok", {
            "metrics": [c.name for c in plan_result.bundle.metrics[:3]],
            "dimensions": [c.name for c in plan_result.bundle.dimensions[:3]],
            "tables": [c.name for c in plan_result.bundle.tables[:3]],
        }, plan_result.bundle.elapsed_ms)
        # plan_result.elapsed_ms 是 Planner.build() 总时长（含语义召回），retrieval 已单独
        # 上报；这里只汇报 plan 净时长（≈ LLM 规划 + 规则修复），避免和 retrieval 重复计时。
        plan_only_ms = max(0, plan_result.elapsed_ms - plan_result.bundle.elapsed_ms)
        emit("plan", "ok", {
            "metric": plan_result.plan.metric,
            "calculation": plan_result.plan.calculation,
            "needs_clarify": plan_result.plan.needs_clarify,
            "confidence": plan_result.plan.confidence,
            "llm_wait_ms": plan_only_ms,
        }, plan_only_ms)

        plan = plan_result.plan

        # Stage 3.2: L2 plan-keyed cache 二次检查
        # 即使 q2p 没命中（首次新问题 / 索引失效 / 上下文真的变了），只要 planner 这次产出的
        # plan 在以前的请求里出现过（任何用户 / 任何会话）→ 直接复用历史完整答案。
        # 一次 planner LLM 的代价已经付了，但能跳过 compile/execute/answerer LLM（120s+）。
        plan_sig_now = plan.signature() if plan.metric else ""
        if plan_sig_now and not force_refresh:
            plan_cached = None
            try:
                plan_cached = self.cache.get_plan(plan_sig_now)
            except Exception:
                plan_cached = None
            if plan_cached:
                emit("cache", "hit", {"layer": "L2 (plan)", "plan_sig": plan_sig_now[:12]}, 0)
                # 写回 q2p 索引（即使首次 miss，这次帮下次秒返）
                if q2p_key:
                    try:
                        self.cache.set(q2p_key, plan_sig_now, ttl=self.cache.cfg.ttl_question if hasattr(self.cache, "cfg") else 3600)
                    except Exception:
                        pass
                elapsed = int((time.perf_counter() - run_started) * 1000)
                return PipelineResult(
                    trace_id=trace_id,
                    question=question_clean,
                    answer=plan_cached.get("answer") or {},
                    plan=plan_cached.get("plan") or plan.to_dict(),
                    sql=plan_cached.get("sql") or "",
                    rows=int(plan_cached.get("rows") or 0),
                    elapsed_ms=elapsed,
                    cached=True,
                    events=[e.to_dict() for e in events],
                )

        # Stage 3.5: clarify shortcut
        if plan.needs_clarify:
            answer = self.answerer.build(question_clean, plan, {"columns": {}}, None, "", skip_llm=True)
            emit("clarify", "ok", {"reason": plan.clarify_reason})
            elapsed = int((time.perf_counter() - run_started) * 1000)
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer=answer,
                plan=plan.to_dict(),
                sql="",
                rows=0,
                elapsed_ms=elapsed,
                cached=False,
                events=[e.to_dict() for e in events],
            )

        # Stage 3.6: data permission — 行级注入 + 表级校验
        try:
            from app.core.permissions import apply_to_plan as _apply_perms, PermissionDenied
            plan = _apply_perms(plan, user_id=user_id, is_admin=is_admin)
            emit("permissions", "ok", {"filters_after": [f.dimension for f in plan.filters]}, 0)
        except PermissionDenied as exc:
            emit("permissions", "denied", {"reason": str(exc)}, 0)
            return PipelineResult(
                trace_id=trace_id, question=question_clean,
                answer={"narrative": "权限不足，请联系管理员开通相关数据权限。",
                        "highlights": [], "risk_notes": [], "suggestions": [],
                        "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                        "chart": {"type": "none"},
                        "explainability": {"reason": str(exc)}},
                plan=plan.to_dict() if hasattr(plan, "to_dict") else {},
                sql="", rows=0,
                elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                cached=False, events=[e.to_dict() for e in events],
                ok=False, error_code="PERMISSION_DENIED",
            )
        except Exception as exc:
            logger.warning("permissions inject failed: %s", exc)

        # Stage 4: compile
        compile_started = time.perf_counter()
        try:
            raw_sql, meta = self.compiler.compile(plan)
        except Exception as exc:
            emit("compile", "error", {"reason": str(exc)}, int((time.perf_counter() - compile_started) * 1000))
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer={"narrative": f"SQL 编译失败：{exc}"},
                plan=plan.to_dict(),
                sql="",
                rows=0,
                elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                cached=False,
                events=[e.to_dict() for e in events],
                ok=False,
                error_code="CHAT_FAILED",
            )
        emit("compile", "ok", {"sql_preview": raw_sql[:200]}, int((time.perf_counter() - compile_started) * 1000))

        # Stage 5: guard
        try:
            report = self.guard.validate(raw_sql)
        except GuardError as exc:
            emit("guard", "error", {"reason": str(exc)}, 0)
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer={"narrative": f"SQL 安全检查未通过：{exc}"},
                plan=plan.to_dict(),
                sql=raw_sql,
                rows=0,
                elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                cached=False,
                events=[e.to_dict() for e in events],
                ok=False,
                error_code="CHAT_FAILED",
            )
        guarded_sql = report.sanitized_sql
        emit("guard", "ok", {"complexity": report.estimated_complexity, "tables": report.tables}, 0)

        # 字段级权限二次校验
        try:
            from app.core.permissions import validate_sql_columns, inject_row_filters_into_sql, PermissionDenied
            validate_sql_columns(guarded_sql, user_id=user_id, is_admin=is_admin, semantic_layer=self.semantic)
            # 行级权限再注入一层 — 审计 P0：即使结构化路径，也要在执行前用 SQL guard 再校验一次
            guarded_sql = inject_row_filters_into_sql(
                guarded_sql, user_id=user_id, is_admin=is_admin, semantic_layer=self.semantic,
            )
        except PermissionDenied as exc:
            emit("permissions", "denied_column", {"reason": str(exc)}, 0)
            return PipelineResult(
                trace_id=trace_id, question=question_clean,
                answer={"narrative": "权限不足，请联系管理员开通相关数据权限。",
                        "highlights": [], "risk_notes": [], "suggestions": [],
                        "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                        "chart": {"type": "none"},
                        "explainability": {"reason": str(exc)}},
                plan=plan.to_dict(),
                sql="", rows=0,
                elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                cached=False, events=[e.to_dict() for e in events],
                ok=False, error_code="PERMISSION_DENIED",
            )

        # Stage 6: cache L3 sql
        sql_cached = self.cache.get_sql_result(guarded_sql)
        exec_obj = None
        if sql_cached and not force_refresh:
            from app.core.exec import ExecResult
            exec_obj = ExecResult(
                columns=list(sql_cached.get("columns") or []),
                rows=list(sql_cached.get("rows") or []),
                row_count=int(sql_cached.get("row_count") or 0),
                elapsed_ms=int(sql_cached.get("elapsed_ms") or 0),
                sql=guarded_sql,
            )
            emit("execute", "cache_hit", {"layer": "L3", "rows": exec_obj.row_count}, 0)
        else:
            exec_started = time.perf_counter()
            try:
                exec_obj = self.executor.run_select(guarded_sql, max_rows=self.cfg.guard.max_rows, timeout_ms=self.cfg.guard.statement_timeout_ms)
            except ExecError as exc:
                emit("execute", "error", {"reason": str(exc)}, int((time.perf_counter() - exec_started) * 1000))
                return PipelineResult(
                    trace_id=trace_id,
                    question=question_clean,
                    answer={"narrative": f"SQL 执行失败：{exc}", "explainability": {"sql": guarded_sql}},
                    plan=plan.to_dict(),
                    sql=guarded_sql,
                    rows=0,
                    elapsed_ms=int((time.perf_counter() - run_started) * 1000),
                    cached=False,
                    events=[e.to_dict() for e in events],
                    ok=False,
                    error_code="CHAT_FAILED",
                )
            emit("execute", "ok", {"rows": exec_obj.row_count, "elapsed_ms": exec_obj.elapsed_ms}, exec_obj.elapsed_ms)
            try:
                self.cache.set_sql_result(guarded_sql, {
                    "columns": exec_obj.columns,
                    "rows": exec_obj.rows,
                    "row_count": exec_obj.row_count,
                    "elapsed_ms": exec_obj.elapsed_ms,
                })
            except Exception:
                pass

        # Stage 7: answer
        answer_started = time.perf_counter()
        answer_payload = self.answerer.build(question_clean, plan, meta, exec_obj, guarded_sql, skip_llm=skip_llm_narrative)
        answer_ms = int((time.perf_counter() - answer_started) * 1000)
        emit("answer", "ok", {
            "chart": (answer_payload.get("chart") or {}).get("type"),
            "llm_wait_ms": 0 if skip_llm_narrative else answer_ms,
        }, answer_ms)

        elapsed = int((time.perf_counter() - run_started) * 1000)
        # cache L1 question (精确匹配，含 ctx_fp)
        cache_payload = {
            "answer": answer_payload, "plan": plan.to_dict(),
            "sql": guarded_sql, "rows": exec_obj.row_count if exec_obj else 0,
        }
        try:
            self.cache.set_question(question_clean, user_id, ctx_fp, cache_payload)
        except Exception:
            pass
        # cache L2 plan-keyed answer + q2p 索引（让下次跨会话/跨上下文也能命中）
        try:
            if plan_sig_now:
                self.cache.set_plan(plan_sig_now, cache_payload)
                if q2p_key:
                    self.cache.set(q2p_key, plan_sig_now, ttl=self.cache.cfg.ttl_question if hasattr(self.cache, "cfg") else 3600)
        except Exception:
            pass

        return PipelineResult(
            trace_id=trace_id,
            question=question_clean,
            answer=answer_payload,
            plan=plan.to_dict(),
            sql=guarded_sql,
            rows=exec_obj.row_count if exec_obj else 0,
            elapsed_ms=elapsed,
            cached=False,
            events=[e.to_dict() for e in events],
        )


    # =========================================================== direct-SQL path

    def _run_direct_sql(self, question: str, *, user_id: str, is_admin: bool,
                        run_started: float, trace_id: str, events: list, emit,
                        history: list[dict[str, str]] | None = None,
                        previous_plan: QueryPlan | None = None) -> PipelineResult:
        """Direct-SQL：LLM 直接生成 SQL → guard → 权限注入 → 执行 → 总结。"""
        from app.core.direct_sql import generate_direct_sql, summarize_direct_result
        from app.core.guard import GuardError
        from app.core.permissions import (
            PermissionDenied, inject_row_filters_into_sql, validate_sql_columns,
        )

        # 1) 生成 SQL
        gen_started = time.perf_counter()
        try:
            sql = generate_direct_sql(
                question, semantic_layer=self.semantic, llm=self.llm,
                history=history, previous_plan=previous_plan.to_dict() if previous_plan else None,
            )
        except Exception as exc:
            emit("direct_sql", "llm_error", {"reason": str(exc)[:200]}, int((time.perf_counter() - gen_started) * 1000))
            return self._failure_result(
                question, trace_id, run_started, events,
                "生成 SQL 失败，请稍后再试或换一种问法。",
            )
        if not sql:
            return self._failure_result(question, trace_id, run_started, events,
                                        "未生成有效 SQL，请稍后再试或换一种问法。")
        emit("direct_sql", "generated", {"sql_preview": sql[:200]}, int((time.perf_counter() - gen_started) * 1000))

        # 2) AST guard（表白名单 + 只 SELECT + 自动 LIMIT）
        try:
            report = self.guard.validate(sql)
            guarded_sql = report.sanitized_sql
        except GuardError as exc:
            emit("direct_sql", "guard_blocked", {"reason": str(exc)}, 0)
            return self._failure_result(question, trace_id, run_started, events,
                                        "生成的 SQL 未通过安全审查，请稍后再试或换一种问法。")

        # 3) 字段级权限
        try:
            validate_sql_columns(guarded_sql, user_id=user_id, is_admin=is_admin, semantic_layer=self.semantic)
        except PermissionDenied as exc:
            emit("direct_sql", "perm_column_denied", {"reason": str(exc)}, 0)
            return self._failure_result(question, trace_id, run_started, events,
                                        "权限不足，请联系管理员开通相关数据权限。")

        # 4) 行级权限注入（强制）
        try:
            guarded_sql = inject_row_filters_into_sql(
                guarded_sql, user_id=user_id, is_admin=is_admin, semantic_layer=self.semantic,
            )
        except PermissionDenied as exc:
            emit("direct_sql", "perm_row_denied", {"reason": str(exc)}, 0)
            return self._failure_result(question, trace_id, run_started, events,
                                        "权限不足，请联系管理员开通相关数据权限。")

        # 5) 执行
        exec_started = time.perf_counter()
        try:
            exec_obj = self.executor.run_select(
                guarded_sql,
                max_rows=self.cfg.guard.max_rows,
                timeout_ms=self.cfg.guard.statement_timeout_ms,
            )
        except Exception as exc:
            err = str(exc)[:300]
            emit("direct_sql", "exec_error", {"reason": err, "sql": guarded_sql[:400]},
                 int((time.perf_counter() - exec_started) * 1000))
            logger.warning("[trace=%s] direct_sql exec failed: %s | SQL=%s", trace_id, err, guarded_sql[:400])
            # 给用户的提示包含 trace_id 便于排查；后端日志有完整 SQL
            return self._failure_result(
                question, trace_id, run_started, events,
                f"查询执行失败：{_summary_db_error(err)}。如需查看可执行 SQL，请联系管理员（trace_id={trace_id[:8]}）。",
                debug_sql=guarded_sql,
            )
        emit("execute", "ok", {"rows": exec_obj.row_count, "elapsed_ms": exec_obj.elapsed_ms}, exec_obj.elapsed_ms)

        # 6) 总结
        try:
            narrative, highlights = summarize_direct_result(
                question, guarded_sql, exec_obj.columns, exec_obj.rows, llm=self.llm,
            )
        except Exception:
            narrative, highlights = f"查询返回 {exec_obj.row_count} 行。", []

        # 7) 组 display_rows
        display_rows = [[str(v) if v is not None else "—" for v in row] for row in exec_obj.rows]
        display_cols = [{"key": c, "label": c, "kind": "value", "unit": "", "format": "", "decimals": 2}
                        for c in exec_obj.columns]
        answer = {
            "narrative": narrative,
            "highlights": highlights,
            "risk_notes": [],
            "table": {
                "columns": exec_obj.columns,
                "rows": exec_obj.rows,
                "display_columns": display_cols,
                "display_rows": display_rows,
                "row_count": exec_obj.row_count,
                "elapsed_ms": exec_obj.elapsed_ms,
            },
            "chart": {"type": "none"},
            "suggestions": [],
            "explainability": {
                "sql": guarded_sql,
                "mode": "direct_sql",
                "row_count": exec_obj.row_count,
                "elapsed_ms": exec_obj.elapsed_ms,
            },
        }
        elapsed = int((time.perf_counter() - run_started) * 1000)
        return PipelineResult(
            trace_id=trace_id, question=question,
            answer=answer, plan={"mode": "direct_sql"},
            sql=guarded_sql, rows=exec_obj.row_count,
            elapsed_ms=elapsed, cached=False,
            events=[e.to_dict() for e in events],
        )

    def _failure_result(self, question: str, trace_id: str, run_started: float, events: list, msg: str, debug_sql: str = "") -> PipelineResult:
        elapsed = int((time.perf_counter() - run_started) * 1000)
        return PipelineResult(
            trace_id=trace_id, question=question,
            answer={"narrative": msg, "highlights": [], "risk_notes": [],
                    "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                    "chart": {"type": "none"}, "suggestions": [],
                    "explainability": {"reason": msg, "sql": debug_sql}},
            plan={}, sql=debug_sql, rows=0, elapsed_ms=elapsed, cached=False,
            events=[e.to_dict() for e in events],
            ok=False, error_code="CHAT_FAILED",
        )


def _summary_db_error(err: str) -> str:
    """把底层 SQL 异常提炼成用户可懂的简短描述。"""
    e = (err or "").lower()
    if "unknown column" in e:
        return "字段名不存在或表关联错误"
    if "table" in e and "doesn't exist" in e:
        return "表不存在"
    if "syntax" in e:
        return "SQL 语法错误"
    if "timeout" in e or "max_execution_time" in e:
        return "查询超时，请缩小数据范围"
    return "数据库执行出错"


_pipeline_singleton: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _pipeline_singleton = Pipeline()
        _pipeline_singleton.warmup()
    return _pipeline_singleton


# ---------------------------------------------------------- SSE helpers

def to_sse_event(event: TraceEvent) -> str:
    body = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"event: stage\ndata: {body}\n\n"


def to_sse_done(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: done\ndata: {body}\n\n"


def to_sse_error(message: str) -> str:
    body = json.dumps({"error": message}, ensure_ascii=False)
    return f"event: error\ndata: {body}\n\n"
