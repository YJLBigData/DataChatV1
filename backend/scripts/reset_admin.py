#!/usr/bin/env python3
"""Reset or create an admin user in the local SQLite user store.

Usage:
    .venv/bin/python scripts/reset_admin.py
    .venv/bin/python scripts/reset_admin.py -u admin@feihe.com -p 'StrongPassword@2026' -y

The script loads backend/.env, uses get_auth_store(), and never prints the
password, JWT secret, database URL, or password hash.
"""
from __future__ import annotations

import argparse
import getpass
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


_load_env_file()

from app.core.auth import AuthError, DEFAULT_ADMIN_USERNAME, get_auth_store  # noqa: E402
from app.core.user_directory import company_directory_enabled  # noqa: E402


def _read_password() -> str:
    while True:
        try:
            pwd = getpass.getpass("新密码（不会显示）: ")
            confirm = getpass.getpass("确认新密码: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            raise SystemExit(1)
        if pwd != confirm:
            print("两次输入不一致，请重试。")
            continue
        return pwd


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset/create a local admin user.")
    ap.add_argument("password_pos", nargs="?", default="", help="新密码（位置参数，向后兼容）")
    ap.add_argument("-u", "--username", default=DEFAULT_ADMIN_USERNAME, help="目标用户名，默认 admin")
    ap.add_argument("-p", "--password", default="", help="新密码；非交互模式必填")
    ap.add_argument("-y", "--non-interactive", action="store_true", help="非交互：不读 stdin")
    ap.add_argument("--must-change", action="store_true", help="下次登录后强制修改密码")
    args = ap.parse_args()

    username = (args.username or DEFAULT_ADMIN_USERNAME).strip()
    pwd = args.password or args.password_pos or ""
    if not pwd:
        if args.non_interactive:
            print("× 非交互模式必须通过 -p/--password 提供密码")
            return 1
        pwd = _read_password()

    if company_directory_enabled():
        print("× 当前配置启用了公司业务库用户目录，已拒绝重置。")
        print("  请设置 USER_DIRECTORY=local 且 DB_USERS_ENABLED=0，使用服务器本地 SQLite 用户库。")
        return 3

    try:
        store = get_auth_store()
        user = store.get_by_username(username)
        email = username if "@" in username else (os.environ.get("DATACHAT_ADMIN_EMAIL") or "")
        if user:
            store.set_password(
                username,
                pwd,
                enforce_strength=True,
                clear_must_change=not args.must_change,
            )
            if email and not getattr(user, "email", ""):
                store.set_email(username, email)
            print(f"✓ 已重置 {username} 密码")
        else:
            store.create_user(
                username,
                pwd,
                role="admin",
                email=email,
                must_change_password=bool(args.must_change),
                enforce_strength=True,
            )
            print(f"✓ 已创建管理员 {username}")
    except AuthError as exc:
        print(f"× 失败: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"× 用户存储初始化失败：{type(exc).__name__}: {exc}")
        return 3

    print(f"  存储: {getattr(store, 'path', 'local-sqlite')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
