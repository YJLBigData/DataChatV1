"""公司业务库用户目录适配（部署后 users 表来源）。

启用条件（二选一，默认不启用 → 本地/测试仍用 SQLite AuthStore）：
  · 环境变量 USER_DIRECTORY=company
  · 或 DB_USERS_ENABLED=1

users 表结构（公司 hs_poc.users）：
  user_id INTEGER PK, username TEXT UNIQUE, display_name TEXT,
  password_hash TEXT(bcrypt), role TEXT(super_admin/admin/user),
  must_change_password INTEGER, is_active INTEGER,
  created_at TEXT, last_login TEXT, feishu_user_id TEXT,
  org_code TEXT, department TEXT

安全：
  · 仅环境变量提供连接串，绝不硬编码。
  · 绝不返回 / 记录 password_hash。
  · is_active != 1 一律拒绝登录。
  · JWT 复用与 SQLite 版相同的 HS256 + resolve_jwt_secret。
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import bcrypt
import jwt

from app.core.auth import (
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_USERNAME,
    JWT_ALG,
    JWT_TTL_HOURS,
    AuthError,
    User,
    generate_initial_password,
    is_password_strong,
    resolve_jwt_secret,
)

logger = logging.getLogger("datachat.user_directory")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ADMIN_ROLES = {"admin", "super_admin"}


def company_directory_enabled() -> bool:
    mode = (os.environ.get("USER_DIRECTORY") or "").strip().lower()
    if mode:
        return mode == "company"
    return (os.environ.get("DB_USERS_ENABLED") or "").strip() in ("1", "true", "True")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompanyAuthStore:
    """与 SQLite AuthStore 暴露相同的接口子集，供 get_auth_store() 透明替换。

    main.py 仅用到：authenticate / issue_token / verify_token /
    get_by_id / get_by_username / set_password。
    """

    def __init__(self, *, engine=None, table_name: Optional[str] = None, secret: Optional[str] = None):
        from sqlalchemy import create_engine

        self.table = table_name or os.environ.get("USER_TABLE_NAME", "users")
        if not _IDENT_RE.match(self.table):
            raise AuthError(f"非法用户表名: {self.table!r}")
        self.secret = secret or resolve_jwt_secret()
        if engine is not None:
            self.engine = engine
        else:
            url = os.environ.get("DATABASE_URL", "").strip()
            if not url:
                from app.core.config import load_config
                url = load_config().mysql.sqlalchemy_url
            self.engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800)
        self._has_email = False
        self._init_schema()
        self._has_email = self._detect_email_column()
        self._ensure_admin()

    def _table_exists(self) -> bool:
        """只读探测用户表是否存在（仅需 SELECT 权限）。

        公司业务库账号通常只有 SELECT —— `CREATE TABLE IF NOT EXISTS` 即使表已存在，
        MySQL 也会先校验 CREATE 权限而拒绝。所以建表前必须先用只读方式判断存在性。
        """
        try:
            from sqlalchemy import inspect as _sa_inspect
            if _sa_inspect(self.engine).has_table(self.table):
                return True
        except Exception as exc:
            logger.warning("inspect has_table failed, fallback to SELECT probe: %s", exc)
        from sqlalchemy import text
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"SELECT 1 FROM {self.table} LIMIT 1"))
            return True
        except Exception:
            return False

    def _init_schema(self) -> None:
        from sqlalchemy import text

        # 表已存在 → 绝不执行任何 DDL（登录只需 SELECT）。
        # email 列缺失则由 _detect_email_column() 降级（按 username/admin 推导），不阻断登录。
        if self._table_exists():
            return

        dialect = self.engine.dialect.name
        id_ddl = "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect == "sqlite" else "BIGINT PRIMARY KEY AUTO_INCREMENT"
        try:
            with self.engine.begin() as conn:
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {self.table} ("
                    f"user_id {id_ddl}, "
                    "username VARCHAR(255) NOT NULL UNIQUE, "
                    "display_name VARCHAR(255) NOT NULL DEFAULT '', "
                    "email VARCHAR(320) NOT NULL DEFAULT '', "
                    "password_hash VARCHAR(255) NOT NULL, "
                    "role VARCHAR(32) NOT NULL DEFAULT 'user', "
                    "must_change_password INTEGER NOT NULL DEFAULT 0, "
                    "is_active INTEGER NOT NULL DEFAULT 1, "
                    "created_at VARCHAR(64) NOT NULL, "
                    "last_login VARCHAR(64), "
                    "feishu_user_id VARCHAR(255) NOT NULL DEFAULT '', "
                    "org_code VARCHAR(255) NOT NULL DEFAULT '', "
                    "department VARCHAR(255) NOT NULL DEFAULT ''"
                    ")"
                ))
        except Exception as exc:
            raise AuthError(
                f"用户表 {self.table!r} 不存在，且当前数据库账号无建表权限。"
                f"请由 DBA 预建该表（或对该账号授予 {self.table} 表的写权限）。原始错误：{exc}"
            ) from exc

    def _detect_email_column(self) -> bool:
        try:
            from sqlalchemy import inspect as _sa_inspect
            cols = {c["name"] for c in _sa_inspect(self.engine).get_columns(self.table)}
            return "email" in cols
        except Exception as exc:
            logger.warning("detect email column failed: %s", exc)
            return False

    def _cols(self) -> str:
        base = (
            "user_id, username, display_name, password_hash, role, "
            "must_change_password, is_active, created_at, last_login, "
            "feishu_user_id, org_code, department"
        )
        return base + (", email" if self._has_email else "")

    def _ensure_admin(self) -> None:
        # 公司业务库通常由公司统一管理、对本账号只读：查不到/建不了 admin 都属正常，
        # 绝不能因此让用户目录初始化失败（否则全员无法登录）。
        try:
            if self.get_by_username(DEFAULT_ADMIN_USERNAME):
                return
        except Exception as exc:
            logger.warning("_ensure_admin: 查询 admin 失败（忽略，不阻断启动）: %s", exc)
            return
        pwd_env = os.environ.get("DATACHAT_ADMIN_PASSWORD") or ""
        pwd = pwd_env or generate_initial_password()
        generated = not pwd_env
        try:
            self.create_user(
                DEFAULT_ADMIN_USERNAME,
                pwd,
                role="admin",
                email=os.environ.get("DATACHAT_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL,
                must_change_password=generated,
                enforce_strength=False,
            )
            if generated:
                logger.warning(
                    "未检测到 DATACHAT_ADMIN_PASSWORD，已为内置 admin 生成一次性初始密码：%s（请立即登录并修改）",
                    pwd,
                )
        except Exception as exc:
            logger.warning(
                "_ensure_admin: 无法自动创建内置 admin（用户目录可能为只读/外部托管）: %s", exc,
            )

    # -------------------------------------------------------- internal

    def _row_to_user(self, row: dict) -> User:
        raw_role = str(row.get("role") or "user").strip().lower()
        eff_role = "admin" if raw_role in _ADMIN_ROLES else "user"
        created_at = _parse_created_at(row.get("created_at"))
        _uname = str(row.get("username") or "")
        # 真实邮箱（用于飞书个人推送），优先级：
        #  1) 显式存储的 email 列（创建/编辑时写入，飞书邮箱可与 username 不同）；
        #  2) username 本身是邮箱(含@) → 直接用；
        #  3) 内置 admin → DATACHAT_ADMIN_EMAIL / DEFAULT_ADMIN_EMAIL；
        #  4) 其它 → 空（不再用 username 冒充邮箱）。
        _email = str(row.get("email") or "").strip()
        if not _email:
            if "@" in _uname:
                _email = _uname
            elif _uname == DEFAULT_ADMIN_USERNAME:
                _email = os.environ.get("DATACHAT_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL
            else:
                _email = ""
        u = User(
            id=str(row.get("user_id")),
            username=_uname,
            role=eff_role,
            created_at=created_at,
            email=_email,
            must_change_password=bool(int(row.get("must_change_password") or 0)),
        )
        # 附加非敏感字段（绝不含 password_hash）
        u.display_name = str(row.get("display_name") or "")  # type: ignore[attr-defined]
        u.raw_role = raw_role                                  # type: ignore[attr-defined]
        u.is_active = bool(int(row.get("is_active") or 0))     # type: ignore[attr-defined]
        u.feishu_user_id = str(row.get("feishu_user_id") or "")  # type: ignore[attr-defined]
        return u

    def _fetch(self, where_col: str, value) -> Optional[dict]:
        from sqlalchemy import text

        sql = text(
            f"SELECT {self._cols()} "
            f"FROM {self.table} WHERE {where_col} = :v LIMIT 1"
        )
        with self.engine.connect() as conn:
            r = conn.execute(sql, {"v": value}).mappings().first()
        return dict(r) if r else None

    # -------------------------------------------------------- public API

    def get_by_username(self, username: str) -> Optional[User]:
        if not username:
            return None
        row = self._fetch("username", username.strip().lower())
        return self._row_to_user(row) if row else None

    def get_by_id(self, uid: str) -> Optional[User]:
        if uid is None:
            return None
        row = self._fetch("user_id", uid)
        return self._row_to_user(row) if row else None

    def authenticate(self, username: str, password: str) -> User:
        if not username or not password:
            raise AuthError("用户名或密码错误")
        row = self._fetch("username", username.strip().lower())
        if not row:
            raise AuthError("用户名或密码错误")
        if not bool(int(row.get("is_active") or 0)):
            raise AuthError("账号已停用，请联系管理员")
        ph = str(row.get("password_hash") or "")
        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), ph.encode("utf-8"))
        except Exception:
            ok = False
        if not ok:
            raise AuthError("用户名或密码错误")
        self._touch_last_login(row.get("user_id"))
        return self._row_to_user(row)

    def _touch_last_login(self, user_id) -> None:
        from sqlalchemy import text

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE {self.table} SET last_login = :ts WHERE user_id = :id"),
                    {"ts": _now_iso(), "id": user_id},
                )
        except Exception as exc:  # 不能因为审计字段更新失败而阻断登录
            logger.warning("update last_login failed: %s", exc)

    def list_users(self) -> list[User]:
        from sqlalchemy import text

        sql = text(
            f"SELECT {self._cols()} "
            f"FROM {self.table} ORDER BY user_id ASC"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
        return [self._row_to_user(dict(r)) for r in rows]

    def create_user(self, username: str, password: str, role: str = "user",
                    *, email: str = "", must_change_password: bool = False,
                    enforce_strength: bool = True) -> User:
        from sqlalchemy import text

        if not username or not password:
            raise AuthError("用户名和密码不能为空")
        if enforce_strength:
            ok, msg = is_password_strong(password)
            if not ok:
                raise AuthError(msg)
        username = username.strip().lower()
        if self.get_by_username(username):
            raise AuthError(f"用户已存在: {username}")
        db_role = "admin" if role in _ADMIN_ROLES else "user"
        h = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
        email = (email or "").strip()
        cols = ("username, display_name, password_hash, role, must_change_password, "
                "is_active, created_at, last_login, feishu_user_id, org_code, department")
        vals = (":username, :display_name, :password_hash, :role, :must_change_password, "
                "1, :created_at, NULL, '', '', ''")
        params = {
            "username": username,
            "display_name": email or username,
            "password_hash": h,
            "role": db_role,
            "must_change_password": 1 if must_change_password else 0,
            "created_at": _now_iso(),
        }
        if self._has_email:
            cols += ", email"
            vals += ", :email"
            params["email"] = email
        with self.engine.begin() as conn:
            res = conn.execute(
                text(f"INSERT INTO {self.table}({cols}) VALUES ({vals})"),
                params,
            )
            uid = str(getattr(res, "lastrowid", "") or "")
        return self.get_by_id(uid) if uid else self.get_by_username(username)  # type: ignore[return-value]

    def delete_user(self, username: str) -> None:
        from sqlalchemy import text

        username = username.strip().lower()
        if username == DEFAULT_ADMIN_USERNAME:
            raise AuthError("不能删除默认管理员")
        with self.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.table} WHERE username = :u"), {"u": username})

    def set_email(self, username: str, email: str) -> None:
        from sqlalchemy import text

        username = username.strip().lower()
        email = (email or "").strip()
        if self._has_email:
            set_clause = "email = :email, display_name = :dn"
        else:
            set_clause = "display_name = :dn"
        if not self.get_by_username(username):
            raise AuthError(f"用户不存在: {username}")
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {self.table} SET {set_clause} WHERE username = :u"),
                {"email": email, "dn": email or username, "u": username},
            )

    def set_password(self, username: str, new_password: str, *, enforce_strength: bool = True,
                     clear_must_change: bool = True) -> None:
        from sqlalchemy import text

        if not new_password:
            raise AuthError("密码不能为空")
        if enforce_strength:
            ok, msg = is_password_strong(new_password)
            if not ok:
                raise AuthError(msg)
        h = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
        sets = "password_hash = :h"
        if clear_must_change:
            sets += ", must_change_password = 0"
        with self.engine.begin() as conn:
            res = conn.execute(
                text(f"UPDATE {self.table} SET {sets} WHERE username = :u"),
                {"h": h, "u": username.strip().lower()},
            )
            if res.rowcount == 0:
                raise AuthError(f"用户不存在: {username}")

    # JWT —— 与 SQLite 版一致；payload 含 user_id/username/display_name/role/
    # must_change_password，绝不含 password_hash 或任何密钥。
    def issue_token(self, user: User) -> str:
        now = int(time.time())
        payload = {
            "sub": user.id,
            "user_id": user.id,
            "username": user.username,
            "display_name": getattr(user, "display_name", ""),
            "role": user.role,
            "must_change_password": bool(user.must_change_password),
            "iat": now,
            "exp": now + JWT_TTL_HOURS * 3600,
        }
        return jwt.encode(payload, self.secret, algorithm=JWT_ALG)

    def verify_token(self, token: str) -> User:
        if not token:
            raise AuthError("缺少 token")
        try:
            payload = jwt.decode(token, self.secret, algorithms=[JWT_ALG])
        except jwt.ExpiredSignatureError:
            raise AuthError("token 已过期，请重新登录")
        except jwt.InvalidTokenError:
            raise AuthError("token 无效")
        uid = str(payload.get("sub") or payload.get("user_id") or "")
        user = self.get_by_id(uid)
        if not user:
            raise AuthError("用户不存在")
        if not getattr(user, "is_active", True):
            raise AuthError("账号已停用，请联系管理员")
        return user


def _parse_created_at(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0
