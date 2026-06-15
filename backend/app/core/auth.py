"""Minimal admin/user auth — bcrypt + JWT, SQLite-backed.

Single admin account is auto-created on first start using the password from env
`DATACHAT_ADMIN_PASSWORD`; if that env var is missing, a one-time random
strong password is generated, written to the boot log, and the user is asked
to change it on next login. No default plaintext password is shipped in code.
Additional users can be added via `/api/admin/users`.

Token flow:
  POST /api/login {username, password}
       → { token, user: {id, username, role} }
  Subsequent requests: header `Authorization: Bearer <token>`

Endpoints that bypass auth: /health, /api/health, /api/login, /web/*
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import bcrypt
import jwt


JWT_ALG = "HS256"
JWT_TTL_HOURS = 24 * 7
DEFAULT_ADMIN_USERNAME = "admin"
# 不在源码里写明文默认密码；env 未配置时启动期一次性随机生成（见 _ensure_admin）
DEFAULT_ADMIN_EMAIL = "admin@example.com"


def generate_initial_password() -> str:
    """一次性随机初始密码 — 至少含 1 字母 + 1 数字，且 ≥10 位，确保通过强度校验。"""
    # 取 base 部分 + 强制追加 1 数字 + 1 字母
    base = secrets.token_urlsafe(9).replace("-", "A").replace("_", "B")
    digits = "23456789"
    letters = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    candidate = base + secrets.choice(digits) + secrets.choice(letters)
    # 通过强度校验则返回，否则递归再试（极小概率）
    ok, _ = is_password_strong(candidate)
    return candidate if ok else generate_initial_password()


def is_password_strong(pwd: str) -> tuple[bool, str]:
    """企业级密码强度：≥8 位，至少含两种字符类型（字母/数字/符号）。"""
    if not pwd or len(pwd) < 8:
        return False, "密码至少 8 位"
    kinds = 0
    if re.search(r"[a-zA-Z]", pwd): kinds += 1
    if re.search(r"\d", pwd):       kinds += 1
    if re.search(r"[^\w]", pwd):    kinds += 1
    if kinds < 2:
        return False, "密码至少包含 2 类字符（字母/数字/符号）"
    if pwd.lower() in {"12345678", "password", "admin123", "qwerty"}:
        return False, "禁止使用常见弱口令"
    return True, ""


_WEAK_JWT_SECRETS = {
    "datachat-local-dev-secret", "changeme", "secret", "jwt-secret",
    "default", "please_replace_with_strong_random_secret",
}
_LOCAL_ENVS = {"local", "dev", "development", "test", "testing"}
_MIN_JWT_SECRET_LEN = 32


def resolve_jwt_secret() -> str:
    """按 APP_ENV 决定 JWT 密钥策略。

    · 本地/开发(APP_ENV in local/dev/development/test)：缺失或弱密钥仅告警，用开发默认，
      不破坏本地开发体验。
    · 其它（如 production）：必须配置强随机 JWT_SECRET（≥32 位、非默认/弱口令），
      否则启动直接失败（fail closed）。
    """
    env = (os.environ.get("APP_ENV") or "local").strip().lower()
    secret = (os.environ.get("JWT_SECRET") or "").strip()
    strong = bool(
        secret
        and secret.lower() not in _WEAK_JWT_SECRETS
        and len(secret) >= _MIN_JWT_SECRET_LEN
    )
    if strong:
        return secret
    if env in _LOCAL_ENVS:
        import logging
        logging.getLogger("datachat.auth").warning(
            "JWT_SECRET 缺失或过弱，APP_ENV=%s 为本地环境，使用开发默认密钥。"
            "生产部署必须配置强随机 JWT_SECRET（≥%d 位）。", env, _MIN_JWT_SECRET_LEN,
        )
        return secret or "datachat-local-dev-secret"
    raise RuntimeError(
        f"JWT_SECRET 未正确配置：APP_ENV={env} 非本地环境，必须设置强随机 JWT_SECRET"
        f"（长度≥{_MIN_JWT_SECRET_LEN}，且不能是默认/弱口令）。"
        "生成示例：python3 -c \"import secrets;print(secrets.token_urlsafe(48))\"，"
        "并写入服务器本地 .env（禁止提交 Git）。"
    )


@dataclass
class User:
    id: str
    username: str
    role: str           # "admin" | "user"
    created_at: float
    email: str = ""
    must_change_password: bool = False
    # 改密时刻（epoch 秒）。token 的 iat < 该值 → token 已失效（改密吊销旧 token）。
    password_changed_at: float = 0.0
    is_active: bool = True   # 停用账号（is_active=0）禁止登录、已签发 token 立即失效


class AuthError(RuntimeError):
    pass


class AuthStore:
    def __init__(self, path: str | Path, secret: str):
        self.path = str(path)
        self.secret = secret or "datachat-local-dev-secret"
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._secure_db_files()
        self._ensure_admin()
        self._secure_db_files()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    email TEXT DEFAULT '',
                    must_change_password INTEGER DEFAULT 0,
                    password_changed_at REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                );
                """
            )
            # 自动迁移：旧表加新字段
            cols = {row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()}
            if "email" not in cols:
                c.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")
            if "must_change_password" not in cols:
                c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
            if "password_changed_at" not in cols:
                c.execute("ALTER TABLE users ADD COLUMN password_changed_at REAL DEFAULT 0")
            if "is_active" not in cols:
                c.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")

    def _secure_db_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            p = Path(f"{self.path}{suffix}")
            if not p.exists():
                continue
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    def _ensure_admin(self) -> None:
        env = (os.environ.get("APP_ENV") or "local").strip().lower()
        admin = self.get_by_username(DEFAULT_ADMIN_USERNAME)
        if admin:
            if env not in _LOCAL_ENVS and (os.environ.get("DATACHAT_KEEP_DEFAULT_ADMIN") or "").strip() not in ("1", "true", "True"):
                with self._lock, self._conn() as c:
                    c.execute("DELETE FROM users WHERE username=?", (DEFAULT_ADMIN_USERNAME,))
                return
            # 如果 admin 还没有 email，写入默认 admin email
            if not admin.email:
                with self._lock, self._conn() as c:
                    c.execute("UPDATE users SET email=? WHERE username=?",
                              (DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_USERNAME))
            return
        if env not in _LOCAL_ENVS and (os.environ.get("DATACHAT_BOOTSTRAP_DEFAULT_ADMIN") or "").strip() not in ("1", "true", "True"):
            return
        pwd_env = os.environ.get("DATACHAT_ADMIN_PASSWORD") or ""
        if pwd_env:
            pwd = pwd_env
            generated = False
        else:
            pwd = generate_initial_password()
            generated = True
        if env not in _LOCAL_ENVS:
            ok, msg = is_password_strong(pwd)
            if not ok:
                raise RuntimeError(f"生产环境默认管理员密码不符合强度要求：{msg}")
        admin_email = os.environ.get("DATACHAT_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL
        self.create_user(
            DEFAULT_ADMIN_USERNAME, pwd, role="admin", email=admin_email,
            must_change_password=generated, enforce_strength=False,
        )
        if generated:
            import logging
            logging.getLogger("datachat.auth").warning(
                "未检测到 DATACHAT_ADMIN_PASSWORD，已为管理员生成一次性初始密码：%s（请立即登录并修改）",
                pwd,
            )

    # ------------------------------------------------------------- users

    def create_user(self, username: str, password: str, role: str = "user",
                    *, email: str = "", must_change_password: bool = False,
                    enforce_strength: bool = True) -> User:
        if not username or not password:
            raise AuthError("用户名和密码不能为空")
        if enforce_strength:
            ok, msg = is_password_strong(password)
            if not ok:
                raise AuthError(msg)
        username = username.strip().lower()
        if self.get_by_username(username):
            raise AuthError(f"用户已存在: {username}")
        # 邮箱校验（用户级邮箱即飞书账号；admin 默认值由 DEFAULT_ADMIN_EMAIL / 环境变量提供）
        if email and not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
            raise AuthError("邮箱格式不合法")
        uid = uuid.uuid4().hex
        now = time.time()
        h = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at, email, must_change_password, password_changed_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (uid, username, h, role, now, email or "", 1 if must_change_password else 0, now),
            )
        return User(id=uid, username=username, role=role, created_at=now,
                    email=email or "", must_change_password=must_change_password,
                    password_changed_at=now)

    def list_users(self) -> list[User]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id, username, role, created_at, COALESCE(email,'') AS email, "
                "COALESCE(must_change_password,0) AS must_change_password, "
                "COALESCE(password_changed_at,0) AS password_changed_at, "
                "COALESCE(is_active,1) AS is_active FROM users ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def _row_to_user(self, r: Any) -> User:
        return User(
            id=r["id"], username=r["username"], role=r["role"], created_at=r["created_at"],
            email=r["email"] if "email" in r.keys() else "",
            must_change_password=bool(r["must_change_password"]) if "must_change_password" in r.keys() else False,
            password_changed_at=float(r["password_changed_at"]) if "password_changed_at" in r.keys() else 0.0,
            is_active=bool(r["is_active"]) if "is_active" in r.keys() else True,
        )

    def get_by_username(self, username: str) -> Optional[User]:
        if not username:
            return None
        username = username.strip().lower()
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id, username, role, created_at, COALESCE(email,'') AS email, "
                "COALESCE(must_change_password,0) AS must_change_password, "
                "COALESCE(password_changed_at,0) AS password_changed_at, "
                "COALESCE(is_active,1) AS is_active FROM users WHERE username=?",
                (username,),
            ).fetchone()
        return self._row_to_user(r) if r else None

    def get_by_id(self, uid: str) -> Optional[User]:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id, username, role, created_at, COALESCE(email,'') AS email, "
                "COALESCE(must_change_password,0) AS must_change_password, "
                "COALESCE(password_changed_at,0) AS password_changed_at, "
                "COALESCE(is_active,1) AS is_active FROM users WHERE id=?",
                (uid,),
            ).fetchone()
        return self._row_to_user(r) if r else None

    def set_email(self, username: str, email: str) -> None:
        if email and not re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
            raise AuthError("邮箱格式不合法")
        with self._lock, self._conn() as c:
            r = c.execute("UPDATE users SET email=? WHERE username=?", (email, username.strip().lower()))
            if r.rowcount == 0:
                raise AuthError(f"用户不存在: {username}")

    def delete_user(self, username: str) -> None:
        username = username.strip().lower()
        if username == DEFAULT_ADMIN_USERNAME:
            raise AuthError("不能删除默认管理员")
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM users WHERE username=?", (username,))

    def set_active(self, username: str, is_active: bool) -> None:
        """启用/停用账号。停用后该用户无法登录、已签发 token 立即失效（见 verify_token）。"""
        username = username.strip().lower()
        if username == DEFAULT_ADMIN_USERNAME and not is_active:
            raise AuthError("不能停用默认管理员")
        with self._lock, self._conn() as c:
            cur = c.execute("UPDATE users SET is_active=? WHERE username=?", (1 if is_active else 0, username))
            if cur.rowcount == 0:
                raise AuthError(f"用户不存在: {username}")

    def set_password(self, username: str, new_password: str, *, enforce_strength: bool = True,
                     clear_must_change: bool = True) -> None:
        if not new_password:
            raise AuthError("密码不能为空")
        if enforce_strength:
            ok, msg = is_password_strong(new_password)
            if not ok:
                raise AuthError(msg)
        username = username.strip().lower()
        now = time.time()
        h = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")
        # 改密即刷新 password_changed_at → 所有签发更早的 token 立即失效（吊销旧 token）。
        with self._lock, self._conn() as c:
            if clear_must_change:
                cur = c.execute("UPDATE users SET password_hash=?, must_change_password=0, password_changed_at=? WHERE username=?", (h, now, username))
            else:
                cur = c.execute("UPDATE users SET password_hash=?, password_changed_at=? WHERE username=?", (h, now, username))
            if cur.rowcount == 0:
                raise AuthError(f"用户不存在: {username}")

    # ---------------------------------------------------------- auth flow

    def authenticate(self, username: str, password: str) -> User:
        if not username or not password:
            raise AuthError("用户名或密码错误")
        username = username.strip().lower()
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id, username, password_hash, role, created_at, "
                "COALESCE(email,'') AS email, "
                "COALESCE(must_change_password,0) AS must_change_password, "
                "COALESCE(password_changed_at,0) AS password_changed_at, "
                "COALESCE(is_active,1) AS is_active "
                "FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if not r:
            raise AuthError("用户名或密码错误")
        if not bcrypt.checkpw(password.encode("utf-8"), r["password_hash"].encode("utf-8")):
            raise AuthError("用户名或密码错误")
        if not bool(r["is_active"] if "is_active" in r.keys() else 1):
            raise AuthError("账号已停用，请联系管理员")
        return User(
            id=r["id"], username=r["username"], role=r["role"], created_at=r["created_at"],
            email=r["email"] if "email" in r.keys() else "",
            must_change_password=bool(r["must_change_password"]) if "must_change_password" in r.keys() else False,
            password_changed_at=float(r["password_changed_at"]) if "password_changed_at" in r.keys() else 0.0,
            is_active=bool(r["is_active"]) if "is_active" in r.keys() else True,
        )

    def issue_token(self, user: User) -> str:
        now = int(time.time())
        payload = {
            "sub": user.id,
            "username": user.username,
            "role": user.role,
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
        uid = str(payload.get("sub") or "")
        user = self.get_by_id(uid)
        if not user:
            raise AuthError("用户不存在")
        # 停用账号：已签发 token 立即失效（禁用即生效，不必等过期）。
        if not getattr(user, "is_active", True):
            raise AuthError("账号已停用，请联系管理员")
        # 改密吊销：token 签发时间（iat）早于最近一次改密 → 视为失效，强制重新登录。
        pwd_changed = float(getattr(user, "password_changed_at", 0) or 0)
        if pwd_changed > 0 and int(payload.get("iat") or 0) < int(pwd_changed):
            raise AuthError("密码已修改，请重新登录")
        return user


_store_singleton: Optional[AuthStore] = None
_store_lock = threading.RLock()


def get_auth_store() -> AuthStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is not None:
            return _store_singleton
        # 部署后：公司 users 表目录（env 显式开启）；否则本地 SQLite（开发/测试不受影响）
        try:
            from app.core.user_directory import company_directory_enabled
            if company_directory_enabled():
                from app.core.user_directory import CompanyAuthStore
                _store_singleton = CompanyAuthStore()
                return _store_singleton
        except Exception as exc:
            # 用户目录初始化失败必须显式暴露，绝不静默回退到本地库（防越权/串库）
            raise RuntimeError(f"公司用户目录初始化失败：{exc}") from exc
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        path = Path(os.environ.get("DATACHAT_AUTH_DB", str(backend_root / "logs" / "auth.db")))
        secret = resolve_jwt_secret()
        _store_singleton = AuthStore(path=path, secret=secret)
        return _store_singleton
