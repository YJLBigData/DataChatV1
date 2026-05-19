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
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.core.cache import get_cache
from app.core.llm.router import LLMRouter, get_llm_router
from app.core.retrieval import HybridRetriever, RetrievalBundle, RetrievalCandidate
from app.core.semantic import SemanticLayer

from .plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange

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
    "差异": "delta",
    "差额": "delta",
    "相差": "delta",
    "累计": "cumulative",
    "累积": "cumulative",
}

# 两指标差异关键词。命中即认定"差异类问题"（被减数−减数），
# 该意图比"前N/排名"更具体：'差异最大的前10'本质是按差异排序取TopN，
# 故 diff 关键词存在时强制 calculation=delta，rank_n 仍单独解析进 limit。
DIFF_KEYWORDS: tuple[str, ...] = ("差异", "差值", "差距", "差额", "相差")

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

    def _explicit_metric_in_question(self, question: str):
        """问句中是否显式点名了某个指标（按别名最长匹配）。用于判断用户是否主动换指标。"""
        q = question or ""
        best = None
        for m in self.semantic.list_metrics():
            for a in m.all_aliases():
                if a and len(a) >= 2 and a in q:
                    if best is None or len(a) > best[1]:
                        best = (m, len(a))
        return best[0] if best else None

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

    # -------------------------------------------------------------- main

    def plan(
        self,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        previous_plan: QueryPlan | None = None,
        today: date | None = None,
    ) -> PlanResult:
        started = time.perf_counter()
        followup = self._looks_like_followup(question, previous_plan)

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

        bundle = self.retriever.search(search_q)
        if followup and previous_plan:
            self._augment_bundle_with_previous(bundle, previous_plan)
        rule_seed = self._extract_rule_seed(question, today=today)

        # Try cache first —— key 必须含上一轮上下文 + followup，否则同一句追问在不同
        # 上下文下会命中同一缓存（这是历史串话的根因之一）。
        cache_key = json.dumps(
            {
                "q": question,
                "h": [m.get("content", "") for m in (history or [])][-3:],
                "today": (today or date.today()).isoformat(),
                "rule": rule_seed,
                "prev": (previous_plan.signature() if (previous_plan and previous_plan.metric) else ""),
                "followup": followup,
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

        prompt = self._build_prompt(question, bundle, history=history, previous_plan=previous_plan, rule_seed=rule_seed, today=today or date.today(), followup=followup)
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
    ) -> dict[str, str]:
        metric_block = "\n".join(
            f"- key={c.name} | label={c.label} | unit={c.payload.get('unit','')} | table={c.payload.get('table','')} | score={c.score:.3f}"
            for c in bundle.metrics
        ) or "- (无召回)"

        dim_block = "\n".join(
            f"- key={c.name} | label={c.label} | sample={','.join(c.payload.get('sample_values') or [])[:80]} | score={c.score:.3f}"
            for c in bundle.dimensions
        ) or "- (无召回)"

        table_block = "\n".join(
            f"- key={c.name} | label={c.label} | grain={c.payload.get('grain','')}"
            for c in bundle.tables
        ) or "- (无召回)"

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
                "只允许按用户这句话的显式增量改动 group_by / filters / order_by / limit。"
                "除非用户这句话本身明确点了**另一个业务指标**或**另一个时间口径**，否则一律不得更换指标/表/时间。"
                "严禁因为本句没提销售口径就回退到默认销售额或最新月份——那会答错。\n"
            )

        system = (
            "你是飞鹤公司的智能问数规划器。任务：把高管的中文问题翻译为受控的 QueryPlan JSON。"
            "你不能编造任何不在候选集中的指标、维度、表。如果问题模糊（例如缺少必要维度筛选、口径冲突），"
            f"请把 needs_clarify 设为 true 并给出 clarify_options。今天是 {today.isoformat()}，"
            f"数据库覆盖范围：{self.semantic.data_range_earliest} ~ {self.semantic.data_range_latest}。"
            f"{followup_rule}"
            "口径要求：1) 仅当问句明确提到『销售额』且无上一轮可继承口径时，销售额才默认 "
            "terminal_sale_amount_total；2) 涉及'达成率/目标完成'必须用 target 表的指标；"
            "3) 用户提到'同比/环比/占比/排名/趋势'必须填到 calculation 字段；"
            "4) 两个指标的『差异/差值/差距/差额』是受支持的计算：calculation 填 \"delta\"，"
            "metric=被减数、extra_metrics=[减数]（两者必须同表），系统会自动算出差异列并按差异排序；"
            "『差异最大的前N』把 N 填到 limit。此类问题信息完整时一律 needs_clarify=false，不要因"
            "“无法按衍生指标排序”而澄清——系统已支持。"
        )

        user = (
            f"用户问题：{question}\n\n"
            f"历史对话（最近 4 条，可能用于多轮继承）：\n{history_block or '(无)'}\n"
            f"{prev_plan_block}\n"
            f"---\n候选指标 metrics（按相关度排序）：\n{metric_block}\n\n"
            f"候选维度 dimensions：\n{dim_block}\n\n"
            f"候选数据表 tables：\n{table_block}\n\n"
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
        # 两指标差异意图优先级高于 rank：'差异最大的前10' 本质是按差异排序取 TopN，
        # 故强制 calculation=delta，rank_n 已单独解析进 limit，不丢失"前N"。
        if any(k in q for k in DIFF_KEYWORDS):
            seed["calculation"] = "delta"
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
    ) -> QueryPlan:
        inherit = bool(followup and previous_plan and previous_plan.metric)

        # 0. 多轮继承（确定性兜底）——"携带状态，只叠加显式增量"。
        #    即便 LLM/召回完全无视多轮规则，这一步也能把指标/表/时间纠回上一轮，
        #    彻底杜绝"裸追问串到别的表/月份"。
        if inherit:
            explicit = self._explicit_metric_in_question(question)
            prev_md = self.semantic.metric(previous_plan.metric)
            expl_md = self.semantic.metric(explicit.name) if explicit else None
            # 只有用户显式点了"另一张表的指标"才算主动换主题，否则一律继承上一轮
            switching = bool(expl_md and prev_md and expl_md.table != prev_md.table)
            if not switching:
                plan.metric = previous_plan.metric
                if not plan.extra_metrics:
                    plan.extra_metrics = list(previous_plan.extra_metrics)
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

        # 5b. 两指标差异（delta）——把"差异类问题"做实，杜绝被迫澄清。
        #     被减数=metric，减数=同表第二个指标（优先 extra_metrics，其次召回集）。
        #     只要凑齐两个同表指标即可执行；'差异最大的前N'=按差异降序取 TopN。
        diff_intent = (
            plan.calculation == "delta"
            or rule_seed.get("calculation") == "delta"
            or any(k in question for k in DIFF_KEYWORDS)
        )
        if diff_intent:
            second = next(
                (m for m in plan.extra_metrics
                 if m != plan.metric and self.semantic.metric(m)
                 and self.semantic.metric(m).table == plan.table),  # type: ignore
                "",
            )
            if not second:
                for c in bundle.metrics:
                    md = self.semantic.metric(c.name)
                    if md and md.name != plan.metric and md.table == plan.table:
                        second = md.name
                        break
            if second:
                plan.calculation = "delta"
                plan.extra_metrics = [second]
                asc = any(t in question for t in ("最小", "最低", "升序", "从小到大", "由小到大"))
                plan.order_by = [OrderBy(field="metric_diff", dir="asc" if asc else "desc")]
                if not plan.limit and rule_seed.get("rank_n"):
                    plan.limit = rule_seed["rank_n"]
                # 差异已可执行 → 收回 LLM 误置的澄清，既不答错也不无谓澄清
                plan.needs_clarify = False
                plan.clarify_reason = ""
                plan.clarify_options = []

        # 6. order by — for rank we always sort by the metric desc
        clean_orders: list[OrderBy] = []
        for o in plan.order_by:
            if o.field == "metric_diff" and plan.calculation == "delta":
                clean_orders.append(OrderBy(field="metric_diff", dir=o.dir or "desc"))
            elif o.field == plan.metric or self.semantic.metric(o.field):
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

        # 9. calc → rank infers limit；delta 仅当问句给了"前N"才限行，否则按默认上限
        if plan.calculation == "rank" and not plan.limit:
            plan.limit = rule_seed.get("rank_n") or 10
        if plan.calculation == "delta" and not plan.limit and rule_seed.get("rank_n"):
            plan.limit = rule_seed["rank_n"]

        # 10. low-confidence => clarify ONLY if metric is unambiguous-bad
        # (we are accuracy-first, but "宁可澄清也不能答错" → never clarify when metric+group_by+filter exist)
        has_signal = bool(plan.filters) or bool(plan.group_by) or bool(plan.calculation)
        if plan.confidence and plan.confidence < 0.3 and not has_signal:
            plan.needs_clarify = True
            if not plan.clarify_reason:
                plan.clarify_reason = "问题信息较少，请确认想看的维度（如大区/产品系列/段位）"
            if not plan.clarify_options:
                plan.clarify_options = self._build_clarify_options_from_bundle(bundle)
        else:
            plan.needs_clarify = bool(plan.needs_clarify)

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

    def _dim_valid(self, dim_name: str, table: str) -> bool:
        d = self.semantic.dimension(dim_name)
        if not d:
            return False
        return table in d.table_columns

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
