"""专家团编排引擎 —— 决策调度总监(卓见全) + 多专家协同 + 报告合成。

复刻 feihe-decision 的 6-Phase 编排，落到 DataChatV1 现有基建上：
  · 模型：复用 app.core.llm.router（与问数同一套，含右上角 provider 切换）；
  · 数据：复用 app.core.orchestrator 数据问数流水线（同一套 DB / 语义层 / 权限）；
  · 知识：语义层(知识库)结构化口径 + definitions/knowledge 分析方法论。

流程：
  1) 总监路由：判断 fast / slow，从【可用专家集】里挑要调度的专家 + 子任务 + 取数需求；
  2) 专家执行：按需调用数据流水线取真实数字，再用专家 persona+skill 方法论给出分析；
  3) 报告合成：总监综合各专家产出，按汇报对象出结论。
单专家/快通道时跳过合成，直接返回。全程 best-effort：数据/某专家失败不阻断整体。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .registry import ExpertDef, ExpertTeamRegistry, get_registry

logger = logging.getLogger("datachat.expert_team")

EventSink = Optional[Callable[[str, dict[str, Any]], None]]


@dataclass
class _Participant:
    """一次运行里参与的专家：内置 ExpertDef 或用户自建 skill（统一成同一接口）。"""
    id: str
    name: str
    profession: str
    emoji: str
    persona: str
    skill_ids: list[str] = field(default_factory=list)
    is_builtin: bool = True


class ExpertTeamOrchestrator:
    def __init__(self, registry: ExpertTeamRegistry | None = None, llm: Any = None, data_pipe: Any = None):
        self.registry = registry or get_registry()
        self._llm = llm
        self.data_pipe = data_pipe

    @property
    def llm(self):
        if self._llm is None:
            from app.core.llm.router import get_llm_router
            self._llm = get_llm_router()
        return self._llm

    # ------------------------------------------------------------------ run

    def run(
        self,
        question: str,
        *,
        user_id: str = "default",
        is_admin: bool = False,
        selected_expert_ids: list[str] | None = None,
        user_skills: list[dict[str, Any]] | None = None,
        overrides: dict[str, dict[str, Any]] | None = None,
        want_report: bool = False,
        history: list[dict[str, str]] | None = None,
        on_event: EventSink = None,
        max_experts: int = 4,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        question = (question or "").strip()

        def emit(stage: str, payload: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(stage, payload)
                except Exception:  # noqa: BLE001
                    pass

        if not question:
            return {"ok": False, "error": "请输入问题。", "report": "请输入问题。", "experts": [], "plan": "", "route": ""}

        # 可用专家池 = 内置 worker（应用用户覆盖、剔除隐藏）+ 用户自建 skill；
        # 按用户勾选过滤（空 = 全部，由总监自主挑）
        pool = self._build_pool(user_skills, overrides)
        selected = set(selected_expert_ids or [])
        candidates = [p for p in pool if (not selected or p.id in selected)]
        if not candidates:
            candidates = pool  # 勾选项都失效时回退全部

        director = self.registry.director()

        # ---- Phase 1-2：总监路由 ----
        emit("director", {"status": "routing", "msg": "决策调度总监正在理解问题、规划调度…"})
        routing = self._route(question, candidates, director, want_report=want_report, history=history)
        route = routing.get("route") or "slow"
        plan = routing.get("plan") or ""
        chosen = routing.get("experts") or []
        chosen = chosen[:max_experts]
        emit("director", {"status": "planned", "route": route, "plan": plan,
                          "experts": [c.get("id") for c in chosen]})

        # ---- Phase 3-4：专家执行 ----
        results: list[dict[str, Any]] = []
        warnings: list[str] = []
        by_id = {p.id: p for p in pool}
        for item in chosen:
            eid = item.get("id")
            p = by_id.get(eid)
            if not p:
                continue
            subtask = item.get("subtask") or question
            data_query = (item.get("data_query") or "").strip()
            emit("expert", {"status": "start", "id": p.id, "name": p.name,
                            "profession": p.profession, "emoji": p.emoji, "subtask": subtask})
            data_block = ""
            data_meta: dict[str, Any] | None = None
            if data_query:
                data_meta = self._fetch_data(data_query, user_id=user_id, is_admin=is_admin)
                data_block = data_meta.get("for_prompt", "")
                if not data_meta.get("ok"):
                    warnings.append(f"{p.name}：取数未成功，已转为定性分析")
                emit("expert", {"status": "data", "id": p.id,
                                "rows": data_meta.get("rows"), "sql": (data_meta.get("sql") or "")[:200]})
            analysis, ok = self._run_expert(p, question, subtask, data_block)
            if not ok:
                warnings.append(f"{p.name}：分析未成功")
            res = {
                "id": p.id, "name": p.name, "profession": p.profession, "emoji": p.emoji,
                "subtask": subtask, "analysis": analysis, "ok": ok,
            }
            if data_meta:
                res["data"] = {k: data_meta.get(k) for k in ("narrative", "sql", "rows", "table_preview")}
            results.append(res)
            emit("expert", {"status": "done", "id": p.id, "name": p.name, "ok": ok})

        successful = [r for r in results if r.get("ok")]

        # ---- 失败门：没调度到专家 / 全部专家失败 → 明确报失败，绝不把失败文本当成功答案 ----
        if not results:
            emit("director", {"status": "error"})
            return {
                "ok": False, "error": "未能调度到合适的专家，请补充问题或更换/勾选其它专家。",
                "route": route, "plan": plan, "experts": results, "report": "",
                "warnings": warnings, "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        if not successful:
            emit("director", {"status": "error"})
            return {
                "ok": False, "error": "本次专家分析未成功（模型或数据服务暂时不可用），请稍后重试。",
                "route": route, "plan": plan, "experts": results, "report": "",
                "warnings": warnings, "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

        # ---- Phase 6：合成（多专家 / 要报告时）；单专家快通道直接用其产出 ----
        synth_ok = True
        if route == "fast" and len(results) == 1 and not want_report:
            report = results[0]["analysis"]
        else:
            emit("director", {"status": "synthesizing", "msg": "总监正在汇总各专家产出、合成报告…"})
            # 仅用成功的专家产出做合成，避免把失败文本喂进报告。
            report, synth_ok = self._synthesize(question, plan, successful, want_report=want_report)
        emit("director", {"status": "done" if synth_ok else "error"})

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not synth_ok:
            # 合成门未过：明确失败（报告内容仍带回，供排查/人工兜底），前端走降级/错误态。
            return {
                "ok": False, "error": "报告合成未完成（模型暂时不可用），请稍后重试。",
                "route": route, "plan": plan, "experts": results, "report": report,
                "warnings": warnings, "elapsed_ms": elapsed_ms,
            }
        return {
            "ok": True,
            "route": route,
            "plan": plan,
            "experts": results,
            "report": report,
            "warnings": warnings,
            "elapsed_ms": elapsed_ms,
        }

    # ----------------------------------------------------------- pool build

    def _build_pool(self, user_skills: list[dict[str, Any]] | None,
                    overrides: dict[str, dict[str, Any]] | None = None) -> list[_Participant]:
        overrides = overrides or {}
        pool: list[_Participant] = []
        for e in self.registry.workers():
            ov = overrides.get(e.id) or {}
            if ov.get("deleted"):
                continue  # 用户已隐藏该内置专家
            def _ov(key: str, default: str) -> str:
                v = ov.get(key)
                return str(v) if (v is not None and str(v).strip() != "") else default
            pool.append(_Participant(
                id=e.id,
                name=_ov("name", e.name),
                profession=_ov("profession", e.profession),
                emoji=_ov("emoji", e.emoji),
                persona=_ov("instructions", e.persona),
                skill_ids=e.skill_ids, is_builtin=True,
            ))
        for us in (user_skills or []):
            try:
                pool.append(_Participant(
                    id=str(us.get("id")), name=str(us.get("name") or "自定义专家"),
                    profession=str(us.get("profession") or "自定义角色"),
                    emoji=str(us.get("emoji") or "✨"),
                    persona=str(us.get("instructions") or ""),
                    skill_ids=[], is_builtin=False,
                ))
            except Exception:  # noqa: BLE001
                continue
        return pool

    # -------------------------------------------------------------- routing

    def _route(self, question: str, candidates: list[_Participant], director: ExpertDef | None,
               *, want_report: bool, history: list[dict[str, str]] | None) -> dict[str, Any]:
        roster = "\n".join(
            f"- id={p.id} | {p.name}（{p.profession}）| 能力：{('、'.join(p.skill_ids) or p.profession)}"
            for p in candidates
        )
        director_persona = (director.persona[:1800] if director else "你是决策调度总监，负责理解问题、规划调度、合成报告。")
        sys = (
            director_persona
            + "\n\n你现在只做【路由与调度规划】，不要自己分析。"
            "从【可用专家】里挑选最合适的若干位（跨域问题可多选，单点查询/单域分析尽量少选），"
            "为每位明确子任务；若该专家需要真实数据，给出一句中文『取数问题』(data_query) 供数据系统执行，"
            "不需要数据则 data_query 留空。报告/综合诊断/汇报类问题 want_report=true。"
        )
        schema = (
            '{"route":"fast|slow","plan":"<1-2句中文调度说明>","want_report":true,'
            '"experts":[{"id":"<专家id>","subtask":"<该专家做什么>","data_query":"<中文取数问题或空>"}]}'
        )
        hist = ""
        if history:
            hist = "\n".join(f"[{m.get('role','user')}] {m.get('content','')}" for m in history[-4:])
        user = (
            f"用户问题：{question}\n"
            f"{('历史对话：' + hist) if hist else ''}\n"
            f"用户是否明确要报告：{'是' if want_report else '未指定'}\n\n"
            f"可用专家：\n{roster}\n\n"
            "请输出调度方案 JSON。"
        )
        try:
            payload, _ = self.llm.chat_json(
                [{"role": "system", "content": sys}, {"role": "user", "content": user}],
                schema_hint=schema, temperature=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team routing LLM failed: %s — fallback heuristic", exc)
            return self._fallback_route(question, candidates, want_report=want_report)
        if not isinstance(payload, dict) or not payload.get("experts"):
            return self._fallback_route(question, candidates, want_report=want_report)
        # 只保留候选集内的合法专家
        valid_ids = {p.id for p in candidates}
        payload["experts"] = [e for e in payload["experts"] if isinstance(e, dict) and e.get("id") in valid_ids]
        if not payload["experts"]:
            return self._fallback_route(question, candidates, want_report=want_report)
        if want_report:
            payload["want_report"] = True
        return payload

    def _fallback_route(self, question: str, candidates: list[_Participant], *, want_report: bool) -> dict[str, Any]:
        """LLM 不可用时的确定性兜底：按触发词粗选，至少给一个取数专家。"""
        q = question or ""
        report_words = any(w in q for w in ("报告", "综合", "汇报", "全面", "诊断"))
        picks: list[dict[str, Any]] = []
        kw_map = [
            ("sales-analyst", ("销售", "达成率", "终端", "品类", "增长", "下滑")),
            ("user-ops-analyst", ("新客", "复购", "潜客", "用户", "转化")),
            ("channel-analyst", ("渠道", "铺货", "动销", "系统")),
            ("market-analyst", ("竞品", "价格", "份额", "市场")),
            ("finance-analyst", ("费用", "roi", "预算", "成本")),
        ]
        cand_ids = {p.id for p in candidates}
        for eid, kws in kw_map:
            if eid in cand_ids and any(k in q.lower() for k in kws):
                picks.append({"id": eid, "subtask": question, "data_query": question})
        if not picks:
            # 没命中关键词 → 用取数专家（速捷）兜底快查
            fast_id = "data-query-analyst" if "data-query-analyst" in cand_ids else (next(iter(cand_ids), ""))
            if fast_id:
                picks.append({"id": fast_id, "subtask": question, "data_query": question})
            return {"route": "fast", "plan": "快速取数兜底（模型不可用）", "experts": picks, "want_report": False}
        return {"route": "slow", "plan": "按关键词调度兜底（模型不可用）", "experts": picks[:4], "want_report": report_words or want_report}

    # --------------------------------------------------------- expert exec

    def _run_expert(self, p: _Participant, question: str, subtask: str, data_block: str) -> tuple[str, bool]:
        methodology = self.registry.skill_methodology(p.skill_ids) if p.skill_ids else ""
        knowledge = self.registry.knowledge_digest(max_chars=3500)
        sys_parts = [p.persona.strip()]
        if methodology:
            sys_parts.append("## 你的 Skill 方法论（必须遵循）\n" + methodology)
        sys_parts.append(
            "## 知识库（数据口径以语义层为准，方法论/行业知识见下）\n" + knowledge
        )
        sys_parts.append(
            "## 输出要求\n结论先行、数据支撑、给具体名称（区域/渠道/品类）。"
            "用简洁中文 markdown，控制在 350 字内，聚焦你的专业子任务，不要复述他人职责。"
            "若给到了『数据结果』，必须基于真实数字分析，不要编造数据。"
        )
        sys = "\n\n".join(sys_parts)
        user = f"用户原始问题：{question}\n\n你的子任务：{subtask}\n"
        if data_block:
            user += f"\n数据系统返回的真实结果：\n{data_block}\n"
        else:
            user += "\n（本子任务无附带数据，请基于方法论与知识库给出分析；如必须依赖数据请说明需要哪些数据。）\n"
        try:
            res = self.llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.2)
            text = (res.text or "").strip()
            if not text:
                return "（该专家暂无产出）", False
            return text, True
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert %s analysis failed: %s", p.id, exc)
            return f"（{p.name} 分析失败，请稍后重试。）", False

    # ------------------------------------------------------------ synthesis

    def _synthesize(self, question: str, plan: str, results: list[dict[str, Any]], *, want_report: bool) -> tuple[str, bool]:
        director = self.registry.director()
        spec = self.registry.knowledge_files().get("output-format-spec", "")[:1800]
        sys = (
            (director.persona[:1500] if director else "你是决策调度总监，负责合成报告。")
            + "\n\n你现在做【报告合成】：综合各专家产出，提炼核心结论、处理矛盾、给跨域建议。"
            "不要机械拼接，要有主理人判断。\n\n## 输出格式参考\n" + spec
        )
        body = "\n\n".join(
            f"### 专家：{r['name']}（{r['profession']}）\n子任务：{r.get('subtask','')}\n{r.get('analysis','')}"
            for r in results
        )
        depth = "完整经营分析报告（核心结论→关键发现→诊断建议→风险提示→建议行动）" if want_report \
            else "精炼结论（核心结论→关键发现→建议，控制在 500 字内）"
        user = (
            f"用户问题：{question}\n调度说明：{plan}\n\n各专家产出：\n{body}\n\n"
            f"请合成{depth}，使用中文 markdown。"
        )
        try:
            res = self.llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.3)
            text = (res.text or "").strip()
            if text:
                return text, True
            # 模型返回空：退回各专家原文（信息不丢），但标记合成未成功。
            return body, False
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team synthesis failed: %s", exc)
            # 合成失败兜底：把各专家产出拼起来，至少不丢信息；但合成本身判失败。
            return "## 各专家分析（报告合成未完成，原文如下）\n\n" + body, False

    # ----------------------------------------------------------- data fetch

    def _fetch_data(self, query: str, *, user_id: str, is_admin: bool) -> dict[str, Any]:
        """复用问数流水线取真实数据。失败/未接入时返回说明，不阻断专家分析。"""
        out: dict[str, Any] = {"query": query, "rows": 0, "sql": "", "narrative": "", "table_preview": "",
                               "for_prompt": "", "ok": True}
        if self.data_pipe is None:
            out["ok"] = False
            out["for_prompt"] = f"（数据查询「{query}」未接入数据系统，请基于经验与口径定性分析）"
            return out
        try:
            r = self.data_pipe.run(query, user_id=user_id, is_admin=is_admin, skip_llm_narrative=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team data fetch failed: %s", exc)
            out["ok"] = False
            out["for_prompt"] = f"（数据查询「{query}」执行失败，请稍后重试。）"
            return out
        if not getattr(r, "ok", True):
            out["ok"] = False
            out["for_prompt"] = f"（数据查询「{query}」未取到有效数据，请基于经验与口径定性分析）"
            return out
        answer = getattr(r, "answer", None) or {}
        narrative = str(answer.get("narrative") or "")
        sql = str(getattr(r, "sql", "") or "")
        rows = int(getattr(r, "rows", 0) or 0)
        table = answer.get("table") or {}
        preview = _table_preview(table)
        out.update({"narrative": narrative, "sql": sql, "rows": rows, "table_preview": preview})
        out["for_prompt"] = (
            f"取数问题：{query}\n结论：{narrative}\n行数：{rows}\n数据预览：\n{preview}"
        ).strip()
        return out


def _table_preview(table: dict[str, Any], *, max_rows: int = 12) -> str:
    """把 answer.table（display_columns/display_rows 或 columns/rows）转成紧凑文本表。"""
    try:
        cols = table.get("display_columns") or table.get("columns") or []
        col_labels = [str(c.get("label") or c.get("key") or c) if isinstance(c, dict) else str(c) for c in cols]
        rows = table.get("display_rows") or table.get("rows") or []
        if not col_labels and rows and isinstance(rows[0], dict):
            col_labels = list(rows[0].keys())
        lines = [" | ".join(col_labels)] if col_labels else []
        for row in rows[:max_rows]:
            if isinstance(row, dict):
                lines.append(" | ".join(str(row.get(c, "")) for c in (col_labels or row.keys())))
            elif isinstance(row, (list, tuple)):
                lines.append(" | ".join(str(x) for x in row))
        if len(rows) > max_rows:
            lines.append(f"…（共 {len(rows)} 行，仅显示前 {max_rows} 行）")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


_orch_singleton: ExpertTeamOrchestrator | None = None


def get_orchestrator() -> ExpertTeamOrchestrator:
    """单例。data_pipe 懒接入：优先复用 main.py 已建好的 Pipeline，避免重复构建语义层/检索。"""
    global _orch_singleton
    if _orch_singleton is None:
        _orch_singleton = ExpertTeamOrchestrator()
    if _orch_singleton.data_pipe is None:
        try:
            from app.core.orchestrator import get_pipeline  # 复用同一条数据流水线
            _orch_singleton.data_pipe = get_pipeline()
        except Exception:  # noqa: BLE001
            pass
    return _orch_singleton
