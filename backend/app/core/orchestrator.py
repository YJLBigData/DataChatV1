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

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Callable, Iterable

from app.core.answerer import Answerer
from app.core.cache import get_cache
from app.core.cache.redis_cache import _fingerprint
from app.core.config import V1Config, load_config
from app.core.direct_sql_classify import _classify_direct_columns, _summary_db_error
from app.core.exec import ExecError, get_executor
from app.core.guard import GuardError, SQLGuard
from app.core.llm.router import LLMError, get_llm_router
from app.core.nl2sql import PlanCompiler, Planner, QueryPlan
from app.core.nl2sql.accuracy_critic import AccuracyCritic
from app.core.nl2sql.result_validator import ResultValidator
from app.core.retrieval import HybridRetriever
from app.core.semantic import SemanticLayer
# SSE 帧编码已拆到 app.core.sse；这里 re-export，保持 main.py 的历史 import 路径不变。
from app.core.sse import to_sse_done, to_sse_error, to_sse_event  # noqa: F401

logger = logging.getLogger("datachat.orchestrator")


class _DirectSQLFallback(Exception):
    """direct-SQL（模型直接写 SQL）阶段 LLM 不可用/失败时抛出，让 run() 回退到结构化 planner。

    planner 自带"LLM 失败→规则兜底"，所以即便用户勾了「不使用缓存/走模型」(force_refresh)，
    遇到网关瞬时抖动也能降级出结果，而不是硬失败成"问数失败"。仅 SQL 生成阶段触发；
    guard/权限/执行等真实失败仍按 _failure_result 收口（不回退、不掩盖）。"""


class PipelineCancelled(Exception):
    """客户端在 SSE 流式问数中途断开 → 在阶段边界检测到取消信号时抛出，让 run() 尽快收尾，
    不再继续后续昂贵的 LLM/DB 工作（P0 并发/取消修复：杜绝"断开后台还在烧算力"）。"""


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
        # 表级最新分区缓存（审计 P1 时间口径）：相对时间按真实分区解析，避免"本月"落到无数据月。
        self._latest_month_cache: dict[str, tuple[float, str | None]] = {}
        self._latest_month_lock = threading.Lock()
        try:
            self._latest_month_ttl = float(os.environ.get("DATACHAT_LATEST_MONTH_TTL", "300") or "300")
        except (TypeError, ValueError):
            self._latest_month_ttl = 300.0
        self.compiler = PlanCompiler(
            self.semantic, default_limit=self.cfg.guard.max_rows,
            latest_month_provider=self._table_latest_month,
        )
        self.guard = SQLGuard(
            allowed_tables=self.semantic.tables.keys(),
            cfg=self.cfg.guard,
            semantic_layer=self.semantic,  # 阶段3.1：允许 DATACHAT_ALLOW_MULTI_TABLE 开启时按 join 图校验
        )
        self.executor = get_executor()
        self.answerer = Answerer(self.semantic, self.llm)
        # 准确率护栏：编译前的结构化复核（Accuracy Critic）+ 执行后的结果校验（Result Validator）。
        # 二者均为确定性、无 LLM 默认开销，可独立测试；Critic 复用 planner 的等价指标解析。
        self.critic = AccuracyCritic(self.planner)
        self.validator = ResultValidator(self.semantic)
        self.cache = get_cache()

    def warmup(self) -> dict[str, Any]:
        try:
            self.retriever.build()
            return {"ok": True, "metrics": len(self.semantic.metrics), "dimensions": len(self.semantic.dimensions), "tables": len(self.semantic.tables)}
        except Exception as exc:
            logger.exception("warmup failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _table_latest_month(self, table_name: str) -> str | None:
        """查该物理表真实最新月份 YYYY-MM（带 TTL 缓存）。供 compiler 解析相对时间用。

        审计 P1：semantic.yaml 写死的 data_range.latest 可能领先真实分区（声称 2026-05，
        实际 2026-04），"本月"于是落到无数据月、静默返回 0 行。这里按表级真实分区纠偏；
        无 DB / 查询异常一律返回 None，由 compiler 安全回退到 semantic 口径（绝不抛错阻断主链路）。
        """
        tdef = self.semantic.table(table_name)
        if not tdef:
            return None
        now = time.time()
        with self._latest_month_lock:
            hit = self._latest_month_cache.get(table_name)
            if hit and (now - hit[0]) < self._latest_month_ttl:
                return hit[1]
        latest: str | None = None
        try:
            if tdef.is_year_month_split():
                ycol = tdef.time_field_year or "year"
                mcol = tdef.time_field_month or "month"
                sql = (f"SELECT CONCAT(`{ycol}`, '-', LPAD(`{mcol}`, 2, '0')) AS m "
                       f"FROM `{tdef.schema}`.`{tdef.name}` "
                       f"ORDER BY `{ycol}` DESC, `{mcol}` DESC LIMIT 1")
            elif tdef.time_field:
                sql = f"SELECT MAX(`{tdef.time_field}`) AS m FROM `{tdef.schema}`.`{tdef.name}`"
            else:
                return None
            res = self.executor.run_select(sql, max_rows=1, timeout_ms=5000)
            if res.rows and res.rows[0] and res.rows[0][0]:
                latest = str(res.rows[0][0])[:7]  # 取 YYYY-MM
        except Exception as exc:  # noqa: BLE001 — 取分区失败不阻断主链路
            logger.debug("table latest month probe failed for %s: %s", table_name, exc)
            latest = None
        with self._latest_month_lock:
            self._latest_month_cache[table_name] = (now, latest)
        return latest

    # ----------------------------------------------------------- run

    def run(
        self,
        question: str,
        *,
        user_id: str = "default",
        is_admin: bool = False,
        history: list[dict[str, str]] | None = None,
        previous_plan: QueryPlan | None = None,
        previous_rows: dict[str, Any] | None = None,
        on_event: Callable[[TraceEvent], None] | None = None,
        skip_llm_narrative: bool = False,
        force_refresh: bool = False,
        cancel_event: "threading.Event | None" = None,
    ) -> PipelineResult:
        trace_id = uuid.uuid4().hex
        events: list[TraceEvent] = []

        def _check_cancel(stage: str) -> None:
            """阶段边界检查取消信号：客户端断开后，绝不再进入下一段昂贵工作（LLM/DB）。"""
            if cancel_event is not None and cancel_event.is_set():
                emit("cancelled", "ok", {"at": stage}, 0)
                raise PipelineCancelled(stage)

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

        # 入口即检查取消：客户端在派发前就断开（含命中缓存的极快路径）也不开工。
        _check_cancel("start")

        # Stage 0.5: 用户数据域（检索分域 / guard 白名单 / 缓存 key 三处共用）。
        # 构建失败不阻断主链路：检索退回全量，强制拦截仍由 Stage 3.6 + guard fail-closed 兜底。
        from app.core.permissions import get_user_scope
        try:
            scope = get_user_scope(user_id, is_admin=is_admin, semantic_layer=self.semantic)
        except Exception as exc:
            logger.warning("user scope build failed (%s) — fallback to unscoped", exc)
            scope = None
        scope_fp = getattr(scope, "fingerprint", "all") if scope is not None else "all"

        # Stage 1: L1 cache  —  (question, user_id, ctx_fp) 精确匹配。
        # ctx_fp 拼入权限指纹：权限一变（行/表/列任何一项），该用户的问题级缓存立即失效，
        # 不会在 TTL 内继续吐出旧权限下算出的答案。
        ctx_fp = scope_fp
        if previous_plan and previous_plan.metric:
            ctx_fp = f"{scope_fp}|{previous_plan.signature()}"
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

        # Stage 1b: question→plan_sig 索引（跨会话加速，仅限"无上下文的独立问题"）
        #
        # L1 必须把 ctx_fp 算进 key（多轮上下文里同一句话意图不同），导致"换个会话又问一遍同一题"
        # 永远 miss。这里加一道弱关联索引：用 (question, user_id, scope) 反查上次问出来的 plan_sig，
        # 拿到 plan_sig 后到 L2 plan-keyed cache 取完整 answer，命中即 <200ms 返回。
        #
        # 【准确率护栏｜P0】q2p 的 key 里没有上下文（不含 previous_plan / history），所以它**只能**
        # 服务"全新会话里的首轮独立问题"。一旦本轮带上下文（追问，如"环比呢""拆到省区""只看东一区"），
        # 同一句问句在不同会话里意图完全不同，命中 q2p 会把别的会话的旧答案串过来——这正是
        # "变快了但准确率变低了"的根因。带上下文时一律跳过 q2p，让 planner 跑完整流程；
        # 真正等价的重复仍由下面 Stage 3.7 的 L2(plan) 缓存（planner 之后按真实 plan 签名）兜底加速。
        # key 含权限指纹：权限变更后旧 q2p 索引整体失效（防止越过 planner 拿到旧权限答案）。
        # 带上下文时把 q2p_key 置空 → 本阶段的读 + 后续 Stage 3.7/末尾的两处写全部自动跳过，
        # 既不串读旧答案，也不拿"追问语义"污染索引（保证索引里只留无上下文的独立问句）。
        has_turn_context = bool((previous_plan and previous_plan.metric) or history)
        q2p_key = (
            self.cache._k("q2p", _fingerprint(question_clean, user_id, scope_fp))
            if (hasattr(self.cache, "_k") and not has_turn_context) else None
        )
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

        # 取消检查（昂贵的 LLM 规划/直写 SQL 之前）：客户端已断开就别再开工。
        _check_cancel("plan")

        # Stage 1.5: 路由到 direct-SQL（让模型直接生成 SQL 出结果）。触发条件：
        #   · 用户勾选「不使用缓存（每次都重新计算）」force_refresh —— 明确要"走模型"，
        #     不要结构化 planner 那套受限 JSON；直接让大模型按完整问题写 SQL；
        #   · 或问题本身复杂/多表/显式要 SQL（should_use_direct_sql）。
        try:
            from app.core.direct_sql import should_use_direct_sql
            if force_refresh or should_use_direct_sql(question_clean):
                reason = "force_refresh_model_direct" if force_refresh else "complex_or_explicit_sql_request"
                emit("route", "direct_sql", {"reason": reason}, 0)
                return self._run_direct_sql(
                    question_clean,
                    user_id=user_id, is_admin=is_admin,
                    run_started=run_started, trace_id=trace_id, events=events, emit=emit,
                    history=history, previous_plan=previous_plan, scope=scope,
                )
        except _DirectSQLFallback as exc:
            # direct-SQL 的 LLM 不可用/失败 → 优雅回退到结构化 planner（自带规则兜底），
            # 不让 force_refresh / 复杂问题在网关抖动时硬失败成"问数失败"。
            emit("route", "planner_fallback", {"reason": "direct_sql_llm_unavailable"}, 0)
            logger.info("direct_sql LLM unavailable (%s) — falling back to structured planner", str(exc)[:120])
        except Exception as exc:
            logger.warning("direct_sql route check failed: %s — falling back to planner", exc)

        # Stage 2 + 3: retrieval + plan (planner internally calls retriever; 按 scope 分域)
        plan_started = time.perf_counter()
        try:
            plan_result = self.planner.plan(question_clean, history=history, previous_plan=previous_plan, previous_rows=previous_rows, scope=scope)
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

        # Stage 3.3: 超范围拒答 —— 问题落在该用户的表范围之外，明确拒答 + 告知范围。
        # 管住准确率的分母：宁拒答不硬答；拒答文案附用户范围与可问示例，引导改问。
        if plan.out_of_scope:
            allowed_sorted = sorted(scope.allowed_tables) if (scope is not None and getattr(scope, "restricted", False)) else []
            table_labels = []
            for t in allowed_sorted:
                tdef = self.semantic.table(t)
                table_labels.append(tdef.label if tdef else t)
            suggestions: list[str] = []
            for m in self.semantic.list_metrics():
                if len(suggestions) >= 3:
                    break
                if m.table in allowed_sorted and m.typical_questions:
                    q0 = str(m.typical_questions[0]).strip()
                    if q0 and q0 not in suggestions:
                        suggestions.append(q0)
            scope_desc = "、".join(table_labels[:15]) if table_labels else "（暂未配置任何数据表）"
            narrative = (
                f"{plan.out_of_scope_reason or '这个问题超出了您的数据范围'}。"
                f"您当前可查询的数据范围：{scope_desc}"
                + (f"（共 {len(table_labels)} 张表）" if table_labels else "")
                + "。如需扩大范围请联系管理员。"
            )
            emit("scope", "rejected", {"reason": plan.out_of_scope_reason, "tables": len(table_labels)}, 0)
            elapsed = int((time.perf_counter() - run_started) * 1000)
            return PipelineResult(
                trace_id=trace_id,
                question=question_clean,
                answer={
                    "narrative": narrative,
                    "highlights": [],
                    "risk_notes": [],
                    "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                    "chart": {"type": "none"},
                    "suggestions": suggestions,
                    "explainability": {
                        "rule": "超出数据范围时明确拒答（宁拒答不硬答）",
                        "reason": plan.out_of_scope_reason,
                        "allowed_tables": allowed_sorted,
                    },
                },
                plan=plan.to_dict(),
                sql="",
                rows=0,
                elapsed_ms=elapsed,
                cached=False,
                events=[e.to_dict() for e in events],
            )

        # Stage 3.4: Accuracy Critic（确定性结构化复核 + 至多一次确定性修复）。
        # 在编译前对最终 plan 做一次"是否honor问句意图"的独立复核：缺指标补、TopN 漏截截、
        # 排序反了纠、结构完整却误澄清放行。仅对高风险问题触发（multi-metric/field-list/
        # TopN/派生指标/追问/低置信/已计划澄清），其余信任 planner，零额外开销。
        # 复核结果落 plan.reasoning 之外的 events 审计，绝不向用户暴露内部提示。
        try:
            followup = self.planner._looks_like_followup(question_clean, previous_plan)
            if AccuracyCritic.should_review(plan, plan_result.rule_seed, followup):
                plan, critic_report, repaired = self.critic.critique_and_repair(
                    question_clean, plan,
                    rule_seed=plan_result.rule_seed, bundle=plan_result.bundle,
                    previous_plan=previous_plan, inherit=followup,
                )
                emit("critic", "ok" if critic_report.ok else "repaired", {
                    "severity": critic_report.severity,
                    "repaired": repaired,
                    "missing_metrics": critic_report.missing_metrics,
                    "missing_dimensions": critic_report.missing_dimensions,
                    "clarify_suppressed": critic_report.clarify_should_be_suppressed,
                }, 0)
        except Exception as exc:  # noqa: BLE001 — 复核绝不阻断主链路
            logger.warning("accuracy critic skipped: %s", exc)

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

        # Stage 3.7: L2 plan-keyed cache（原 3.2，刻意移到权限注入之后）。
        #
        # 修复（P0 串数据 bug）：旧实现在 Stage 3.6 之前计算签名并直接命中返回，
        # 而 set_plan 存的是注入行级权限之后算出的完整答案 → 行级权限不同的两个用户
        # 问同一句话会得到相同的"无权限"签名，互相命中对方的数字（错数 + 越权）。
        # 现在签名在 apply_to_plan 之后计算：signature() 本就包含 filters，行级权限
        # 不同 → filters 不同 → 签名天然分开；权限相同的用户（含全部无限制用户）
        # 仍然共享缓存，性能不受影响。
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

        # Stage 5: guard（分域用户按本人表白名单校验，未分域走全语义层白名单）
        scope_whitelist = (
            scope.allowed_tables
            if (scope is not None and getattr(scope, "restricted", False))
            else None
        )
        try:
            report = self.guard.validate(raw_sql, allowed_tables=scope_whitelist)
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
            # 取消检查（真正打 DB 之前）：断开就不发这条可能很重的查询。
            _check_cancel("execute")
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
        # 取消检查（叙述 LLM 之前）：已执行完拿到数据，但客户端走了就别再花一次 LLM 生成叙述。
        _check_cancel("answer")
        answer_started = time.perf_counter()
        answer_payload = self.answerer.build(question_clean, plan, meta, exec_obj, guarded_sql, skip_llm=skip_llm_narrative)
        answer_ms = int((time.perf_counter() - answer_started) * 1000)

        # 相对时间却 0 行：明确告知"最新可用月份"，杜绝静默空表误导用户（审计 P1 时间口径）。
        try:
            tr_kind = str(getattr(plan.time_range.kind, "value", plan.time_range.kind))
            if exec_obj is not None and exec_obj.row_count == 0 and tr_kind == "relative":
                latest = self._table_latest_month(plan.table) or self.semantic.data_range_latest
                if latest:
                    note = (f"所选时间范围内暂无数据。该数据集当前最新可用月份为 {latest}，"
                            f"请改用具体月份（如「{latest}」）后重试。")
                    cur = str((answer_payload or {}).get("narrative") or "")
                    if "最新可用月份" not in cur:
                        answer_payload["narrative"] = note + (("\n\n" + cur) if cur else "")
                    emit("data_range", "empty_relative", {"latest": latest, "table": plan.table}, 0)
        except Exception as exc:  # noqa: BLE001 — 提示性增强，绝不影响主结果
            logger.debug("data-range note skipped: %s", exc)

        # Stage 7.1: Result Validator（执行后、答案定稿前的结果一致性校验）。
        # 把"答非所问/排序错/空结果"显式化：报告落 explainability.validation 供审计，
        # 面向用户的提示并入 risk_notes（去重），绝不伪造成功、不改写真实数据。
        try:
            vreport = self.validator.validate(question_clean, plan, meta, exec_obj)
            if vreport.issues:
                explain = answer_payload.setdefault("explainability", {})
                if isinstance(explain, dict):
                    explain["validation"] = vreport.to_dict()
                notes = vreport.user_notes()
                if notes:
                    existing = list(answer_payload.get("risk_notes") or [])
                    for n in notes:
                        if n not in existing:
                            existing.append(n)
                    answer_payload["risk_notes"] = existing
            # 始终上报 validate 事件（含 ok），让 trace 能看到"已校验"，便于审计与观测。
            emit("validate", "ok" if vreport.ok else "flagged",
                 {"severity": vreport.severity, "issues": [i.code for i in vreport.issues]}, 0)
        except Exception as exc:  # noqa: BLE001 — 校验绝不阻断主结果
            logger.warning("result validator skipped: %s", exc)

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
                        previous_plan: QueryPlan | None = None,
                        scope: Any | None = None) -> PipelineResult:
        """Direct-SQL：LLM 直接生成 SQL → guard → 权限注入 → 执行 → 总结。
        分域用户：schema 上下文只含本人的表（LLM 看不到域外表），guard 按本人白名单校验。"""
        from app.core.direct_sql import generate_direct_sql, summarize_direct_result
        from app.core.guard import GuardError
        from app.core.permissions import (
            PermissionDenied, inject_row_filters_into_sql, validate_sql_columns,
        )

        scope_whitelist = (
            scope.allowed_tables
            if (scope is not None and getattr(scope, "restricted", False))
            else None
        )

        # 1) 生成 SQL
        gen_started = time.perf_counter()
        try:
            sql = generate_direct_sql(
                question, semantic_layer=self.semantic, llm=self.llm,
                history=history, previous_plan=previous_plan.to_dict() if previous_plan else None,
                allowed_tables=scope_whitelist,
            )
        except Exception as exc:
            emit("direct_sql", "llm_error", {"reason": str(exc)[:200]}, int((time.perf_counter() - gen_started) * 1000))
            # LLM 写 SQL 失败 → 不硬失败，抛回退信号让 run() 走结构化 planner（含规则兜底），
            # 保证 force_refresh/复杂问题在网关抖动时仍能降级出结果。
            raise _DirectSQLFallback(str(exc)) from exc
        if not sql:
            return self._failure_result(question, trace_id, run_started, events,
                                        "未生成有效 SQL，请稍后再试或换一种问法。")
        emit("direct_sql", "generated", {"sql_preview": sql[:200]}, int((time.perf_counter() - gen_started) * 1000))

        # 2) AST guard（表白名单按用户分域 + 只 SELECT + 自动 LIMIT）
        try:
            report = self.guard.validate(sql, allowed_tables=scope_whitelist)
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

        # 7) 组 display_rows + 列类型推断（维度/指标/时间），让 direct-SQL 结果也能切图表，
        #    不再永远只能看列表（修复"走模型后图表不可用"的体验回退）。
        display_rows = [[str(v) if v is not None else "—" for v in row] for row in exec_obj.rows]
        display_cols = _classify_direct_columns(exec_obj.columns, exec_obj.rows)
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


_pipeline_singleton: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _pipeline_singleton = Pipeline()
        _pipeline_singleton.warmup()
    return _pipeline_singleton
