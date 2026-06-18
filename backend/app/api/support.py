"""跨路由共享的可信结果取回 —— 从 main.py 拆出（零行为变化）。

报告生成 / 飞书推送 / XLSX 导出都必须以**服务端落地的 assistant 消息**为准，绝不信任
前端传入的 question/answer/plan/sql/narrative。这里提供唯一的取回入口，供 main.py 与
各 route 模块、exports 服务共用，避免重复实现与"前端伪造内容"的安全面。
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.auth import User


def trusted_result_for_trace(store, user: User, conversation_id: str, trace_id: str) -> dict[str, Any]:
    """按 (conversation_id, trace_id) 从会话存储取回**服务端可信**的问数结果。

    安全（P0）：报告生成 / 飞书推送绝不信任前端传入的 question/answer/plan/sql/narrative，
    一律以服务端落地的 assistant 消息为准，杜绝伪造内容。
    校验会话归属（user_id 必须匹配），缺参 → 400，找不到 / 越权一律 404（不泄露存在性）。
    返回 {question, answer, plan, sql}。
    """
    conversation_id = (conversation_id or "").strip()
    trace_id = (trace_id or "").strip()
    if not conversation_id or not trace_id:
        raise HTTPException(status_code=400, detail="缺少 conversation_id 或 trace_id")
    sess = store.get_session(conversation_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    msgs = store.list_messages(conversation_id, limit=500)
    for i, m in enumerate(msgs):
        if m.role == "assistant" and str((m.payload or {}).get("trace_id") or "") == trace_id:
            payload = m.payload or {}
            question = ""
            for prev in reversed(msgs[:i]):
                if prev.role == "user":
                    question = prev.content
                    break
            answer = payload.get("answer")
            plan = payload.get("plan")
            return {
                "question": question,
                "answer": answer if isinstance(answer, dict) else {},
                "plan": plan if isinstance(plan, dict) else {},
                "sql": str(payload.get("sql") or ""),
            }
    raise HTTPException(status_code=404, detail="未找到该回答（trace_id 无效或结果已过期）")


def user_dict(u: User) -> dict[str, Any]:
    """User → 对外 JSON（绝不含密码哈希等敏感字段）。供 me/profile 与 admin 用户管理共用。"""
    return {
        "id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at,
        "email": u.email or "",
        "must_change_password": bool(u.must_change_password),
        "is_active": bool(getattr(u, "is_active", True)),
    }


# 兼容旧引用名（main.py / exports 历史用下划线前缀）。
_trusted_result_for_trace = trusted_result_for_trace
_user_dict = user_dict
