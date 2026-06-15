"""HTTP 鉴权依赖（从 main.py 抽出，#16）。

统一鉴权入口：普通依赖 require_user / require_admin 与 SSE 端点共用 _authenticate_or_403，
保证 must_change_password 拦截在所有入口一致（见 P0-2）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from app.core.auth import AuthError, User, get_auth_store


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


# 未改密用户仅可访问：查看自己 / 改密。其它核心接口一律 403。
_PW_CHANGE_EXEMPT_PATHS = {"/api/me", "/api/me/password"}


def _authenticate_or_403(token: str, path: str) -> User:
    """统一鉴权：verify_token + 未改初始密码拦截。

    安全（P0）：普通依赖 require_user 与 SSE /api/chat/stream（token 走 query/header）
    必须共用同一套校验，否则首次登录未改密的用户能绕过 must_change_password 限制，
    /api/chat 被 403 拦下、/api/chat/stream 却能继续问数。
    """
    try:
        user = get_auth_store().verify_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    # 后端强制：未改初始密码的用户不能访问核心接口（不依赖前端引导）
    if user.must_change_password and path not in _PW_CHANGE_EXEMPT_PATHS:
        raise HTTPException(
            status_code=403,
            detail="MUST_CHANGE_PASSWORD:请先修改初始密码后再使用系统功能",
        )
    return user


def require_user(request: Request, authorization: Optional[str] = Header(None)) -> User:
    return _authenticate_or_403(_bearer_token(authorization), request.url.path)


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
