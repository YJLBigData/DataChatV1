"""管理端路由：用户管理、审计日志、数据权限（从 main.py 拆出，零行为变化）。

均要求 require_admin（数据权限写入还会用语义层校验维度/表合法性）。语义层管理、LLM 设置/预设
分别在 main.py（依赖 cfg/get_pipe 热重载）与 routes/llm.py。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import require_admin
from app.api.schemas import CreateUserReq, PermissionsPutReq, ResetPasswordReq, UserActiveReq
from app.api.support import user_dict
from app.core.auth import AuthError, User, get_auth_store
from app.core.permissions import get_permissions_store
from app.core.query_log import get_query_log_store

router = APIRouter(tags=["admin"])


# ============================================================ admin: users

@router.get("/api/admin/users")
def api_admin_list_users(_: User = Depends(require_admin)) -> dict[str, Any]:
    users = get_auth_store().list_users()
    return {"items": [user_dict(u) for u in users]}


@router.post("/api/admin/users")
def api_admin_create_user(req: CreateUserReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
    from app.core.auth import generate_initial_password
    # 没传密码 → 随机生成强密码并返回（一次性，admin 转告新用户）
    initial_pwd = req.password or generate_initial_password()
    enforce = bool(req.password)   # 用户传密码必须强度校验；系统生成跳过（自带强度）
    try:
        user = get_auth_store().create_user(
            req.username, initial_pwd, req.role,
            email=req.email or "",
            must_change_password=bool(req.must_change_password),
            enforce_strength=enforce,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out = user_dict(user)
    if not req.password:
        out["one_time_password"] = initial_pwd
    return out


@router.delete("/api/admin/users/{username}")
def api_admin_delete_user(username: str, _: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        get_auth_store().delete_user(username)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.post("/api/admin/users/{username}/password")
def api_admin_reset_password(username: str, req: ResetPasswordReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
    from app.core.auth import generate_initial_password
    new_pwd = req.new_password or generate_initial_password()
    enforce = bool(req.new_password)
    try:
        get_auth_store().set_password(
            username, new_pwd,
            enforce_strength=enforce,
            clear_must_change=not req.must_change_password,
        )
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    out: dict[str, Any] = {"ok": True}
    if not req.new_password:
        out["one_time_password"] = new_pwd
    return out


@router.post("/api/admin/users/{username}/active")
def api_admin_set_user_active(username: str, req: UserActiveReq = Body(...), admin: User = Depends(require_admin)) -> dict[str, Any]:
    """启用/停用账号。停用后该用户无法登录、已签发 token 立即失效。"""
    if username.strip().lower() == admin.username and not req.is_active:
        raise HTTPException(status_code=400, detail="不能停用当前登录的管理员账号")
    store = get_auth_store()
    if not hasattr(store, "set_active"):
        raise HTTPException(status_code=501, detail="当前用户存储不支持启停")
    try:
        store.set_active(username, bool(req.is_active))
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "username": username, "is_active": bool(req.is_active)}


# ============================================================ admin: query log

@router.get("/api/admin/logs")
def api_admin_logs(
    _: User = Depends(require_admin),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    username: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
) -> dict[str, Any]:
    items, total = get_query_log_store().list(
        limit=limit, offset=offset,
        username_like=username, status=status, keyword=keyword,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ============================================================ admin: permissions

@router.get("/api/admin/permissions")
def api_admin_list_perms(_: User = Depends(require_admin)) -> dict[str, Any]:
    store = get_permissions_store()
    users = get_auth_store().list_users()
    all_perms = store.list_all()
    return {
        "items": [
            {
                "user_id": u.id, "username": u.username, "role": u.role,
                "row_rules":       all_perms.get(u.id, {}).get("row_rules") or {},
                "allowed_tables":  all_perms.get(u.id, {}).get("allowed_tables") or [],
                "allowed_columns": all_perms.get(u.id, {}).get("allowed_columns") or {},
                "deny_by_default": bool(all_perms.get(u.id, {}).get("deny_by_default")),
            }
            for u in users
        ]
    }


@router.get("/api/admin/permissions/{user_id}")
def api_admin_get_perms(user_id: str, _: User = Depends(require_admin)) -> dict[str, Any]:
    b = get_permissions_store().get_for_user(user_id)
    return {
        "user_id": user_id,
        "row_rules": b.row_rules,
        "allowed_tables": b.allowed_tables,
        "allowed_columns": b.allowed_columns,
        "deny_by_default": b.deny_by_default,
    }


@router.put("/api/admin/permissions/{user_id}")
def api_admin_put_perms(user_id: str, req: PermissionsPutReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
    from app.core.orchestrator import get_pipeline
    pipe = get_pipeline()
    valid_dims = set(pipe.semantic.dimensions.keys())
    valid_tables = set(pipe.semantic.tables.keys())
    if req.row_rules:
        unknown = [d for d in req.row_rules.keys() if d not in valid_dims]
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知维度: {unknown}")
    if req.allowed_tables:
        unknown = [t for t in req.allowed_tables if t not in valid_tables]
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知数据表: {unknown}")
    if req.allowed_columns:
        for tbl in req.allowed_columns.keys():
            if tbl not in valid_tables:
                raise HTTPException(status_code=400, detail=f"未知数据表: {tbl}")
    get_permissions_store().set_for_user(
        user_id,
        row_rules=req.row_rules,
        allowed_tables=req.allowed_tables,
        allowed_columns=req.allowed_columns,
        deny_by_default=req.deny_by_default,
    )
    return {"ok": True}
