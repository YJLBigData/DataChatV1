"""LLM 可热改配置（SQLite 持久化）—— 阶段 2.3 / 4.x 通用基础设施。

为什么不直接改 .env：
  · .env 改动需要 systemctl restart 才生效，体验差
  · .env 是部署脚本写的，重装 install.sh 会覆盖你前端改的值
  · .env 文件里手动 SQL 注入式编辑容易把别的键搞坏

设计要点：
  · 白名单（ALLOWED_KEYS）：只接受这些键的写入，杜绝任意 env 注入
  · 读优先级：DB 覆盖 → 环境变量 → 代码默认（cfg.llm.*）
  · 5s 内存缓存：避免高 QPS 下每次都查 SQLite
  · 写入即生效：bump 版本号让 LLMRouter 在下一次调用时拿到新值
  · 脱敏：GET 时 secret 只回前 3 后 4，绝不回完整密文
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.llm_settings")

# 只允许这些键能通过管理 API 改 —— 安全白名单
ALLOWED_KEYS: set[str] = {
    "DASHSCOPE_API_KEY",        # 百炼 AK
    "DASHSCOPE_BASE_URL",       # 百炼 base URL
    "DASHSCOPE_MODEL",          # 百炼 chat 模型名（qwen-plus / qwen-max / qwen3.6-max-preview / ...）
    "DASHSCOPE_EMBED_MODEL",    # 百炼 embedding 模型名（text-embedding-v3 / ...）
    "LLM_PROVIDER",             # 默认 provider：bailian / feihe
}
SECRET_KEYS: set[str] = {"DASHSCOPE_API_KEY"}


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "****"
    return v[:3] + "****" + v[-4:]


class LLMSettingsStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, str] = {}
        self._cache_ts: float = 0.0
        self._version: int = 0  # 写入即自增；LLMRouter 在调用前查版本可知是否要刷新
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _refresh_cache(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._cache_ts) < 5.0:
            return
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT key, value FROM llm_settings").fetchall()
        self._cache = {r["key"]: r["value"] for r in rows}
        self._cache_ts = now

    # --- public API ----------------------------------------------------------

    def get(self, key: str, env_fallback: bool = True, default: str = "") -> str:
        """读优先级：内存缓存(DB) → 环境变量 → default。"""
        self._refresh_cache()
        v = self._cache.get(key)
        if v:
            return v
        if env_fallback:
            return os.environ.get(key, default) or default
        return default

    def get_all_effective(self) -> dict[str, dict[str, Any]]:
        """前端 GET 用：每个键返回当前生效值（脱敏 secret）+ 来源 + 是否已设置。"""
        self._refresh_cache(force=True)
        out: dict[str, dict[str, Any]] = {}
        for k in ALLOWED_KEYS:
            db_v = self._cache.get(k, "")
            env_v = os.environ.get(k, "")
            effective = db_v or env_v
            is_secret = k in SECRET_KEYS
            out[k] = {
                "value": _mask(effective) if (is_secret and effective) else effective,
                "is_secret": is_secret,
                "is_set": bool(effective),
                "source": "db" if db_v else ("env" if env_v else "default"),
            }
        return out

    def set_many(self, updates: dict[str, str]) -> list[str]:
        """批量写。空字符串/None 视为"清除该键"（回退到 env 或默认）。
        返回真正被修改的键名列表。"""
        changed: list[str] = []
        with self._lock, self._conn() as c:
            for k, v in updates.items():
                if k not in ALLOWED_KEYS:
                    continue
                if v is None or v == "":
                    cur = c.execute("DELETE FROM llm_settings WHERE key=?", (k,))
                    if cur.rowcount > 0:
                        changed.append(k)
                else:
                    # 用 INSERT OR REPLACE 兼容 CentOS7 老 SQLite 3.7（不支持 ON CONFLICT DO UPDATE）
                    c.execute(
                        "INSERT OR REPLACE INTO llm_settings(key, value, updated_at) VALUES(?,?,?)",
                        (k, str(v), time.time()),
                    )
                    changed.append(k)
        # 强制下次读重新拉 DB
        self._cache_ts = 0.0
        self._version += 1
        if changed:
            # 不打印任何 value（含 secret 在内一律不回显）
            logger.info("llm_settings updated: %s", changed)
        return changed

    @property
    def version(self) -> int:
        return self._version


_store_singleton: Optional[LLMSettingsStore] = None
_store_lock = threading.RLock()


def get_llm_settings_store() -> LLMSettingsStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is not None:
            return _store_singleton
        # 默认放到本地用户/权限同目录，方便 SQLite 集中备份
        default = "/opt/datachatv1/logs/llm_settings.db"
        auth_db = os.environ.get("DATACHAT_AUTH_DB", "")
        if auth_db:
            default = str(Path(auth_db).parent / "llm_settings.db")
        path = Path(os.environ.get("DATACHAT_LLM_SETTINGS_DB", default))
        _store_singleton = LLMSettingsStore(path)
        return _store_singleton
