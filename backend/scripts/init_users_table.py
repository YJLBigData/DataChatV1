#!/usr/bin/env python3
"""Initialize the server-local SQLite user and permission store.

This deployment check intentionally refuses USER_DIRECTORY=company: user
accounts and data permissions are stored on the application server, not in the
business database. It prints only non-sensitive paths/counts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _load_env_file() -> None:
    env_path = BACKEND / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def main() -> int:
    _load_env_file()
    try:
        from app.core.auth import DEFAULT_ADMIN_USERNAME, get_auth_store
        from app.core.permissions import get_permissions_store
        from app.core.user_directory import company_directory_enabled
    except Exception as exc:  # noqa: BLE001
        print(f"× 导入失败：{type(exc).__name__}: {exc}")
        return 2

    if company_directory_enabled():
        print("× 当前配置仍启用 USER_DIRECTORY=company/DB_USERS_ENABLED=1，已拒绝。")
        print("  用户与权限必须使用服务器本地 SQLite；请设置 USER_DIRECTORY=local 且 DB_USERS_ENABLED=0。")
        return 3

    check_user = (sys.argv[1] if len(sys.argv) > 1 else
                  os.environ.get("INIT_CHECK_USERNAME") or DEFAULT_ADMIN_USERNAME).strip()

    try:
        auth_store = get_auth_store()
        perm_store = get_permissions_store()
        users = auth_store.list_users()
        perm_store.list_all()
    except Exception as exc:  # noqa: BLE001
        print(f"× 本地用户/权限存储初始化失败：{type(exc).__name__}: {exc}")
        return 4

    auth_path = str(getattr(auth_store, "path", "local-sqlite"))
    perm_path = str(getattr(perm_store, "path", "local-sqlite"))
    print("[init_users_table] 模式: local-sqlite")
    print(f"  ✓ 用户库可用: {auth_path} (用户数={len(users)})")
    print(f"  ✓ 权限库可用: {perm_path} (与用户库同库={'YES' if auth_path == perm_path else 'NO'})")

    who = auth_store.get_by_username(check_user)
    if who:
        print(f"  ✓ 登录账号存在 (username={who.username}, role={who.role})")
    else:
        print(f"  ! 登录账号 {check_user!r} 尚不存在，部署脚本将创建/重置该管理员。")
    print("[init_users_table] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
