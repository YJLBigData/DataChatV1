"""多套 LLM 预设（preset）持久化 —— 支持「不同 AK + 不同模型」并存切换。

设计：
  · SQLite 单表 llm_presets；id=uuid；is_default 全表唯一为 1（同步 set 时其他清零）
  · provider='bailian' 时需要 api_key + base_url + model[+embed_model]
  · provider='feihe' 时不存 api_key（AES_KEY 走服务器 .env，全服一份）
  · 读写都过这层；router 通过 get_active_preset(preset_id) 拿到要用的那一套
  · GET 全部脱敏（api_key 只回前 3 后 4）；写入不回回完整密钥
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.llm_presets")


@dataclass
class LLMPreset:
    id: str
    name: str
    provider: str            # 'bailian' | 'feihe'
    api_key: str = ""        # bailian 才有
    base_url: str = ""       # bailian 才有
    model: str = ""          # chat 模型名
    embed_model: str = ""    # bailian 才用
    is_default: bool = False
    is_active: bool = True
    last_tested_at: float = 0.0
    last_test_ok: bool = False
    last_test_response: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @staticmethod
    def _mask(v: str) -> str:
        if not v:
            return ""
        if len(v) <= 8:
            return "****"
        return v[:3] + "****" + v[-4:]

    def to_dict_masked(self) -> dict[str, Any]:
        d = asdict(self)
        d["api_key"] = self._mask(self.api_key) if self.api_key else ""
        d["api_key_set"] = bool(self.api_key)
        return d


_ALLOWED_PROVIDERS = {"bailian", "feihe"}


class LLMPresetsStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS llm_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    api_key TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    embed_model TEXT NOT NULL DEFAULT '',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_tested_at REAL NOT NULL DEFAULT 0,
                    last_test_ok INTEGER NOT NULL DEFAULT 0,
                    last_test_response TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_llm_presets_active
                    ON llm_presets(is_active, is_default);
                """
            )

    @staticmethod
    def _row_to_preset(r: sqlite3.Row) -> LLMPreset:
        return LLMPreset(
            id=r["id"], name=r["name"], provider=r["provider"],
            api_key=r["api_key"] or "", base_url=r["base_url"] or "",
            model=r["model"] or "", embed_model=r["embed_model"] or "",
            is_default=bool(r["is_default"]), is_active=bool(r["is_active"]),
            last_tested_at=float(r["last_tested_at"] or 0),
            last_test_ok=bool(r["last_test_ok"]),
            last_test_response=r["last_test_response"] or "",
            created_at=float(r["created_at"] or 0),
            updated_at=float(r["updated_at"] or 0),
        )

    # ------------- public API -------------

    def list_all(self, include_inactive: bool = False) -> list[LLMPreset]:
        with self._lock, self._conn() as c:
            if include_inactive:
                rows = c.execute("SELECT * FROM llm_presets ORDER BY is_default DESC, name ASC").fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM llm_presets WHERE is_active=1 ORDER BY is_default DESC, name ASC"
                ).fetchall()
        return [self._row_to_preset(r) for r in rows]

    def get(self, preset_id: str) -> Optional[LLMPreset]:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM llm_presets WHERE id=?", (preset_id,)).fetchone()
        return self._row_to_preset(r) if r else None

    def get_by_name(self, name: str) -> Optional[LLMPreset]:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM llm_presets WHERE name=?", (name,)).fetchone()
        return self._row_to_preset(r) if r else None

    def get_default(self) -> Optional[LLMPreset]:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT * FROM llm_presets WHERE is_active=1 AND is_default=1 LIMIT 1"
            ).fetchone()
        return self._row_to_preset(r) if r else None

    def _validate(self, name: str, provider: str, api_key: str, model: str) -> None:
        if not name or not name.strip():
            raise ValueError("name 不能为空")
        if provider not in _ALLOWED_PROVIDERS:
            raise ValueError(f"provider 必须是 {_ALLOWED_PROVIDERS} 之一")
        if not model or not model.strip():
            raise ValueError("model（chat 模型名）不能为空")
        if provider == "bailian" and not api_key.strip():
            raise ValueError("bailian provider 必须提供 api_key")

    def create(
        self, *, name: str, provider: str, api_key: str = "", base_url: str = "",
        model: str = "", embed_model: str = "",
    ) -> LLMPreset:
        self._validate(name, provider, api_key, model)
        now = time.time()
        pid = uuid.uuid4().hex
        with self._lock, self._conn() as c:
            # 首条记录自动设为默认
            existing = c.execute("SELECT COUNT(*) FROM llm_presets WHERE is_active=1").fetchone()[0]
            is_default = 1 if existing == 0 else 0
            try:
                c.execute(
                    "INSERT INTO llm_presets("
                    "id,name,provider,api_key,base_url,model,embed_model,"
                    "is_default,is_active,created_at,updated_at) VALUES("
                    "?,?,?,?,?,?,?,?,1,?,?)",
                    (pid, name.strip(), provider, api_key, base_url, model.strip(),
                     embed_model, is_default, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"预设名已存在: {name}") from exc
        return self.get(pid)  # type: ignore[return-value]

    def update(
        self, preset_id: str, *,
        name: Optional[str] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,        # None=不动；""=清空；非空=替换
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        embed_model: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> LLMPreset:
        cur = self.get(preset_id)
        if cur is None:
            raise ValueError(f"preset 不存在: {preset_id}")
        new_name = (name if name is not None else cur.name).strip()
        new_provider = provider if provider is not None else cur.provider
        new_api_key = cur.api_key if api_key is None else api_key
        new_base_url = cur.base_url if base_url is None else base_url
        new_model = (model if model is not None else cur.model).strip()
        new_embed = cur.embed_model if embed_model is None else embed_model
        new_active = cur.is_active if is_active is None else is_active
        self._validate(new_name, new_provider, new_api_key, new_model)
        with self._lock, self._conn() as c:
            try:
                c.execute(
                    "UPDATE llm_presets SET name=?,provider=?,api_key=?,base_url=?,"
                    "model=?,embed_model=?,is_active=?,updated_at=? WHERE id=?",
                    (new_name, new_provider, new_api_key, new_base_url, new_model,
                     new_embed, 1 if new_active else 0, time.time(), preset_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"预设名已存在: {new_name}") from exc
        return self.get(preset_id)  # type: ignore[return-value]

    def delete(self, preset_id: str) -> None:
        """软删除：is_active=0；若它是 default，自动把另一活跃记录提为 default。"""
        with self._lock, self._conn() as c:
            cur = c.execute("SELECT is_default FROM llm_presets WHERE id=?", (preset_id,)).fetchone()
            if not cur:
                return
            was_default = bool(cur["is_default"])
            c.execute("UPDATE llm_presets SET is_active=0, is_default=0, updated_at=? WHERE id=?",
                      (time.time(), preset_id))
            if was_default:
                # 自动把任意一个活跃 preset 提为 default
                row = c.execute("SELECT id FROM llm_presets WHERE is_active=1 ORDER BY created_at ASC LIMIT 1").fetchone()
                if row:
                    c.execute("UPDATE llm_presets SET is_default=1, updated_at=? WHERE id=?",
                              (time.time(), row["id"]))

    def set_default(self, preset_id: str) -> None:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT id FROM llm_presets WHERE id=? AND is_active=1", (preset_id,)).fetchone()
            if not r:
                raise ValueError(f"preset 不存在或已停用: {preset_id}")
            now = time.time()
            c.execute("UPDATE llm_presets SET is_default=0, updated_at=? WHERE is_default=1", (now,))
            c.execute("UPDATE llm_presets SET is_default=1, updated_at=? WHERE id=?", (now, preset_id))

    def record_test(self, preset_id: str, ok: bool, response: str) -> None:
        snippet = (response or "")[:500]
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE llm_presets SET last_tested_at=?, last_test_ok=?, last_test_response=?,"
                "updated_at=? WHERE id=?",
                (time.time(), 1 if ok else 0, snippet, time.time(), preset_id),
            )


_singleton: Optional[LLMPresetsStore] = None
_singleton_lock = threading.RLock()


def get_llm_presets_store() -> LLMPresetsStore:
    global _singleton
    if _singleton is not None:
        return _singleton
    import os
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        default = "/opt/datachatv1/logs/llm_presets.db"
        auth_db = os.environ.get("DATACHAT_AUTH_DB", "")
        if auth_db:
            default = str(Path(auth_db).parent / "llm_presets.db")
        path = Path(os.environ.get("DATACHAT_LLM_PRESETS_DB", default))
        _singleton = LLMPresetsStore(path)
        return _singleton
