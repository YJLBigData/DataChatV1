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

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.auth import User
from app.core.folders import FolderNotFound

from .history import get_expert_conversation_store, get_expert_folders_store
from .jobs import get_expert_job_manager
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
    smartq_cube_ids: Optional[list[str]] = None


# ---- 会话历史 + 文件夹（与问数完全独立的专家团专属存储）----
class ConvCreateReq(BaseModel):
    title: str = "新会话"


class ConvRenameReq(BaseModel):
    title: str = "新会话"


class FolderCreateReq(BaseModel):
    name: str = "未命名"
    color: str = ""


class FolderRenameReq(BaseModel):
    name: str
    color: Optional[str] = None


class CollectReq(BaseModel):
    conversation_id: str
    folder_id: str


class MembershipReq(BaseModel):
    conversation_ids: list[str] = []


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
        smartq_cube_ids=req.smartq_cube_ids or None,
        on_event=on_event,
    )
    result["events"] = events
    try:
        get_expert_store().log_run(user.id, question, {"result": result})
    except Exception:  # noqa: BLE001
        pass
    return result


# ===================================================== 后台编排（异步 job）

@router.post("/chat/async")
def team_chat_async(req: TeamChatReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    """提交一次专家团编排为**服务端后台任务**，立即返回 job_id + conversation_id。

    切页/刷新/关页都不会中断分析（线程池在后台跑，结果落库到专家团会话）。
    前端轮询 GET /jobs/{job_id} 取进度与最终结果；完成后在左侧会话/导航打红点。
    """
    question = (req.question or "").strip()
    if not question:
        return {"ok": False, "error": "请输入问题"}

    conv_store = get_expert_conversation_store()
    cid = req.conversation_id
    if cid:
        s = conv_store.get_session(cid)
        if not s or s.user_id != user.id:
            return {"ok": False, "error": "会话不存在或无权访问"}
    else:
        s = conv_store.create_session(user.id, title=(question[:30] or "新会话"))
        cid = s.id

    # 先取既有历史（不含当前问题）供多轮上下文，再落当前 user 消息（保证 user 先于 assistant）
    from .jobs import JobRejected
    history = conv_store.history_for_llm(cid, limit=6)
    conv_store.append_message(
        cid, "user", question,
        payload={"expert_ids": req.expert_ids or [], "want_report": bool(req.want_report)},
    )
    try:
        job_id = get_expert_job_manager().submit(
            conversation_id=cid,
            user_id=user.id,
            is_admin=(user.role == "admin"),
            question=question,
            expert_ids=req.expert_ids,
            want_report=bool(req.want_report),
            llm_provider=req.llm_provider,
            smartq_cube_ids=req.smartq_cube_ids or None,
            history=history,
        )
    except JobRejected as exc:
        # 背压拒绝：在会话里留一条 assistant 提示（而非悬空的提问），刷新后也能看到
        conv_store.append_message(cid, "assistant", exc.message,
                                  payload={"result": {"ok": False, "error": exc.message}})
        return {"ok": False, "error": exc.message, "conversation_id": cid}
    return {"ok": True, "job_id": job_id, "conversation_id": cid}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    """取消后台编排：排队中即时取消；运行中 best-effort（跑完丢弃结果）。"""
    ok = get_expert_job_manager().cancel(job_id, user.id)
    return {"ok": ok}


@router.get("/jobs")
def list_jobs(user: User = Depends(require_user), status: Optional[str] = None) -> dict[str, Any]:
    """列出当前用户的后台编排（默认 active=queued|running）—— 前端刷新后据此重挂红点/进度。
    含**排队中**的 job（此前只取 running 会漏掉刚提交还没轮到的分析）。"""
    items = get_expert_job_manager().list_for_user(user.id, status=status or "active")
    return {"ok": True, "items": items}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    """轮询后台编排状态。job 不存在（服务重启/过期）→ status=missing，
    前端据此把对应轮标记为"分析已中断，请重试"，而非一直转圈。"""
    snap = get_expert_job_manager().get(job_id, user_id=user.id)
    if not snap:
        return {"ok": False, "status": "missing", "error": "任务不存在或已过期（可能服务已重启）"}
    return {"ok": True, **snap}


# ===================================================== 会话历史（专家团专属）

@router.post("/conversations")
def conv_create(req: ConvCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    s = get_expert_conversation_store().create_session(user.id, title=req.title)
    return {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}


@router.get("/conversations")
def conv_list(user: User = Depends(require_user)) -> dict[str, Any]:
    items = get_expert_conversation_store().list_sessions(user.id)
    return {"items": [{"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at} for s in items]}


@router.get("/conversations/{cid}")
def conv_get(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_expert_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    msgs = store.list_messages(cid)
    return {
        "id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "payload": m.payload, "created_at": m.created_at}
            for m in msgs
        ],
    }


@router.patch("/conversations/{cid}")
def conv_rename(cid: str, req: ConvRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_expert_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    store.rename_session(cid, req.title or "新会话")
    return {"ok": True}


@router.delete("/conversations/{cid}")
def conv_delete(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_expert_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    store.delete_session(cid)
    # 顺带解除该会话在所有文件夹里的收藏（与问数 delete_folder 语义一致：不留悬挂收藏）
    try:
        fstore = get_expert_folders_store()
        for fid in fstore.folder_ids_for_conversation(user.id, cid):
            fstore.remove(user.id, cid, fid)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ===================================================== 文件夹 + 收藏（专家团专属）

@router.get("/folders")
def folders_list(user: User = Depends(require_user)) -> dict[str, Any]:
    items = get_expert_folders_store().list_folders(user.id)
    return {"items": [{"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at} for f in items]}


@router.post("/folders")
def folders_create(req: FolderCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    f = get_expert_folders_store().create_folder(user.id, req.name, req.color)
    return {"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at}


@router.patch("/folders/{folder_id}")
def folders_rename(folder_id: str, req: FolderRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_expert_folders_store().rename_folder(user.id, folder_id, req.name, req.color)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True}


@router.delete("/folders/{folder_id}")
def folders_delete(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_expert_folders_store().delete_folder(user.id, folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True}


@router.get("/folders/{folder_id}/conversations")
def folder_conversations(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    fstore = get_expert_folders_store()
    conv_store = get_expert_conversation_store()
    out: list[dict[str, Any]] = []
    for it in fstore.list_collections(user.id, folder_id=folder_id):
        s = conv_store.get_session(it.conversation_id)
        if not s:
            continue
        out.append({"id": s.id, "title": s.title, "created_at": s.created_at,
                    "updated_at": s.updated_at, "collected_at": it.created_at})
    return {"items": out}


@router.post("/conversations/{cid}/collect")
def conv_collect(cid: str, req: CollectReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    if cid != req.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不一致")
    if not (req.folder_id or "").strip():
        raise HTTPException(status_code=422, detail="folder_id 不能为空")
    s = get_expert_conversation_store().get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在或无权限")
    store = get_expert_folders_store()
    # 归属校验：目标文件夹必须存在且属于该用户，杜绝悬挂收藏（审计 P0）。
    if not store.get_folder(user.id, req.folder_id):
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    try:
        c = store.add(user.id, cid, req.folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True, "id": c.id}


@router.delete("/conversations/{cid}/collect/{folder_id}")
def conv_uncollect(cid: str, folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    get_expert_folders_store().remove(user.id, cid, folder_id)
    return {"ok": True}


@router.get("/conversations/{cid}/folders")
def conv_folders(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    return {"folder_ids": get_expert_folders_store().folder_ids_for_conversation(user.id, cid)}


@router.post("/folders/membership")
def folders_membership(req: MembershipReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    """批量查询多个专家团会话各自被收藏到哪些文件夹 —— 消除前端 N+1。"""
    mp = get_expert_folders_store().folder_ids_for_conversations(user.id, req.conversation_ids or [])
    return {"map": mp}
