"""会话历史路由（从 main.py 拆出，零行为变化）。

前缀 /api/conversations：建/列/取/改名/删。会话归属校验（user_id 必须匹配）贯穿每个接口，
越权/不存在一律 404（不泄露存在性）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import require_user
from app.api.schemas import ConversationCreateReq, ConversationRenameReq
from app.core.auth import User
from app.core.conversation import get_conversation_store

router = APIRouter(tags=["conversations"])


@router.post("/api/conversations")
def conversations_create(req: ConversationCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    s = get_conversation_store().create_session(user.id, title=req.title)
    return {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}


@router.get("/api/conversations")
def conversations_list(user: User = Depends(require_user)) -> dict[str, Any]:
    items = get_conversation_store().list_sessions(user.id)
    return {"items": [{"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at} for s in items]}


@router.get("/api/conversations/{cid}")
def conversations_get(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    msgs = store.list_messages(cid)
    return {
        "id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "payload": m.payload, "created_at": m.created_at}
            for m in msgs
        ],
    }


@router.patch("/api/conversations/{cid}")
def conversations_rename(cid: str, body: ConversationRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    store.rename_session(cid, body.title or "新会话")
    return {"ok": True}


@router.delete("/api/conversations/{cid}")
def conversations_delete(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_conversation_store()
    s = store.get_session(cid)
    if not s or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    store.delete_session(cid)
    return {"ok": True}
