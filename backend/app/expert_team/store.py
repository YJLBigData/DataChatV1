"""用户自建 skill / 专家 持久化 —— 与项目其它元数据一样落 SQLite（独立库文件）。

路径：DATACHAT_EXPERT_DB 或 backend/logs/expert_team.db。
自建 skill 表现为一个"专家"（带 persona + 方法论），可与内置专家任意组合调度。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.expert_team")


@dataclass
class UserSkill:
    id: str
    user_id: str
    name: str                 # 中文显示名
    profession: str           # 职业 / 角色定位
    instructions: str         # 方法论 / system prompt（用户填写）
    emoji: str = "✨"
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "profession": self.profession,
            "instructions": self.instructions,
            "emoji": self.emoji,
            "is_builtin": False,
            "is_director": False,
            "skills": [self.id],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ExpertTeamStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db: sqlite3.Connection | None = None
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            from app.core.sqlite_util import open_tuned
            self._db = open_tuned(self.path)
        return self._db

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_skill_v1 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    profession TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '✨',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_user_skill_user ON user_skill_v1(user_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS team_run_v1 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_team_run_user ON team_run_v1(user_id, created_at DESC);
                """
            )

    # ---------------------------------------------------------- user skills

    def create_skill(self, user_id: str, name: str, profession: str, instructions: str, emoji: str = "✨") -> UserSkill:
        now = time.time()
        sk = UserSkill(
            id="usk_" + uuid.uuid4().hex[:12],
            user_id=user_id,
            name=(name or "").strip()[:40] or "自定义专家",
            profession=(profession or "").strip()[:40] or "自定义角色",
            instructions=(instructions or "").strip()[:8000],
            emoji=(emoji or "✨").strip()[:8] or "✨",
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO user_skill_v1(id,user_id,name,profession,instructions,emoji,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (sk.id, sk.user_id, sk.name, sk.profession, sk.instructions, sk.emoji, sk.created_at, sk.updated_at),
            )
        return sk

    def update_skill(self, user_id: str, sid: str, *, name: str | None = None,
                     profession: str | None = None, instructions: str | None = None,
                     emoji: str | None = None) -> bool:
        cur = self.get_skill(user_id, sid)
        if not cur:
            return False
        cur.name = (name if name is not None else cur.name).strip()[:40] or cur.name
        cur.profession = (profession if profession is not None else cur.profession).strip()[:40] or cur.profession
        cur.instructions = (instructions if instructions is not None else cur.instructions).strip()[:8000]
        cur.emoji = (emoji if emoji is not None else cur.emoji).strip()[:8] or cur.emoji
        cur.updated_at = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE user_skill_v1 SET name=?,profession=?,instructions=?,emoji=?,updated_at=? WHERE id=? AND user_id=?",
                (cur.name, cur.profession, cur.instructions, cur.emoji, cur.updated_at, sid, user_id),
            )
        return True

    def delete_skill(self, user_id: str, sid: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM user_skill_v1 WHERE id=? AND user_id=?", (sid, user_id))
            return cur.rowcount > 0

    def get_skill(self, user_id: str, sid: str) -> UserSkill | None:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT * FROM user_skill_v1 WHERE id=? AND user_id=?", (sid, user_id)
            ).fetchone()
        return self._row(r) if r else None

    def list_skills(self, user_id: str) -> list[UserSkill]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM user_skill_v1 WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: sqlite3.Row) -> UserSkill:
        return UserSkill(
            id=r["id"], user_id=r["user_id"], name=r["name"], profession=r["profession"],
            instructions=r["instructions"], emoji=r["emoji"],
            created_at=r["created_at"], updated_at=r["updated_at"],
        )

    # -------------------------------------------------------------- run log

    def log_run(self, user_id: str, question: str, payload: dict[str, Any]) -> None:
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT INTO team_run_v1(id,user_id,question,payload_json,created_at) VALUES (?,?,?,?,?)",
                    ("run_" + uuid.uuid4().hex[:12], user_id, (question or "")[:2000],
                     json.dumps(payload, ensure_ascii=False)[:200000], time.time()),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team run log failed: %s", exc)


_store_singleton: Optional[ExpertTeamStore] = None
_store_lock = threading.RLock()


def get_expert_store() -> ExpertTeamStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is not None:
            return _store_singleton
        from app.core.config import load_config
        backend_root = load_config().app.semantic_path.parent.parent
        default_path = str(backend_root / "logs" / "expert_team.db")
        path = Path(os.environ.get("DATACHAT_EXPERT_DB", default_path))
        _store_singleton = ExpertTeamStore(path)
        return _store_singleton
