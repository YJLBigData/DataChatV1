"""专家团 HTTP 接口 —— 独立 APIRouter，在 main.py 一行 include 即可挂载。

路由前缀 /api/expert-team：
  · GET    /bootstrap            花名册（含覆盖/隐藏后的内置专家 + 我的自建 skill）+ 知识库 + 工作流
  · GET    /members/{id}         查：单个成员完整可编辑内容（含默认值，便于还原）
  · POST   /skills               增：新建自定义 skill（= 一个可调度的自定义专家）
  · PATCH  /members/{id}         改：内置专家→写覆盖；自建 skill→更新
  · DELETE /members/{id}         删：内置专家→隐藏（软删，可还原）；自建 skill→硬删；总监不可删
  · POST   /members/{id}/reset   内置专家还原出厂默认（清覆盖）
  · POST   /chat                 编排问数/报告（总监调度多专家 → 合成）

内置专家与自建 skill 一视同仁地支持增删改查；内置的"改/删"以覆盖落库，不动定义文件。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.auth import User

from .members import list_members, member_detail, split_for_orchestrator
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


class MemberPatchReq(BaseModel):
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


def _is_custom(member_id: str) -> bool:
    return member_id.startswith("usk_")


@router.get("/bootstrap")
def bootstrap(user: User = Depends(require_user)) -> dict[str, Any]:
    reg = get_registry()
    members = list_members(user.id, include_director=True)
    director = next((m for m in members if m.get("is_director")), None)
    experts = [m for m in members if m.get("is_builtin") and not m.get("is_director")]
    user_skills = [m for m in members if not m.get("is_builtin")]
    return {
        "director": director,
        "experts": experts,
        "user_skills": user_skills,
        "knowledge_files": sorted(reg.knowledge_files().keys()),
        "workflows": [
            {"name": "销售诊断", "trigger": "销售涨跌 / 达成率异常", "flow": "速捷→齐增辉→查实真→合成"},
            {"name": "新客诊断", "trigger": "新客分析 / 复购率", "flow": "速捷→甄客来+齐增辉→查实真→合成"},
            {"name": "区域经营", "trigger": "区域诊断 / 大区异常", "flow": "齐增辉+路通达+甄客来→查实真→合成"},
            {"name": "综合报告", "trigger": "月度报告 / 总裁汇报", "flow": "全5分析师→查实真→合成"},
            {"name": "费用效率", "trigger": "费用率 / ROI", "flow": "贝精诚+齐增辉→查实真→合成"},
        ],
    }


@router.get("/members/{member_id}")
def get_member(member_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    detail = member_detail(user.id, member_id)
    if not detail:
        return {"ok": False, "error": "成员不存在"}
    return {"ok": True, "member": detail}


@router.post("/skills")
def create_skill(req: SkillCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    if not (req.name or "").strip():
        return {"ok": False, "error": "技能名称不能为空"}
    sk = get_expert_store().create_skill(
        user.id, req.name, req.profession, req.instructions, req.emoji or "✨",
    )
    return {"ok": True, "member": member_detail(user.id, sk.id)}


@router.patch("/members/{member_id}")
def update_member(member_id: str, req: MemberPatchReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_expert_store()
    if _is_custom(member_id):
        ok = store.update_skill(
            user.id, member_id, name=req.name, profession=req.profession,
            instructions=req.instructions, emoji=req.emoji,
        )
        if not ok:
            return {"ok": False, "error": "技能不存在或无权修改"}
    else:
        if not get_registry().expert(member_id):
            return {"ok": False, "error": "成员不存在"}
        store.upsert_override(
            user.id, member_id, name=req.name, profession=req.profession,
            instructions=req.instructions, emoji=req.emoji,
        )
    return {"ok": True, "member": member_detail(user.id, member_id)}


@router.delete("/members/{member_id}")
def delete_member(member_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_expert_store()
    if _is_custom(member_id):
        return {"ok": store.delete_skill(user.id, member_id)}
    e = get_registry().expert(member_id)
    if not e:
        return {"ok": False, "error": "成员不存在"}
    if e.is_director:
        return {"ok": False, "error": "决策调度总监为编排必需，不可删除（可编辑）"}
    store.upsert_override(user.id, member_id, deleted=True)  # 软删=隐藏，可还原
    return {"ok": True, "hidden": True}


@router.post("/members/{member_id}/reset")
def reset_member(member_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    """内置专家还原出厂默认（清覆盖，含取消隐藏）。自建 skill 无默认可还原。"""
    if _is_custom(member_id):
        return {"ok": False, "error": "自定义技能没有出厂默认，请直接编辑或删除"}
    if not get_registry().expert(member_id):
        return {"ok": False, "error": "成员不存在"}
    get_expert_store().clear_override(user.id, member_id)
    return {"ok": True, "member": member_detail(user.id, member_id)}


@router.post("/chat")
def team_chat(req: TeamChatReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    question = (req.question or "").strip()
    if not question:
        return {"ok": False, "error": "请输入问题"}

    from app.core.llm.router import set_request_provider
    set_request_provider(req.llm_provider)

    # 自建 skill + 内置覆盖（含隐藏）一并交给编排器，保证调度池 = 用户当前看到的花名册
    user_skills, overrides = split_for_orchestrator(user.id)
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
        overrides=overrides,
        want_report=req.want_report,
        on_event=on_event,
    )
    result["events"] = events
    try:
        get_expert_store().log_run(user.id, question, {"result": result})
    except Exception:  # noqa: BLE001
        pass
    return result
