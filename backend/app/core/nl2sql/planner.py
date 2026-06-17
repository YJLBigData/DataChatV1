"""Planner — turn natural language into a typed QueryPlan.

Strategy:
1. Lightweight rule extraction (time, dimension values, calculation keywords)
   over the candidate set returned by the retriever — the retriever already
   ranks the most likely metric/dimensions/few-shots.
2. Single LLM call asks the model to fill the QueryPlan as JSON. We feed it
   only the candidates (not the entire semantic layer), keeping the prompt
   small and accuracy high.
3. Post-validate: the metric must exist; the table must come from semantic
   layer; group_by / filter dimensions must be valid for that table.
4. If validation fails or confidence < threshold => mark needs_clarify with
   structured options.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.cache import get_cache
from app.core.llm.router import LLMRouter, get_llm_router
from app.core.retrieval import HybridRetriever, RetrievalBundle, RetrievalCandidate
from app.core.semantic import SemanticLayer

from .plan import HavingFilter, OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange

logger = logging.getLogger("datachat.planner")


CALCULATION_KEYWORDS: dict[str, str] = {
    "同比": "yoy_growth",
    "同比增长": "yoy_growth",
    "yoy": "yoy_growth",
    "环比": "mom_growth",
    "环比增长": "mom_growth",
    "mom": "mom_growth",
    "占比": "ratio",
    "比例": "ratio",
    "比重": "ratio",
    "排名": "rank",
    "排行": "rank",
    "top": "rank",
    "前": "rank",
    "趋势": "trend",
    "走势": "trend",
    "差值": "delta",
    "差距": "delta",
    "累计": "cumulative",
    "累积": "cumulative",
}

PERIOD_KEYWORDS: dict[str, tuple[str, int]] = {
    "本月": ("this_month", 0),
    "当月": ("this_month", 0),
    "这个月": ("this_month", 0),
    "本年": ("this_year", 0),
    "今年": ("this_year", 0),
    "本年累计": ("ytd", 0),
    "年度": ("this_year", 0),
    "上月": ("last_month", 0),
    "上个月": ("last_month", 0),
    "上一个月": ("last_month", 0),
    "上年": ("last_year", 0),
    "去年": ("last_year", 0),
    "近三个月": ("last_n_months", 3),
    "近3个月": ("last_n_months", 3),
    "最近三个月": ("last_n_months", 3),
    "近六个月": ("last_n_months", 6),
    "近6个月": ("last_n_months", 6),
    "近半年": ("last_n_months", 6),
    "近12个月": ("last_n_months", 12),
    "近一年": ("last_n_months", 12),
    "ytd": ("ytd", 0),
    "年初至今": ("ytd", 0),
}

QUARTER_RE = re.compile(r"(?:(\d{4})年?)?(?:第)?([1234一二三四])\s*季度")
QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4", "1": "1", "2": "2", "3": "3", "4": "4"}

# 指标阈值过滤（HAVING）：把"达成率低于90%""销售额超过100万""占比大于5%"这类
# 对【指标】的比较条件提取出来。注意：否定式/双字算子（不低于/不超过）必须排在裸算子
# （低于/超过）之前——alternation 在同一起点按从左到右第一个命中，"不"位置先吃掉"不低于"，
# 避免被误判成 lt。整段为非重叠匹配（finditer），所以"不低于"里的"低于"不会被二次命中。
_THRESHOLD_RE = re.compile(
    r"(?P<op>不低于|不少于|不小于|大于等于|不小过|至少|≥|>=|"
    r"不超过|不高于|不大于|小于等于|至多|最多|≤|<=|"
    r"高于|大于|超过|超出|多于|大过|＞|>|"
    r"低于|小于|不足|不到|少于|达不到|未达到|未及|低过|＜|<)"
    r"\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<pct>%|％)?\s*(?P<unit>万|亿)?"
)
_THRESHOLD_OP_CANON = {
    "不低于": "gte", "不少于": "gte", "不小于": "gte", "大于等于": "gte", "不小过": "gte",
    "至少": "gte", "≥": "gte", ">=": "gte",
    "不超过": "lte", "不高于": "lte", "不大于": "lte", "小于等于": "lte",
    "至多": "lte", "最多": "lte", "≤": "lte", "<=": "lte",
    "高于": "gt", "大于": "gt", "超过": "gt", "超出": "gt", "多于": "gt", "大过": "gt", "＞": "gt", ">": "gt",
    "低于": "lt", "小于": "lt", "不足": "lt", "不到": "lt", "少于": "lt",
    "达不到": "lt", "未达到": "lt", "未及": "lt", "低过": "lt", "＜": "lt", "<": "lt",
}

# 多轮"继续/下钻/切片"标记 —— 出现任一即视为对上一轮的追问（而非独立新问题）。
# 刻意只收录追问语气词；独立问句里常见的 "各/分别/排序/列出/前N" 一律不在此列，
# 避免把带完整口径的标准问句误判为追问。
CONTINUATION_MARKERS: tuple[str, ...] = (
    "只看", "仅看", "单看", "就看", "继续", "接着", "顺着", "下钻", "钻取",
    "拆到", "拆开", "拆分", "细分到", "细化到", "展开看", "再按", "再看",
    "再拆", "还看", "其中", "上面", "上述", "刚才", "之前", "这些", "那些",
    "这几个", "那几个", "这3个", "这三个", "那3个", "那三个", "表现怎么样",
    "怎么样", "加上", "去掉", "换成", "改成", "基础上",
)

# 时间表达（出现任一即认为问句自带时间口径，多为独立新问题）。
_TIME_HINT_RE = re.compile(
    r"20\d{2}|季度|本月|当月|这个月|上月|上个月|本年|今年|去年|上年|"
    r"年初至今|ytd|近\s*\d|最近|\d+\s*月"
)


@dataclass
class PlanResult:
    plan: QueryPlan
    bundle: RetrievalBundle
    raw_llm_payload: dict[str, Any]
    elapsed_ms: int


PLAN_SCHEMA = """{
  "metric": "<语义层 metrics 的英文 key>",
  "extra_metrics": ["<其它同表指标 key>"],
  "table": "<物理表名，从语义层 tables 中选择，必须与 metric 一致>",
  "group_by": ["<语义层 dimensions 英文 key>"],
  "filters": [
    {"dimension": "<语义层 dimensions key>", "op": "eq|in|like", "values": ["..."]}
  ],
  "having": [
    {"metric": "<语义层 metrics key>", "op": "lt|lte|gt|gte|eq|ne", "value": 0.9}
  ],
  "time_range": {
    "kind": "none|relative|absolute|range",
    "period": "this_month|last_month|this_year|last_year|ytd|last_n_months",
    "n": 0,
    "year": "YYYY",
    "months": ["MM"],
    "start_ym": "YYYY-MM",
    "end_ym": "YYYY-MM"
  },
  "calculation": "yoy_growth|mom_growth|ratio|rank|trend|delta|cumulative|''",
  "order_by": [{"field": "<metric or dimension key>", "dir": "asc|desc"}],
  "limit": 0,
  "needs_clarify": false,
  "clarify_reason": "",
  "clarify_options": [],
  "confidence": 0.0,
  "reasoning": "<一段中文解释>"
}"""


class Planner:
    def __init__(
        self,
        semantic: SemanticLayer,
        retriever: HybridRetriever,
        llm: LLMRouter | None = None,
    ):
        self.semantic = semantic
        self.retriever = retriever
        self.llm = llm or get_llm_router()
        self.cache = get_cache()

    # --------------------------------------------------- multi-turn context

    def _question_has_time(self, question: str) -> bool:
        return bool(_TIME_HINT_RE.search(question or ""))

    def _looks_like_followup(self, question: str, previous_plan: QueryPlan | None) -> bool:
        """是否把本轮当作上一轮的追问（继承表/指标/时间，只叠加显式增量）。

        判定原则（准确率优先）：
        · 没有可继承的上一轮 plan → 一定不是追问；
        · 出现"继续/下钻/只看/拆开/…"等追问语气词 → 是追问；
        · 句子自带明确时间口径（如 2025年1月）且无追问语气词 → 视为独立新问题；
        · 其余（有上文、无独立时间口径）→ 视为对上文的延续。
        """
        if not (previous_plan and previous_plan.metric):
            return False
        q = question or ""
        if any(mk in q for mk in CONTINUATION_MARKERS):
            return True
        if self._question_has_time(q):
            return False
        return True

    def _explicit_metric_in_question(self, question: str, allowed_tables: "set[str] | frozenset[str] | None" = None):
        """问句中是否显式点名了某个指标（按别名最长匹配）。用于判断用户是否主动换指标。
        allowed_tables 非 None 时只在该表范围内匹配（分域：不会"换"到域外指标上）。"""
        q = question or ""
        best = None
        for m in self.semantic.list_metrics():
            if allowed_tables is not None and m.table not in allowed_tables:
                continue
            for a in m.all_aliases():
                if a and len(a) >= 2 and a in q:
                    if best is None or len(a) > best[1]:
                        best = (m, len(a))
        return best[0] if best else None

    def _metric_name_in_text(self, text: str) -> str:
        """文本片段里出现的指标（按最长别名匹配），返回逻辑指标 key；没有则空串。
        用于把阈值条件就近绑定到具体指标（"达成率低于90%" → 达成率指标）。"""
        t = text or ""
        best_name, best_len = "", 0
        for m in self.semantic.list_metrics():
            for a in m.all_aliases():
                if a and len(a) >= 2 and a in t and len(a) > best_len:
                    best_name, best_len = m.name, len(a)
        return best_name

    @staticmethod
    def _cjk_ngrams(text: str) -> set[str]:
        toks: set[str] = set()
        t = text or ""
        for size in (2, 3):
            for i in range(0, max(0, len(t) - size + 1)):
                frag = t[i : i + size]
                if frag.strip():
                    toks.add(frag)
        return toks

    # 同一业务概念在不同表的等价维度族（只在族内做安全改写）。
    # 关键修复：旧实现用"别名 n-gram 任意重叠"猜映射，会把 is_guide_shop
    # 错配成 big_system_channel，生成 `big_system_channel_name='是'` 这种脏条件。
    # 现在只允许在明确等价族内对齐；族外一律不映射 → 调用方丢弃，宁缺勿错。
    _DIM_FAMILIES: tuple[tuple[str, ...], ...] = (
        ("channel_type", "big_system_channel"),  # 渠道族
    )

    def _remap_dim(self, dim_name: str, table: str) -> str | None:
        """把逻辑维度对齐到目标表上的物理维度（保守、零猜测）。

        · 维度本身在 table 上有列 → 原样返回；
        · 否则仅当它属于某个明确"等价族"，且族内有维度在 table 上可用 → 用那个；
        · 其它情况返回 None（调用方丢弃该维度/过滤），绝不靠模糊相似度乱配。
        """
        d = self.semantic.dimension(dim_name)
        if not d:
            return None
        if table in d.table_columns:
            return dim_name
        for family in self._DIM_FAMILIES:
            if dim_name not in family:
                continue
            for sib in family:
                if sib == dim_name:
                    continue
                sd = self.semantic.dimension(sib)
                if sd and table in sd.table_columns:
                    return sib
        return None

    def _augment_bundle_with_previous(self, bundle: RetrievalBundle, previous_plan: QueryPlan) -> None:
        """把上一轮用到的指标/表/维度强制放进候选集。

        否则 LLM 受"不得编造候选集外的指标"约束，反而无法沿用上一轮指标。
        """
        def _ensure_metric(name: str) -> None:
            if not name:
                return
            md = self.semantic.metric(name)
            if not md or any(c.name == name for c in bundle.metrics):
                return
            bundle.metrics.insert(0, RetrievalCandidate(
                kind="metric", name=md.name, label=md.label, score=1.0,
                text=md.label, payload={"unit": md.unit, "table": md.table, "domain": md.domain},
            ))

        _ensure_metric(previous_plan.metric)
        for em in previous_plan.extra_metrics:
            _ensure_metric(em)

        prev_metric = self.semantic.metric(previous_plan.metric)
        if prev_metric:
            tdef = self.semantic.table(prev_metric.table)
            if tdef and not any(c.name == tdef.name for c in bundle.tables):
                bundle.tables.insert(0, RetrievalCandidate(
                    kind="table", name=tdef.name, label=tdef.label, score=1.0,
                    text=tdef.label, payload={"grain": tdef.grain},
                ))

        known_dims = {c.name for c in bundle.dimensions}
        for dim in [*previous_plan.group_by, *(f.dimension for f in previous_plan.filters)]:
            dd = self.semantic.dimension(dim)
            if dd and dd.name not in known_dims:
                bundle.dimensions.insert(0, RetrievalCandidate(
                    kind="dimension", name=dd.name, label=dd.label, score=1.0,
                    text=dd.label, payload={"sample_values": dd.sample_values[:8]},
                ))
                known_dims.add(dd.name)

    # ----------------------------------------------------- scope / few-shots

    def _out_of_scope_reason(
        self,
        question: str,
        bundle: RetrievalBundle,
        allowed: "frozenset[str] | None",
        *,
        followup: bool,
    ) -> str:
        """超范围判定（仅分域用户、且非多轮追问时启用）。

        两级信号，宁紧勿松：
          ① 显式点名了某个指标，但它所在的表不在用户范围内 → 精确拒答（高置信）；
          ② 范围内检索全员低分（< DATACHAT_SCOPE_REJECT_THRESHOLD，默认 0.35，
             0=关闭）→ 问题大概率不属于该用户的任何表 → 通用拒答。
        返回空串 = 在范围内，正常走 planner。
        """
        if allowed is None or followup:
            return ""
        # ① 显式指标点名
        in_scope_hit = self._explicit_metric_in_question(question, allowed_tables=allowed)
        if in_scope_hit is None:
            global_hit = self._explicit_metric_in_question(question)
            if global_hit is not None:
                return (
                    f"您问到的「{global_hit.label}」所在的数据表不在您的数据范围内"
                )
        else:
            return ""  # 显式点名了范围内指标 → 一定可答
        # ② 检索全员低分
        try:
            threshold = float(os.environ.get("DATACHAT_SCOPE_REJECT_THRESHOLD", "0.35") or 0)
        except ValueError:
            threshold = 0.35
        if threshold <= 0:
            return ""
        best = 0.0
        for c in (*bundle.metrics, *bundle.tables, *bundle.few_shots):
            best = max(best, float(c.score or 0.0))
        if best < threshold:
            return "未在您的数据范围内找到与问题匹配的指标或数据表"
        return ""

    def _merge_adopted_few_shots(
        self,
        bundle: RetrievalBundle,
        question: str,
        allowed: "frozenset[str] | None",
    ) -> None:
        """把"用户采纳沉淀"的同域 few-shot 合并进候选（Vanna 路线的飞轮）。
        采纳库不可用/为空时静默跳过，绝不影响主链路。"""
        try:
            from app.core.fewshot_store import get_fewshot_store
            shots = get_fewshot_store().search(question, allowed_tables=allowed, limit=3)
        except Exception:
            return
        if not shots:
            return
        known = {c.label for c in bundle.few_shots}
        for s in reversed(shots):  # 逆序 insert(0)，保持高分在前
            q = str(s.get("question") or "")
            if not q or q in known:
                continue
            bundle.few_shots.insert(0, RetrievalCandidate(
                kind="few_shot", name=q[:40], label=q,
                score=float(s.get("score") or 0.9), text=q,
                payload={"intent": s.get("intent") or {}, "source": "adopted"},
            ))
            known.add(q)

    # -------------------------------------------------------------- main

    def plan(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        previous_plan: QueryPlan | None = None,
        today: date | None = None,
        scope: Any | None = None,   # permissions.UserScope；None = 不分域（兼容旧调用）
    ) -> PlanResult:
        started = time.perf_counter()
        followup = self._looks_like_followup(question, previous_plan)
        # 分域：检索/显式指标匹配/表卡片只看用户自己的表集合
        allowed: frozenset[str] | None = None
        if scope is not None and getattr(scope, "restricted", False):
            allowed = frozenset(scope.allowed_tables or ())

        # 召回查询：追问时把上一轮指标/问句拼进去，否则裸追问("把东一区按渠道拆开看")
        # 没有指标信号，会召回错指标 → 错表。
        search_q = question
        if followup and previous_plan:
            prev_md = self.semantic.metric(previous_plan.metric)
            prev_user = ""
            for m in reversed(history or []):
                if m.get("role") == "user":
                    prev_user = m.get("content", "")
                    break
            search_q = " ".join(x for x in (prev_md.label if prev_md else "", prev_user, question) if x)

        bundle = self.retriever.search(search_q, allowed_tables=allowed)
        self._merge_adopted_few_shots(bundle, question, allowed)
        if followup and previous_plan:
            self._augment_bundle_with_previous(bundle, previous_plan)
        rule_seed = self._extract_rule_seed(question, today=today)

        # 超范围拒答：问题落在用户表范围之外 → 不进 LLM，明确拒答（宁拒答不硬答）。
        oos_reason = self._out_of_scope_reason(
            question, bundle, allowed, followup=bool(followup and previous_plan),
        )
        if oos_reason:
            logger.info("plan.out_of_scope user_tables=%s q=%r reason=%s",
                        sorted(allowed or ()), question[:80], oos_reason)
            return PlanResult(
                plan=QueryPlan(out_of_scope=True, out_of_scope_reason=oos_reason),
                bundle=bundle,
                raw_llm_payload={"out_of_scope": True},
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )

        # Try cache first —— key 必须含上一轮上下文 + followup + 数据域指纹，
        # 否则同一句话在不同上下文/不同表范围的用户之间会命中同一缓存（历史串话根因之一）。
        cache_key = json.dumps(
            {
                "q": question,
                "h": [m.get("content", "") for m in (history or [])][-3:],
                "today": (today or date.today()).isoformat(),
                "rule": rule_seed,
                "prev": (previous_plan.signature() if (previous_plan and previous_plan.metric) else ""),
                "followup": followup,
                "scope": getattr(scope, "fingerprint", "") if scope is not None else "",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        from hashlib import sha1
        sig = sha1(cache_key.encode("utf-8")).hexdigest()
        cached = self.cache.get_plan(sig)
        if cached:
            try:
                plan = QueryPlan.from_dict(cached)
                return PlanResult(plan=plan, bundle=bundle, raw_llm_payload={"cache": True}, elapsed_ms=int((time.perf_counter() - started) * 1000))
            except Exception:
                pass

        prompt = self._build_prompt(question, bundle, history=history, previous_plan=previous_plan, rule_seed=rule_seed, today=today or date.today(), followup=followup, scope=scope)
        try:
            payload, llm_result = self.llm.chat_json(
                [
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ],
                schema_hint=PLAN_SCHEMA,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning("planner LLM failed: %s — falling back to rules-only", exc)
            payload = self._rule_only_plan(question, bundle, rule_seed, today=today or date.today())
            llm_result = None

        plan = QueryPlan.from_dict(payload if isinstance(payload, dict) else {})
        plan = self._validate_and_repair(
            plan, bundle, rule_seed, today=today or date.today(),
            previous_plan=previous_plan, followup=followup, question=question,
            allowed_tables=allowed,
        )

        # Save to cache
        try:
            self.cache.set_plan(sig, plan.to_dict())
        except Exception:
            pass

        return PlanResult(
            plan=plan,
            bundle=bundle,
            raw_llm_payload=payload if isinstance(payload, dict) else {"raw": str(payload)},
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------- prompts

    def _status_mark(self, kind: str, name: str) -> str:
        obj = self.semantic.metric(name) if kind == "metric" else self.semantic.dimension(name)
        return "已认证" if getattr(obj, "status", "draft") == "verified" else "草稿"

    def _table_cards(self, bundle: RetrievalBundle, scope: Any | None) -> tuple[str, bool]:
        """表候选呈现（P0.5 全量呈现）。

        用户数据域 ≤ DATACHAT_FULL_TABLE_CARDS_MAX（默认 20）张表时，呈现该域
        **全部**表的语义卡片——选表从"召回命中概率题"变成"完整画面里的选择题"，
        域外表模型根本看不到。超过阈值回退为召回 top-k 行。
        返回 (block, 是否全量)。"""
        try:
            max_cards = int(os.environ.get("DATACHAT_FULL_TABLE_CARDS_MAX", "20") or 0)
        except ValueError:
            max_cards = 20
        if scope is not None and getattr(scope, "restricted", False):
            names = sorted(scope.allowed_tables or ())
        else:
            names = [t.name for t in self.semantic.list_tables()]
        if max_cards <= 0 or len(names) > max_cards:
            block = "\n".join(
                f"- key={c.name} | label={c.label} | grain={c.payload.get('grain','')}"
                for c in bundle.tables
            ) or "- (无召回)"
            return block, False
        cards: list[str] = []
        for name in names:
            t = self.semantic.table(name)
            if not t:
                continue
            status = "已认证" if getattr(t, "status", "draft") == "verified" else "草稿"
            desc = " ".join((t.description or "").split())[:200]
            metric_labels = [m.label for m in self.semantic.list_metrics() if m.table == name][:10]
            lines = [
                f"- key={t.name} | label={t.label} | 状态={status}",
                f"  粒度：{t.grain or '—'}",
            ]
            if desc:
                lines.append(f"  定位：{desc}")
            if metric_labels:
                lines.append(f"  该表指标：{'、'.join(metric_labels)}")
            for n in (t.notes or [])[:2]:
                n_clean = " ".join(str(n).split())[:80]
                if n_clean:
                    lines.append(f"  注意：{n_clean}")
            cards.append("\n".join(lines))
        if not cards:
            return "- (您的数据范围内没有可用数据表)", True
        return "\n".join(cards), True

    def _build_prompt(
        self,
        question: str,
        bundle: RetrievalBundle,
        *,
        history: list[dict[str, str]] | None,
        previous_plan: QueryPlan | None,
        rule_seed: dict[str, Any],
        today: date,
        followup: bool = False,
        scope: Any | None = None,
    ) -> dict[str, str]:
        metric_block = "\n".join(
            f"- key={c.name} | label={c.label} | unit={c.payload.get('unit','')} | table={c.payload.get('table','')} | 状态={self._status_mark('metric', c.name)} | score={c.score:.3f}"
            for c in bundle.metrics
        ) or "- (无召回)"

        dim_block = "\n".join(
            f"- key={c.name} | label={c.label} | sample={','.join(c.payload.get('sample_values') or [])[:80]} | 状态={self._status_mark('dimension', c.name)} | score={c.score:.3f}"
            for c in bundle.dimensions
        ) or "- (无召回)"

        table_block, full_tables = self._table_cards(bundle, scope)

        few_shot_block = "\n".join(
            f"- 问句: {c.label}\n  期望plan: {json.dumps(c.payload.get('intent') or {}, ensure_ascii=False)}"
            for c in bundle.few_shots[:5]
        ) or ""

        prev_plan_block = ""
        if previous_plan and previous_plan.metric:
            prev_plan_block = f"\n上一轮 plan：{json.dumps(previous_plan.to_dict(), ensure_ascii=False)}\n"

        history_block = ""
        if history:
            tail = history[-4:]
            history_block = "\n".join(f"[{m.get('role','user')}] {m.get('content','')}" for m in tail)

        followup_rule = ""
        if followup and previous_plan and previous_plan.metric:
            followup_rule = (
                "\n【多轮追问规则｜最高优先级】本轮是对『上一轮 plan』的追问（升维/降维/筛选/排序/换切面）。"
                "必须严格继承上一轮的 metric、extra_metrics、table、time_range、calculation；"
                "只允许按用户这句话的显式增量改动 group_by / filters / having / order_by / limit。"
                "除非用户这句话本身明确点了**另一个业务指标**或**另一个时间口径**，否则一律不得更换指标/表/时间。"
                "严禁因为本句没提销售口径就回退到默认销售额或最新月份——那会答错。\n"
            )

        scope_rule = ""
        if scope is not None and getattr(scope, "restricted", False):
            scope_rule = (
                "【数据范围】下面的候选数据表就是该用户可用的**全部**数据表，"
                "选表/选指标必须严格出自候选集，绝不能引用清单之外的表。"
            )
        status_rule = (
            "候选若标注 状态=草稿，表示其业务描述为机器起草、未经业务认证；"
            "当草稿口径与已认证口径冲突或两者难分时，优先选已认证条目，仍无法确定则 needs_clarify。"
        )

        system = (
            "你是飞鹤公司的智能问数规划器。任务：把高管的中文问题翻译为受控的 QueryPlan JSON。"
            "你不能编造任何不在候选集中的指标、维度、表。如果问题模糊（例如缺少必要维度筛选、口径冲突），"
            f"请把 needs_clarify 设为 true 并给出 clarify_options。今天是 {today.isoformat()}，"
            f"数据库覆盖范围：{self.semantic.data_range_earliest} ~ {self.semantic.data_range_latest}。"
            f"{scope_rule}{status_rule}"
            f"{followup_rule}"
            "口径要求：1) 仅当问句明确提到『销售额』且无上一轮可继承口径时，销售额才默认 "
            "terminal_sale_amount_total；2) 涉及'达成率/目标完成'必须用 target 表的指标；"
            "3) 用户提到'同比/环比/占比/排名/趋势'必须填到 calculation 字段；"
            "4) 对【指标】的阈值筛选（如'达成率低于90%''销售额超过100万''占比大于5%'）必须写进 having，"
            "不要遗漏：op 用 lt/lte/gt/gte/eq/ne；百分比类指标(value 用比率，如 90%→0.9、120%→1.2)。"
        )

        table_header = (
            "候选数据表 tables（已是您可用的全部数据表，含语义卡片）"
            if full_tables else "候选数据表 tables"
        )
        user = (
            f"用户问题：{question}\n\n"
            f"历史对话（最近 4 条，可能用于多轮继承）：\n{history_block or '(无)'}\n"
            f"{prev_plan_block}\n"
            f"---\n候选指标 metrics（按相关度排序）：\n{metric_block}\n\n"
            f"候选维度 dimensions：\n{dim_block}\n\n"
            f"{table_header}：\n{table_block}\n\n"
            f"参考样例 few-shots：\n{few_shot_block}\n\n"
            f"基于规则提取（时间/排名/算子）：{json.dumps(rule_seed, ensure_ascii=False)}\n\n"
            f"请只输出符合 schema 的 JSON："
        )
        return {"system": system, "user": user}

    # --------------------------------------------------------- rules

    def _extract_rule_seed(self, question: str, *, today: date | None = None) -> dict[str, Any]:
        q = question or ""
        ql = q.lower()
        seed: dict[str, Any] = {
            "calculation": "",
            "period": "",
            "n": 0,
            "absolute": None,
            "rank_n": 0,
            "filter_hits": [],
            "group_by_hint": [],
            "metric_hits": [],     # 问句里显式点名的全部指标（按别名最长优先去重）
            "order_hint": None,    # {"field": <metric/dim>, "dir": "asc|desc"} 来自"按X从低到高/高到低"
            "having_hints": [],    # [{"metric": key, "op": lt|lte|gt|gte, "value": float, "is_percent": bool, "raw": str}]
        }
        # calculation keywords (most-specific keys first to preserve correctness)
        for k, v in CALCULATION_KEYWORDS.items():
            if k in q:  # Chinese keywords are case-insensitive in Chinese
                seed["calculation"] = v
                break
        for k, v in CALCULATION_KEYWORDS.items():
            if k in ql:
                seed["calculation"] = seed["calculation"] or v
        # rank n: top10 / 前10 / 前 5 名 / 前三 / TOP5
        m = re.search(r"(?:top|前)\s*(\d{1,3})", ql)
        if m:
            seed["rank_n"] = int(m.group(1))
            seed["calculation"] = seed["calculation"] or "rank"
        else:
            cn_rank = re.search(r"前\s*(一|二|三|四|五|六|七|八|九|十)", q)
            if cn_rank:
                cn_map = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
                seed["rank_n"] = cn_map.get(cn_rank.group(1), 10)
                seed["calculation"] = seed["calculation"] or "rank"
        # period
        for k, (period, n) in PERIOD_KEYWORDS.items():
            if k in q or k in ql:
                seed["period"] = period
                seed["n"] = n
                break
        # quarter
        qmatch = QUARTER_RE.search(q)
        if qmatch:
            year = qmatch.group(1) or ""
            qchar = QUARTER_MAP.get(qmatch.group(2)) or ""
            if qchar:
                qi = int(qchar)
                months = [f"{(qi - 1) * 3 + i + 1:02d}" for i in range(3)]
                seed["absolute"] = {"year": year or "", "months": months}
        # explicit YYYY-MM or YYYY年MM月
        ym = re.findall(r"(20\d{2})\s*[年-]\s*(\d{1,2})", q)
        if ym and not seed.get("absolute"):
            year = ym[0][0]
            months = [f"{int(m):02d}" for _, m in ym]
            seed["absolute"] = {"year": year, "months": months}

        # filter hits (dimension sample_values, value_dict labels)
        # 关键修复：只接受长度≥2 的值做子串命中。像 is_guide_shop 的样例值
        # "是"/"否" 是单字，会命中"分别是多少/达成率是"等任何中文问句，
        # 造成 `WHERE 列='是'` 这种莫名其妙的过滤。单字一律不作为过滤信号。
        for d in self.semantic.list_dimensions():
            for v in d.sample_values:
                if v and len(str(v)) >= 2 and v in q:
                    seed["filter_hits"].append({"dimension": d.name, "value": v})
                    break
            for code, label in (d.value_dict or {}).items():
                if label and len(str(label)) >= 2 and label in q:
                    seed["filter_hits"].append({"dimension": d.name, "value": code})
                    break

        # group_by hint: 在/按<dim>、各<dim>、每个<dim>、<dim>排名/排行
        # plus "前N的<dim>" or "<dim>前N" → both rank-on-dim
        dim_alias_re = []
        for d in self.semantic.list_dimensions():
            for alias in d.all_aliases():
                if alias and len(alias) <= 8:
                    dim_alias_re.append((alias, d.name))
        # sort longest first to prefer specific aliases
        dim_alias_re.sort(key=lambda x: -len(x[0]))
        for alias, dim_name in dim_alias_re:
            if dim_name in [g for g in seed["group_by_hint"]]:
                continue
            patterns = [
                rf"按\s*{re.escape(alias)}",
                rf"各\s*{re.escape(alias)}",
                rf"每个\s*{re.escape(alias)}",
                rf"每\s*{re.escape(alias)}",
                rf"按照\s*{re.escape(alias)}",
                rf"分\s*{re.escape(alias)}",
                rf"{re.escape(alias)}\s*排名",
                rf"{re.escape(alias)}\s*排行",
                rf"{re.escape(alias)}\s*维度",
                # 多轮追问的下钻/升维/降维动词：拆到/拆开/下钻/钻取/细分/细化/下沉到 <dim>
                rf"拆\s*(?:到|开|分|成)?\s*{re.escape(alias)}",
                rf"下\s*钻\s*到?\s*{re.escape(alias)}",
                rf"钻\s*取\s*到?\s*{re.escape(alias)}",
                rf"细\s*(?:分|化)\s*到?\s*{re.escape(alias)}",
                rf"下\s*沉\s*到?\s*{re.escape(alias)}",
                rf"上\s*卷\s*到?\s*{re.escape(alias)}",
                rf"到\s*{re.escape(alias)}\s*层级",
                rf"{re.escape(alias)}\s*层级",
                # 排前N的<dim>, 前N<dim>, TopN<dim>
                rf"(?:前|top)\s*\d{{1,3}}\s*(?:名)?\s*的?\s*{re.escape(alias)}",
                rf"前(?:一|二|三|四|五|六|七|八|九|十)\s*(?:名)?\s*的?\s*{re.escape(alias)}",
                # <dim>对比 / <dim>之间
                rf"{re.escape(alias)}\s*对比",
                rf"{re.escape(alias)}\s*之间",
            ]
            for pat in patterns:
                if re.search(pat, q, re.IGNORECASE):
                    seed["group_by_hint"].append(dim_name)
                    break
        # de-dup, keep order
        seen = set()
        uniq = []
        for d in seed["group_by_hint"]:
            if d not in seen:
                uniq.append(d); seen.add(d)
        seed["group_by_hint"] = uniq

        # metric_hits：问句里显式点名了哪些指标（"金额、目标、达成率"这类一句多指标）。
        # 取每个指标命中的最长别名长度，长别名优先（更具体），去重保序。
        ranked: list[tuple[int, str]] = []
        for m in self.semantic.list_metrics():
            best_len = 0
            for a in m.all_aliases():
                if a and len(a) >= 2 and a in q:
                    best_len = max(best_len, len(a))
            if best_len:
                ranked.append((best_len, m.name))
        ranked.sort(key=lambda x: -x[0])
        seen_m: set[str] = set()
        for _, name in ranked:
            if name not in seen_m:
                seed["metric_hits"].append(name)
                seen_m.add(name)

        # order_hint：显式排序方向（"按达成率从低到高排序"）。先确定方向词，再就近找指标别名。
        ASC_PAT = r"(从低到高|从小到大|由低到高|由小到大|升序|正序|低到高|小到大)"
        DESC_PAT = r"(从高到低|从大到小|由高到低|由大到小|降序|倒序|高到低|大到小)"
        order_dir = ""
        if re.search(ASC_PAT, q):
            order_dir = "asc"
        elif re.search(DESC_PAT, q):
            order_dir = "desc"
        if order_dir:
            dir_pat = ASC_PAT if order_dir == "asc" else DESC_PAT
            order_field = ""
            best_alias_len = 0
            for m in self.semantic.list_metrics():
                for a in m.all_aliases():
                    if not a or len(a) < 2 or a not in q:
                        continue
                    # 别名与方向词相邻（中间≤8个非标点字符）→ 认为是排序字段
                    if re.search(re.escape(a) + r"[^，。；、!？\n]{0,8}?" + dir_pat, q) and len(a) > best_alias_len:
                        order_field = m.name
                        best_alias_len = len(a)
            # 没就近匹配到具体指标，但问句确实命中了某个指标 → 用最具体的那个兜底
            if not order_field and seed["metric_hits"]:
                order_field = seed["metric_hits"][0]
            if order_field:
                seed["order_hint"] = {"field": order_field, "dir": order_dir}

        # having_hints：指标阈值过滤（"达成率低于90%""销售额超过100万"）。就近把算子绑定到指标：
        # 先看算子左侧 ≤12 字窗口，再看右侧（"低于90%的达成率"语序），都没有则留空交给校验期用主指标兜底。
        for hm in _THRESHOLD_RE.finditer(q):
            op = _THRESHOLD_OP_CANON.get(hm.group("op"))
            if not op:
                continue
            try:
                val = float(hm.group("num"))
            except (TypeError, ValueError):
                continue
            unit = hm.group("unit") or ""
            if unit == "万":
                val *= 1e4
            elif unit == "亿":
                val *= 1e8
            left = q[max(0, hm.start() - 12):hm.start()]
            right = q[hm.end():hm.end() + 12]
            metric_name = self._metric_name_in_text(left) or self._metric_name_in_text(right)
            seed["having_hints"].append({
                "metric": metric_name,
                "op": op,
                "value": val,
                "is_percent": bool(hm.group("pct")),  # 是否带 % 号；裸数的百分比归一在校验期按指标口径判定
                "raw": hm.group(0),
            })
        return seed

    def _rule_only_plan(self, question: str, bundle: RetrievalBundle, rule_seed: dict[str, Any], today: date) -> dict[str, Any]:
        metric = bundle.metrics[0].name if bundle.metrics else ""
        return {
            "metric": metric,
            "table": (self.semantic.metric(metric).table if metric and self.semantic.metric(metric) else ""),
            "group_by": list(rule_seed.get("group_by_hint") or []),
            "filters": [{"dimension": h["dimension"], "op": "eq", "values": [h["value"]]} for h in rule_seed.get("filter_hits", [])],
            "time_range": {
                "kind": ("absolute" if rule_seed.get("absolute") else ("relative" if rule_seed.get("period") else "none")),
                "period": rule_seed.get("period") or "",
                "n": rule_seed.get("n") or 0,
                "year": (rule_seed.get("absolute") or {}).get("year") or "",
                "months": list((rule_seed.get("absolute") or {}).get("months") or []),
            },
            "calculation": rule_seed.get("calculation") or "",
            "order_by": [],
            "limit": rule_seed.get("rank_n") or 0,
            "needs_clarify": not metric,
            "clarify_reason": "" if metric else "无法识别指标，请补充关键词（销售/达成率/新客等）",
            "clarify_options": self._build_clarify_options_from_bundle(bundle) if not metric else [],
            "confidence": 0.55 if metric else 0.0,
            "reasoning": "规则兜底生成（LLM 不可用或被旁路）",
        }

    # --------------------------------------------------------- validation

    def _validate_and_repair(
        self,
        plan: QueryPlan,
        bundle: RetrievalBundle,
        rule_seed: dict[str, Any],
        *,
        today: date,
        previous_plan: QueryPlan | None = None,
        followup: bool = False,
        question: str = "",
        allowed_tables: "frozenset[str] | None" = None,
    ) -> QueryPlan:
        inherit = bool(followup and previous_plan and previous_plan.metric)

        # 0. 多轮继承（确定性兜底）——"携带状态，只叠加显式增量"。
        #    即便 LLM/召回完全无视多轮规则，这一步也能把指标/表/时间纠回上一轮，
        #    彻底杜绝"裸追问串到别的表/月份"。
        if inherit:
            # 分域：换指标只允许换到用户自己的表上，避免"换"出范围再被权限拦截
            explicit = self._explicit_metric_in_question(question, allowed_tables=allowed_tables)
            prev_md = self.semantic.metric(previous_plan.metric)
            expl_md = self.semantic.metric(explicit.name) if explicit else None
            # 跨表显式换指标 = 主动换主题 → 不继承（让本轮 plan 自立）。
            switching_table = bool(expl_md and prev_md and expl_md.table != prev_md.table)
            # 同表显式点了"另一个指标"（上一轮看目标、这轮追问达成率）：
            # 切换主指标到新指标，但仍继承上一轮口径（时间/维度/筛选），上一轮指标降为附带列。
            # 修复：旧逻辑只要不跨表就强行 plan.metric=上一轮，导致"我持续追问要达成率"被无视。
            explicit_diff_same_table = bool(
                expl_md and prev_md and expl_md.table == prev_md.table
                and expl_md.name != previous_plan.metric
            )

            def _inherit_context(keep_metric: bool, new_primary: str = "") -> None:
                if keep_metric:
                    plan.metric = previous_plan.metric
                    if not plan.extra_metrics:
                        plan.extra_metrics = list(previous_plan.extra_metrics)
                else:
                    plan.metric = new_primary
                    carried = [previous_plan.metric, *previous_plan.extra_metrics, *plan.extra_metrics]
                    plan.extra_metrics = list(dict.fromkeys(m for m in carried if m and m != new_primary))
                plan.group_by = list(previous_plan.group_by) + [
                    g for g in plan.group_by if g not in previous_plan.group_by
                ]
                merged: dict[str, PlanFilter] = {f.dimension: f for f in previous_plan.filters}
                for f in plan.filters:
                    if f.dimension:
                        merged[f.dimension] = f
                plan.filters = list(merged.values())
                if not plan.calculation and not rule_seed.get("calculation"):
                    plan.calculation = previous_plan.calculation

            if switching_table:
                pass  # 不继承
            elif explicit_diff_same_table:
                _inherit_context(keep_metric=False, new_primary=expl_md.name)
            else:
                _inherit_context(keep_metric=True)

        # 1. metric must exist
        metric_def = self.semantic.metric(plan.metric)
        if not metric_def and inherit and previous_plan.metric:
            plan.metric = previous_plan.metric
            metric_def = self.semantic.metric(plan.metric)
        if not metric_def and bundle.metrics:
            plan.metric = bundle.metrics[0].name
            metric_def = self.semantic.metric(plan.metric)
        if not metric_def:
            plan.needs_clarify = True
            plan.clarify_reason = "无法确定要查询的业务指标，请补充关键词"
            plan.clarify_options = self._build_clarify_options_from_bundle(bundle)
            return plan

        # 2. table comes from metric
        plan.table = metric_def.table

        # 2b. 一句多指标（"各大区门店销售金额、门店销售目标和销售达成率"）。
        #     结构化只能单表聚合 → 选能同时容纳最多指标的表，主指标优先取"排序所指"的那个，
        #     其余作为附带列一起 SELECT。仅在非多轮继承时整体改写（追问已在 step 0 处理）。
        #     这是把"LLM 漏选 extra_metrics / 选错表"彻底补齐的确定性兜底。
        metric_hits = list(rule_seed.get("metric_hits") or [])
        if len(metric_hits) >= 2 and not inherit:
            best_t, mapped = self._best_table_for_metrics(metric_hits)
            if best_t and len(mapped) >= 2:
                order_field = (rule_seed.get("order_hint") or {}).get("field") or ""
                primary = self._equivalent_metric_on_table(order_field, best_t) if order_field else None
                if not primary or primary not in mapped:
                    cur = self._equivalent_metric_on_table(plan.metric, best_t)
                    primary = cur if (cur and cur in mapped) else mapped[0]
                plan.table = best_t
                plan.metric = primary
                plan.extra_metrics = [m for m in mapped if m != primary]
                metric_def = self.semantic.metric(plan.metric) or metric_def
                logger.info(
                    "plan.multi_metric table=%s primary=%s extras=%s (hits=%s)",
                    best_t, primary, plan.extra_metrics, metric_hits,
                )

        # 3a. inject group_by from rule hints (the LLM often misses these)
        for dim_hint in rule_seed.get("group_by_hint") or []:
            if dim_hint not in plan.group_by:
                plan.group_by.append(dim_hint)

        # 3b. 维度对齐到当前表（如 渠道→大系统渠道），不可对齐则丢弃；保序去重
        remapped_gb: list[str] = []
        for d in plan.group_by:
            rd = self._remap_dim(d, plan.table)
            if rd and rd not in remapped_gb:
                remapped_gb.append(rd)
        plan.group_by = remapped_gb

        # 4. filters：补齐规则命中 → 维度对齐 → 清洗去重
        existing_filter_dims = {f.dimension for f in plan.filters}
        for hit in rule_seed.get("filter_hits") or []:
            if hit["dimension"] not in existing_filter_dims:
                plan.filters.append(PlanFilter(dimension=hit["dimension"], op="eq", values=[hit["value"]], raw=hit["value"]))
        clean_filters: list[PlanFilter] = []
        seen_fdims: set[str] = set()
        for f in plan.filters:
            if not f.dimension or not f.values:
                continue
            rd = self._remap_dim(f.dimension, plan.table)
            if not rd or rd in seen_fdims:
                continue
            f.dimension = rd
            f.op = (f.op or "eq").lower()
            if f.op not in ("eq", "in", "like"):
                f.op = "eq"
            clean_filters.append(f)
            seen_fdims.add(rd)
        plan.filters = clean_filters

        # 4b. 被单值等值过滤锁定的维度，没必要再 group by
        #     （"只看东一区 + 拆到省区" → 只按省区，不再按大区）
        single_eq = {f.dimension for f in plan.filters if f.op == "eq" and len(f.values) == 1}
        if single_eq:
            trimmed = [g for g in plan.group_by if g not in single_eq]
            if trimmed:
                plan.group_by = trimmed

        # 5. extra metrics: must be on same table
        plan.extra_metrics = [
            m for m in plan.extra_metrics
            if self.semantic.metric(m) and self.semantic.metric(m).table == plan.table  # type: ignore
        ]

        # 6pre. 显式排序方向（"按达成率从低到高排序"）→ 覆盖 order_by。
        #       只允许排到"已 SELECT 出来的列"（主指标 / 附带指标 / 分组维度），否则
        #       compiler 会 ORDER BY 一个不存在的列别名导致 SQL 报错。
        oh = rule_seed.get("order_hint")
        if oh and oh.get("field"):
            of = self._equivalent_metric_on_table(oh["field"], plan.table) or oh["field"]
            selected_aliases = {plan.metric, *plan.extra_metrics, *plan.group_by}
            if of in selected_aliases:
                plan.order_by = [OrderBy(field=of, dir=(oh.get("dir") or "desc"))]

        # 6. order by — for rank we always sort by the metric desc
        clean_orders: list[OrderBy] = []
        for o in plan.order_by:
            if o.field == plan.metric or self.semantic.metric(o.field):
                clean_orders.append(OrderBy(field=o.field or plan.metric, dir=o.dir or "desc"))
            elif self._dim_valid(o.field, plan.table):
                clean_orders.append(OrderBy(field=o.field, dir=o.dir or "asc"))
        plan.order_by = clean_orders
        if (not plan.order_by and inherit and previous_plan.order_by
                and not rule_seed.get("calculation") and not plan.calculation):
            plan.order_by = [
                OrderBy(field=o.field, dir=o.dir) for o in previous_plan.order_by
                if o.field == plan.metric or self.semantic.metric(o.field) or self._dim_valid(o.field, plan.table)
            ]
        if plan.calculation == "rank" and not plan.order_by:
            plan.order_by = [OrderBy(field=plan.metric, dir="desc" if metric_def.higher_is_better else "asc")]

        # 6b. HAVING：把"指标阈值"过滤（达成率低于90% / 销售额超过100万）落成 plan.having。
        #     - 指标解析到当前表（达成率 → 当前 target 表上的达成率指标）；解析不到→用主指标兜底；
        #     - 百分比指标做 %→比率 归一（90% / 90 → 0.9；120% → 1.2；0.9 原样）；
        #     - 用聚合表达式编译，过滤的指标若未被 SELECT 则补进 extra_metrics，让用户看见所筛列。
        valid_ops = {"lt", "lte", "gt", "gte", "eq", "ne"}

        def _resolve_having_metric(token: str) -> str:
            if not token:
                return ""
            # already a logical key on some table?
            base = token if self.semantic.metric(token) else self._metric_name_in_text(token)
            if not base:
                return ""
            mapped = self._equivalent_metric_on_table(base, plan.table)
            if mapped:
                return mapped
            md = self.semantic.metric(base)
            return base if (md and md.table == plan.table) else ""

        collected: list[HavingFilter] = []

        def _collect_having(token: str, op: str, value: Any, raw: str, is_percent: bool | None) -> None:
            op = (op or "lt").lower()
            if op not in valid_ops:
                return
            mname = _resolve_having_metric(token) or (plan.metric if self.semantic.metric(plan.metric) else "")
            md = self.semantic.metric(mname) if mname else None
            if not md or md.table != plan.table:
                return
            try:
                val = float(value)
            except (TypeError, ValueError):
                return
            is_pct_metric = (md.display_format == "percent") or (md.unit == "%")
            if is_pct_metric and (is_percent or val >= 2):
                # "90%"/"90"/"120" → 比率；已是比率（0.9/1.2，<2 且无%）则原样
                val = val / 100.0
            collected.append(HavingFilter(metric=mname, op=op, value=val, raw=str(raw or "")))

        for h in plan.having:  # LLM 直接给的 having（若有）
            _collect_having(h.metric, h.op, h.value, h.raw, None)
        for hint in (rule_seed.get("having_hints") or []):  # 规则命中（百分比口径更可靠，最后覆盖）
            _collect_having(hint.get("metric"), hint.get("op"), hint.get("value"), hint.get("raw"), hint.get("is_percent"))

        dedup_h: dict[tuple[str, str], HavingFilter] = {}
        for h in collected:
            dedup_h[(h.metric, h.op)] = h
        plan.having = list(dedup_h.values())
        for h in plan.having:
            if h.metric != plan.metric and h.metric not in plan.extra_metrics:
                plan.extra_metrics.append(h.metric)

        # 7. calculation override from rule (if rule found it but LLM missed)
        if not plan.calculation and rule_seed.get("calculation"):
            plan.calculation = rule_seed["calculation"]

        # 7b. limit 继承（上一轮是 TopN，本轮没给新的 TopN）
        if not plan.limit and inherit and previous_plan.limit and not rule_seed.get("rank_n"):
            plan.limit = previous_plan.limit

        # 8. time range — apply rules / defaults / 多轮继承
        plan.time_range = self._apply_time_defaults(
            plan.time_range, plan.calculation, rule_seed, today=today,
            previous_plan=previous_plan, followup=inherit,
        )

        # 9. calc → rank infers limit
        if plan.calculation == "rank" and not plan.limit:
            plan.limit = rule_seed.get("rank_n") or 10

        # 10. low-confidence => clarify ONLY if metric is unambiguous-bad
        # (we are accuracy-first, but "宁可澄清也不能答错" → never clarify when metric+group_by+filter exist)
        has_signal = bool(plan.filters) or bool(plan.group_by) or bool(plan.calculation) or bool(plan.having)
        plan_actionable = bool(plan.metric) and has_signal
        if plan.confidence and plan.confidence < 0.3 and not has_signal:
            plan.needs_clarify = True
            if not plan.clarify_reason:
                plan.clarify_reason = "问题信息较少，请确认想看的维度（如大区/产品系列/段位）"
            if not plan.clarify_options:
                plan.clarify_options = self._build_clarify_options_from_bundle(bundle)
        elif plan.needs_clarify and plan_actionable:
            # 结构充分时 LLM 仍说要澄清 → 信结构，强制执行
            # 触发场景：不同 LLM (百炼/飞鹤 kaier_znws/...) 对衍生表达式（diff/ratio/绝对值）
            # 排序的容忍度不同——飞鹤侧会主动 needs_clarify=true，百炼侧不会。compiler 已经会把
            # extra_metrics 一并 SELECT 出来，answerer 在 narrative 阶段可以兜底计算差值/比率，
            # 因此结构上 metric + (filters|group_by|calculation) 已足够，不应再回弹给用户。
            logger.info(
                "plan.override_llm_clarify metric=%s table=%s group_by=%s extra=%s "
                "calc=%s conf=%s reason=%r",
                plan.metric, plan.table, plan.group_by, plan.extra_metrics,
                plan.calculation, plan.confidence, plan.clarify_reason,
            )
            plan.needs_clarify = False
            plan.clarify_reason = ""
            plan.clarify_options = []
        else:
            plan.needs_clarify = bool(plan.needs_clarify)

        # 10b. P1.5 歧义澄清（确定性，不依赖 LLM 自觉）：top-2 指标分数咬太近且
        #      用户没显式点名 → 不硬选，抛给用户点选（点选记录又是免费标注数据）。
        plan = self._maybe_ambiguity_clarify(plan, bundle, question, inherit)

        # trace：是否追问 / 继承结果 / 表是否切换及原因（便于排查"串表串口径"）
        try:
            if inherit and previous_plan:
                pm = self.semantic.metric(previous_plan.metric)
                switch_reason = (
                    "none" if (pm and pm.table == plan.table)
                    else f"metric_or_topic_change->{plan.table}"
                )
                logger.info(
                    "plan.trace followup=1 metric=%s table=%s group_by=%s "
                    "filters=%s time=%s calc=%s limit=%s switch_reason=%s",
                    plan.metric, plan.table, plan.group_by,
                    [f.dimension for f in plan.filters],
                    plan.time_range.kind, plan.calculation, plan.limit, switch_reason,
                )
            else:
                logger.info(
                    "plan.trace followup=0 metric=%s table=%s group_by=%s time=%s calc=%s",
                    plan.metric, plan.table, plan.group_by, plan.time_range.kind, plan.calculation,
                )
        except Exception:
            pass

        return plan

    def _maybe_ambiguity_clarify(
        self,
        plan: QueryPlan,
        bundle: RetrievalBundle,
        question: str,
        inherit: bool,
    ) -> QueryPlan:
        """P1.5：top-2 指标候选区分度不足时强制澄清。

        触发条件（全部满足）：
          · DATACHAT_AMBIGUITY_GAP > 0（默认 0.10；设 0 关闭）；
          · 非多轮继承、planner 未主动澄清、未超范围；
          · plan 选中的就是 top-1 或 top-2（说明模型也在两者之间摇摆）；
          · top-1/top-2 分差 < gap 且都有正分；
          · 问句没有显式点名所选指标的任何别名（点名 = 用户已经做过选择）。
        """
        try:
            gap = float(os.environ.get("DATACHAT_AMBIGUITY_GAP", "0.10") or 0)
        except ValueError:
            gap = 0.10
        if gap <= 0 or inherit or plan.needs_clarify or plan.out_of_scope:
            return plan
        ms = bundle.metrics
        if len(ms) < 2:
            return plan
        top1, top2 = ms[0], ms[1]
        if (
            top1.name == top2.name
            or plan.metric not in (top1.name, top2.name)
            or top1.score <= 0 or top2.score <= 0
            or (top1.score - top2.score) >= gap
        ):
            return plan
        chosen = self.semantic.metric(plan.metric)
        if chosen:
            q = question or ""
            for a in chosen.all_aliases():
                if a and len(a) >= 2 and a in q:
                    return plan
        opts: list[dict[str, Any]] = []
        for c in (top1, top2):
            md = self.semantic.metric(c.name)
            tdef = self.semantic.table(md.table) if md else None
            hint = " · ".join(x for x in (
                (tdef.label if tdef else (md.table if md else "")),
                (md.unit if md else ""),
            ) if x)
            opts.append({"type": "metric", "key": c.name, "label": c.label, "hint": hint})
        plan.needs_clarify = True
        plan.clarify_reason = (
            f"您的问题同时匹配多个相近口径（{top1.label} / {top2.label}），为避免答错请选择其一"
        )
        plan.clarify_options = opts
        logger.info(
            "plan.ambiguity_clarify q=%r top1=%s(%.3f) top2=%s(%.3f) chosen=%s",
            (question or "")[:60], top1.name, top1.score, top2.name, top2.score, plan.metric,
        )
        return plan

    def _dim_valid(self, dim_name: str, table: str) -> bool:
        d = self.semantic.dimension(dim_name)
        if not d:
            return False
        return table in d.table_columns

    @staticmethod
    def _norm_expr(expr: str) -> str:
        return "".join((expr or "").lower().split())

    def _equivalent_metric_on_table(self, metric_name: str, table: str) -> str | None:
        """把一个指标对齐到目标表：本就在该表→原样；否则找该表上**表达式等价**的指标。

        关键场景：'门店销售金额' 命中 shop_sale_amount_total（明细表），但用户同时要
        目标/达成率（都在 target 表）。target 表上的 shop_sale_amount_actual_total 表达式
        同为 SUM(shop_sale_amount)，于是金额可以无损地落到 target 表，三个指标同表一次查出。
        """
        md = self.semantic.metric(metric_name)
        if not md:
            return None
        if md.table == table:
            return metric_name
        target_expr = self._norm_expr(md.expression)
        for m in self.semantic.list_metrics():
            if m.table == table and self._norm_expr(m.expression) == target_expr:
                return m.name
        return None

    def _best_table_for_metrics(self, names: list[str]) -> tuple[str | None, list[str]]:
        """给定一组显式点名的指标，挑出能同时容纳最多指标的单张表（结构化只能单表聚合）。
        返回 (table, 落到该表上的指标名列表，保序去重)。"""
        cand_tables: list[str] = []
        for n in names:
            md = self.semantic.metric(n)
            if md and md.table not in cand_tables:
                cand_tables.append(md.table)
        best_t: str | None = None
        best_mapped: list[str] = []
        for t in cand_tables:
            mapped: list[str] = []
            for n in names:
                em = self._equivalent_metric_on_table(n, t)
                if em and em not in mapped:
                    mapped.append(em)
            if len(mapped) > len(best_mapped):
                best_t, best_mapped = t, mapped
        return best_t, best_mapped

    def _apply_time_defaults(
        self,
        tr: TimeRange,
        calculation: str,
        rule_seed: dict[str, Any],
        *,
        today: date,
        previous_plan: QueryPlan | None = None,
        followup: bool = False,
    ) -> TimeRange:
        # 多轮继承：本句完全没有时间口径 → 沿用上一轮时间窗，
        # 绝不因为"没提时间"就回退到最新月（这正是串到 2026-04 的根因）。
        current_has_time = bool(
            tr.kind != TimeKind.NONE or rule_seed.get("period") or rule_seed.get("absolute")
        )
        if followup and previous_plan and not current_has_time:
            ptr = previous_plan.time_range
            if ptr and ptr.kind != TimeKind.NONE:
                return TimeRange(
                    kind=ptr.kind, period=ptr.period, n=ptr.n,
                    year=ptr.year, months=list(ptr.months),
                    start_ym=ptr.start_ym, end_ym=ptr.end_ym,
                )

        # If LLM left it empty but rule extracted a period, use rule's period
        if tr.kind == TimeKind.NONE and rule_seed.get("period"):
            tr.kind = TimeKind.RELATIVE
            tr.period = rule_seed["period"]
            tr.n = rule_seed.get("n") or 0
        if tr.kind == TimeKind.NONE and rule_seed.get("absolute"):
            tr.kind = TimeKind.ABSOLUTE
            tr.year = rule_seed["absolute"].get("year") or ""
            tr.months = list(rule_seed["absolute"].get("months") or [])

        # Clamp to data range
        latest = self.semantic.data_range_latest or f"{today.year}-{today.month:02d}"
        latest_year, latest_month = (latest.split("-") + ["12"])[:2]
        if tr.kind == TimeKind.NONE:
            tr.kind = TimeKind.RELATIVE
            tr.period = "this_month"

        if tr.kind == TimeKind.RELATIVE and not tr.period:
            tr.period = "this_month"

        # If user used absolute year not in dataset, fall back to latest
        if tr.kind == TimeKind.ABSOLUTE and tr.year and not tr.year.isdigit():
            tr.year = latest_year

        return tr

    def _build_clarify_options_from_bundle(self, bundle: RetrievalBundle) -> list[dict[str, Any]]:
        opts: list[dict[str, Any]] = []
        for c in bundle.metrics[:3]:
            opts.append({"type": "metric", "key": c.name, "label": c.label, "hint": c.payload.get("unit") or ""})
        for c in bundle.dimensions[:3]:
            opts.append({"type": "dimension", "key": c.name, "label": c.label, "hint": ",".join(c.payload.get("sample_values") or [])[:60]})
        return opts
