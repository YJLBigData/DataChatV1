"""会话文件夹 + 收藏路由（从 main.py 拆出，零行为变化）。

前缀 /api：文件夹 CRUD、把会话收藏进文件夹、批量查询会话归属。归属校验贯穿每个写接口：
目标文件夹必须存在且属于该用户，杜绝悬挂收藏（审计 P0）；改名/删除不存在的文件夹 → 404。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import require_user
from app.api.schemas import CollectionReq, FolderCreateReq, FolderMembershipReq, FolderRenameReq
from app.core.auth import User
from app.core.conversation import get_conversation_store
from app.core.folders import FolderNotFound, get_folders_store

router = APIRouter(tags=["folders"])


@router.get("/api/folders")
def api_folders_list(user: User = Depends(require_user)) -> dict[str, Any]:
    items = get_folders_store().list_folders(user.id)
    return {"items": [{"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at} for f in items]}


@router.post("/api/folders")
def api_folders_create(req: FolderCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    f = get_folders_store().create_folder(user.id, req.name, req.color)
    return {"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at}


@router.patch("/api/folders/{folder_id}")
def api_folders_rename(folder_id: str, req: FolderRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_folders_store().rename_folder(user.id, folder_id, req.name, req.color)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True}


@router.delete("/api/folders/{folder_id}")
def api_folders_delete(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_folders_store().delete_folder(user.id, folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True}


@router.get("/api/folders/{folder_id}/conversations")
def api_folders_conversations(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_folders_store()
    items = store.list_collections(user.id, folder_id=folder_id)
    # 加上会话元信息
    conv_store = get_conversation_store()
    out: list[dict[str, Any]] = []
    for it in items:
        s = conv_store.get_session(it.conversation_id)
        if not s:
            continue
        out.append({
            "id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at,
            "collected_at": it.created_at,
        })
    return {"items": out}


@router.post("/api/conversations/{cid}/collect")
def api_conversation_collect(cid: str, req: CollectionReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    # cid 必须等于 body.conversation_id，且会话必须属于该用户
    if cid != req.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id 不一致")
    if not (req.folder_id or "").strip():
        raise HTTPException(status_code=422, detail="folder_id 不能为空")
    s = get_conversation_store().get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在或无权限")
    store = get_folders_store()
    # 归属校验：目标文件夹必须存在且属于该用户，杜绝悬挂收藏（审计 P0）。
    if not store.get_folder(user.id, req.folder_id):
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    try:
        c = store.add(user.id, cid, req.folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在或无权限")
    return {"ok": True, "id": c.id}


@router.post("/api/folders/membership")
def api_folders_membership(req: FolderMembershipReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    """批量查询多个会话各自被收藏到哪些文件夹 —— 消除前端逐个查询的 N+1。"""
    mp = get_folders_store().folder_ids_for_conversations(user.id, req.conversation_ids or [])
    return {"map": mp}


@router.delete("/api/conversations/{cid}/collect/{folder_id}")
def api_conversation_uncollect(cid: str, folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    get_folders_store().remove(user.id, cid, folder_id)
    return {"ok": True}


@router.get("/api/conversations/{cid}/folders")
def api_conversation_folders(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    fids = get_folders_store().folder_ids_for_conversation(user.id, cid)
    return {"folder_ids": fids}
