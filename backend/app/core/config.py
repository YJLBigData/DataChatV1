"""Centralised v1 runtime configuration（本项目只有 v1，无 v2）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    return _backend_root().parent


def _coalesce_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return str(value)
    return default


@dataclass
class MySQLConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "chatbi"
    charset: str = "utf8mb4"
    pool_size: int = 5
    pool_recycle: int = 3600
    statement_timeout_ms: int = 15000

    @property
    def sqlalchemy_url(self) -> str:
        from urllib.parse import quote_plus
        pwd = quote_plus(self.password or "")
        return (
            f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}/"
            f"{self.database}?charset={self.charset}"
        )


@dataclass
class LLMConfig:
    primary_provider: str = "bailian"
    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_chat_model: str = "qwen3.6-max-preview"
    bailian_embed_model: str = "text-embedding-v3"
    # qwen3.6-max-preview is a reasoning model — single call can take 50-90s
    # under default settings. 180s gives us headroom; we also disable retries
    # so a slow call doesn't snowball into N×60s.
    timeout_seconds: int = 180
    connect_timeout_seconds: int = 10
    chat_temperature: float = 0.0
    max_tokens: int = 1500
    max_retries: int = 1
    # Some DashScope reasoning models accept `enable_thinking=False` to skip
    # the chain-of-thought stream and return ~10× faster.
    disable_thinking: bool = True


@dataclass
class CacheConfig:
    redis_url: str = "redis://127.0.0.1:6379/2"
    enabled: bool = True
    ttl_question: int = 60 * 60 * 24
    ttl_plan: int = 60 * 60 * 24 * 7
    ttl_sql_result: int = 60 * 60 * 6
    ttl_embedding: int = 60 * 60 * 24 * 30
    namespace: str = "datachat"


@dataclass
class GuardConfig:
    max_rows: int = 500
    statement_timeout_ms: int = 15000
    allow_select_only: bool = True
    forbid_multi_statement: bool = True
    require_limit: bool = True
    block_select_star: bool = True
    forbidden_keywords: list[str] = field(
        default_factory=lambda: [
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
            "CREATE", "REPLACE", "MERGE", "GRANT", "REVOKE", "RENAME",
            "LOCK", "UNLOCK", "CALL", "EXECUTE", "LOAD", "OUTFILE",
        ]
    )


@dataclass
class AppConfig:
    name: str = "DataChat"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8001
    semantic_path: Path = field(default_factory=lambda: _backend_root() / "config" / "semantic.yaml")
    log_level: str = "INFO"


@dataclass
class V1Config:
    app: AppConfig = field(default_factory=AppConfig)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    raw: dict[str, Any] = field(default_factory=dict)


_LOADED: V1Config | None = None


def _apply_env_file(path: Path) -> None:
    """把 .env 风格文件读进 os.environ（已存在的键不覆盖 → 真实环境变量优先）。"""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


def resolve_app_env() -> str:
    """决定运行环境：os.environ['APP_ENV'] > backend/.env 里的 APP_ENV > 默认 local。

    用于选择 backend/config/env/<APP_ENV>.env 这套环境专属配置
    （本地 → 本地 MySQL；服务器 → 服务器 MySQL；用户库与业务库同源）。
    """
    env = os.environ.get("APP_ENV")
    if env:
        return env.strip().lower()
    for path in (_backend_root() / ".env", _backend_root() / "config" / "runtime.local.env",
                 _project_root() / ".env"):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("APP_ENV=") and "=" in line:
                    return line.partition("=")[2].strip().strip('"').strip("'").lower() or "local"
        except Exception:
            pass
    return "local"


def _load_runtime_local_env() -> None:
    """加载顺序（高优先级在前；已存在键不被后者覆盖）：
      1) 真实 os.environ（最高）
      2) backend/.env
      3) backend/config/runtime.local.env
      4) <project>/.env
      5) backend/config/env/<APP_ENV>.env   ← 环境专属默认（本地/服务器各一份，无密钥）
    """
    app_env = resolve_app_env()
    os.environ.setdefault("APP_ENV", app_env)
    candidates = [
        _backend_root() / ".env",
        _backend_root() / "config" / "runtime.local.env",
        _project_root() / ".env",
        _backend_root() / "config" / "env" / f"{app_env}.env",
    ]
    for path in candidates:
        _apply_env_file(path)


def load_config(reload: bool = False) -> V1Config:
    global _LOADED
    if _LOADED is not None and not reload:
        return _LOADED
    _load_runtime_local_env()

    cfg = V1Config()
    cfg.app.host = os.environ.get("APP_HOST", cfg.app.host)
    cfg.app.port = int(os.environ.get("APP_PORT", cfg.app.port))
    cfg.app.log_level = os.environ.get("LOG_LEVEL", cfg.app.log_level)

    # 兼容两套环境变量名：MYSQL_*（历史）与 DB_*（部署/公司 .env 约定）。
    # 优先级：MYSQL_* > DB_* > 默认值，避免破坏既有本地配置。
    cfg.mysql.host = _coalesce_env("MYSQL_HOST", "DB_HOST", default=cfg.mysql.host)
    cfg.mysql.port = int(_coalesce_env("MYSQL_PORT", "DB_PORT", default=str(cfg.mysql.port)))
    cfg.mysql.user = _coalesce_env("MYSQL_USER", "DB_USER", default=cfg.mysql.user)
    cfg.mysql.password = _coalesce_env("MYSQL_PASSWORD", "DB_PASSWORD", default=cfg.mysql.password)
    cfg.mysql.database = _coalesce_env("MYSQL_DATABASE", "DB_NAME", default=cfg.mysql.database)

    cfg.llm.bailian_api_key = _coalesce_env("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", default="")
    cfg.llm.bailian_base_url = os.environ.get("DASHSCOPE_BASE_URL", cfg.llm.bailian_base_url)
    cfg.llm.bailian_chat_model = os.environ.get("DASHSCOPE_MODEL", cfg.llm.bailian_chat_model)
    cfg.llm.bailian_embed_model = os.environ.get("DASHSCOPE_EMBED_MODEL", cfg.llm.bailian_embed_model)

    cfg.cache.redis_url = os.environ.get("DATACHAT_REDIS_URL", cfg.cache.redis_url)
    cfg.cache.enabled = os.environ.get("DATACHAT_CACHE_ENABLED", "1") not in ("0", "false", "False")

    settings_path = _backend_root() / "config" / "settings.yaml"
    if settings_path.exists():
        try:
            cfg.raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg.raw = {}

    _LOADED = cfg
    return cfg


def reset_for_tests() -> None:
    global _LOADED
    _LOADED = None
