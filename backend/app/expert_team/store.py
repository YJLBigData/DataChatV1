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
                CREATE TABLE IF NOT EXISTS expert_override_v1 (
                    user_id TEXT NOT NULL,
                    expert_id TEXT NOT NULL,
                    name TEXT,
                    profession TEXT,
                    instructions TEXT,
                    emoji TEXT,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, expert_id)
                );
                CREATE TABLE IF NOT EXISTS expert_job_v1 (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    finished_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_expert_job_user ON expert_job_v1(user_id, created_at DESC);
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

    # --------------------------------------------------- builtin overrides

    def upsert_override(self, user_id: str, expert_id: str, *, name: str | None = None,
                        profession: str | None = None, instructions: str | None = None,
                        emoji: str | None = None, deleted: bool | None = None) -> dict[str, Any]:
        """对内置专家做"改/删（隐藏）"覆盖。只更新给定字段，其余沿用既有覆盖值。
        不改动磁盘上的定义文件——所有定制都落库，可随时 clear_override 还原默认。"""
        cur = self.get_override(user_id, expert_id) or {}
        merged = {
            "name": (name if name is not None else cur.get("name")),
            "profession": (profession if profession is not None else cur.get("profession")),
            "instructions": (instructions if instructions is not None else cur.get("instructions")),
            "emoji": (emoji if emoji is not None else cur.get("emoji")),
            "deleted": (1 if deleted else 0) if deleted is not None else int(cur.get("deleted") or 0),
        }
        for k in ("name", "profession", "emoji"):
            if merged[k] is not None:
                merged[k] = str(merged[k]).strip()[:40]
        if merged["instructions"] is not None:
            merged["instructions"] = str(merged["instructions"]).strip()[:8000]
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO expert_override_v1"
                "(user_id,expert_id,name,profession,instructions,emoji,deleted,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, expert_id, merged["name"], merged["profession"], merged["instructions"],
                 merged["emoji"], merged["deleted"], time.time()),
            )
        return merged

    def get_override(self, user_id: str, expert_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT * FROM expert_override_v1 WHERE user_id=? AND expert_id=?",
                (user_id, expert_id),
            ).fetchone()
        if not r:
            return None
        return {
            "name": r["name"], "profession": r["profession"], "instructions": r["instructions"],
            "emoji": r["emoji"], "deleted": int(r["deleted"] or 0),
        }

    def list_overrides(self, user_id: str) -> dict[str, dict[str, Any]]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM expert_override_v1 WHERE user_id=?", (user_id,)).fetchall()
        return {
            r["expert_id"]: {
                "name": r["name"], "profession": r["profession"], "instructions": r["instructions"],
                "emoji": r["emoji"], "deleted": int(r["deleted"] or 0),
            }
            for r in rows
        }

    def clear_override(self, user_id: str, expert_id: str) -> bool:
        """删除覆盖 = 把内置专家还原成出厂默认（含取消隐藏）。"""
        with self._lock, self._conn() as c:
            cur = c.execute(
                "DELETE FROM expert_override_v1 WHERE user_id=? AND expert_id=?",
                (user_id, expert_id),
            )
            return cur.rowcount > 0

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

    # ------------------------------------------------------ job records (审计/恢复)

    def upsert_job(self, *, job_id: str, user_id: str, conversation_id: str, question: str,
                   status: str, error: str = "", created_at: float, finished_at: float = 0.0) -> None:
        """落库后台 job 记录与最终状态（审计 + 重启后可见历史）。best-effort。"""
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO expert_job_v1"
                    "(job_id,user_id,conversation_id,question,status,error,created_at,finished_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (job_id, user_id, conversation_id, (question or "")[:2000], status,
                     (error or "")[:500], created_at, finished_at),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team job record failed: %s", exc)

    def update_job_status(self, job_id: str, status: str, error: str = "", finished_at: float = 0.0) -> None:
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "UPDATE expert_job_v1 SET status=?, error=?, finished_at=? WHERE job_id=?",
                    (status, (error or "")[:500], finished_at, job_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team job status update failed: %s", exc)

    def count_active_jobs(self, user_id: str) -> int:
        """该用户尚未结束的 job 数（用于每用户背压；多 worker 共享同一 SQLite）。"""
        try:
            with self._lock, self._conn() as c:
                r = c.execute(
                    "SELECT COUNT(*) AS n FROM expert_job_v1 WHERE user_id=? AND status IN ('queued','running')",
                    (user_id,),
                ).fetchone()
            return int(r["n"] if r else 0)
        except Exception:  # noqa: BLE001
            return 0

    def count_active_global(self) -> int:
        """全局尚未结束的 job 数（全局队列深度上限；多 worker 共享同一 SQLite）。"""
        try:
            with self._lock, self._conn() as c:
                r = c.execute(
                    "SELECT COUNT(*) AS n FROM expert_job_v1 WHERE status IN ('queued','running')"
                ).fetchone()
            return int(r["n"] if r else 0)
        except Exception:  # noqa: BLE001
            return 0

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """读单条 job 记录（跨 worker 轮询/列举用：内存里没有时回退 DB）。"""
        try:
            with self._lock, self._conn() as c:
                r = c.execute("SELECT * FROM expert_job_v1 WHERE job_id=?", (job_id,)).fetchone()
            return dict(r) if r else None
        except Exception:  # noqa: BLE001
            return None

    def list_active_jobs(self, user_id: str) -> list[dict[str, Any]]:
        """该用户在途（queued|running）job 记录（跨 worker 重挂红点/进度用）。"""
        try:
            with self._lock, self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM expert_job_v1 WHERE user_id=? AND status IN ('queued','running') "
                    "ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def reset_stale_active_jobs(self) -> None:
        """进程启动时把 DB 里仍标记 queued/running 的旧 job 收敛为 interrupted（上次进程已退出）。"""
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "UPDATE expert_job_v1 SET status='interrupted', finished_at=? "
                    "WHERE status IN ('queued','running')",
                    (time.time(),),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expert_team stale job reset failed: %s", exc)


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
