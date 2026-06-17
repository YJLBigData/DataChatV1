"""专家团 HTTP 接口 —— 独立 APIRouter，在 main.py 一行 include 即可挂载。

路由前缀 /api/expert-team：
  · GET  /bootstrap        专家花名册 + 我的自建 skill + 知识库清单
  · POST /skills           新建自定义 skill（=一个可调度的自定义专家）
  · PATCH /skills/{id}     编辑
  · DELETE /skills/{id}    删除
  · POST /chat             编排问数/报告（总监调度多专家 → 合成）

鉴权复用 app.api.deps；模型 provider 复用右上角下拉（llm_provider 透传）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.auth import User

from .orchestrator import get_orchestrator
from .registry import get_registry
from .store import get_expert_store

logger = logging.getLogger("datachat.expert_team")

router = APIRouter(prefix="/api/expert-team", tags=["expert-team"])


class SkillCreateReq(BaseModel):
    name: str
    profession: str = ""
    instructions: str = ""
    emoji: str = "✨"


class SkillPatchReq(BaseModel):
    name: Optional[str] = None
    profession: Optional[str] = None
    instructions: Optional[str] = None
    emoji: Optional[str] = None


class TeamChatReq(BaseModel):
    question: str
    expert_ids: Optional[list[str]] = None   # 勾选要参与的专家/skill；空 = 自动调度
    want_report: bool = False
    llm_provider: Optional[str] = None
    conversation_id: Optional[str] = None


@router.get("/bootstrap")
def bootstrap(user: User = Depends(require_user)) -> dict[str, Any]:
    reg = get_registry()
    director = reg.director()
    store = get_expert_store()
    return {
        "director": director.to_dict() if director else None,
        "experts": [e.to_dict() for e in reg.workers()],
        "user_skills": [s.to_dict() for s in store.list_skills(user.id)],
        "knowledge_files": sorted(reg.knowledge_files().keys()),
        "workflows": [
            {"name": "销售诊断", "trigger": "销售涨跌 / 达成率异常", "flow": "速捷→齐增辉→查实真→合成"},
            {"name": "新客诊断", "trigger": "新客分析 / 复购率", "flow": "速捷→甄客来+齐增辉→查实真→合成"},
            {"name": "区域经营", "trigger": "区域诊断 / 大区异常", "flow": "齐增辉+路通达+甄客来→查实真→合成"},
            {"name": "综合报告", "trigger": "月度报告 / 总裁汇报", "flow": "全5分析师→查实真→合成"},
            {"name": "费用效率", "trigger": "费用率 / ROI", "flow": "贝精诚+齐增辉→查实真→合成"},
        ],
    }


@router.post("/skills")
def create_skill(req: SkillCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    if not (req.name or "").strip():
        return {"ok": False, "error": "技能名称不能为空"}
    sk = get_expert_store().create_skill(
        user.id, req.name, req.profession, req.instructions, req.emoji or "✨",
    )
    return {"ok": True, "skill": sk.to_dict()}


@router.patch("/skills/{sid}")
def update_skill(sid: str, req: SkillPatchReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    ok = get_expert_store().update_skill(
        user.id, sid, name=req.name, profession=req.profession,
        instructions=req.instructions, emoji=req.emoji,
    )
    if not ok:
        return {"ok": False, "error": "技能不存在或无权修改"}
    sk = get_expert_store().get_skill(user.id, sid)
    return {"ok": True, "skill": sk.to_dict() if sk else None}


@router.delete("/skills/{sid}")
def delete_skill(sid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    ok = get_expert_store().delete_skill(user.id, sid)
    return {"ok": ok}


@router.post("/chat")
def team_chat(req: TeamChatReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    question = (req.question or "").strip()
    if not question:
        return {"ok": False, "error": "请输入问题"}

    from app.core.llm.router import set_request_provider
    set_request_provider(req.llm_provider)

    store = get_expert_store()
    # 把用户勾选的自建 skill 取出来给编排器（内置专家由 registry 提供）
    user_skills = [s.to_dict() for s in store.list_skills(user.id)]
    events: list[dict[str, Any]] = []

    def on_event(stage: str, payload: dict[str, Any]) -> None:
        events.append({"stage": stage, **payload})

    orch = get_orchestrator()
    result = orch.run(
        question,
        user_id=user.id,
        is_admin=(user.role == "admin"),
        selected_expert_ids=req.expert_ids or None,
        user_skills=user_skills,
        want_report=req.want_report,
        on_event=on_event,
    )
    result["events"] = events
    try:
        store.log_run(user.id, question, {"result": result})
    except Exception:  # noqa: BLE001
        pass
    return result
