"""数据导出 job 持久化 —— SQLite（独立库文件，与其它元数据并列）。

job 记录是真相源：状态 queued/running/ready/error/expired + 文件路径 + 行数 + 归属。
进程重启时把残留的 queued/running 收敛为 error（上次进程已退出）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ExportJob:
    id: str
    user_id: str
    conversation_id: str
    trace_id: str
    question: str
    status: str            # queued | running | ready | error | expired
    filename: str
    path: str
    row_count: int
    truncated: int
    error: str
    created_at: float
    updated_at: float
    expires_at: float

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "status": self.status,
            "filename": self.filename,
            "row_count": self.row_count,
            "truncated": bool(self.truncated),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


class ExportStore:
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
                CREATE TABLE IF NOT EXISTS export_job_v1 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_export_user ON export_job_v1(user_id, created_at DESC);
                """
            )
        # 启动收敛：上次进程残留的在途 job → error
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE export_job_v1 SET status='error', error='服务重启，导出中断，请重试', updated_at=? "
                "WHERE status IN ('queued','running')",
                (time.time(),),
            )

    def create(self, *, user_id: str, conversation_id: str, trace_id: str, question: str,
               filename: str, expires_at: float) -> ExportJob:
        now = time.time()
        job = ExportJob(
            id="exp_" + uuid.uuid4().hex[:14], user_id=user_id, conversation_id=conversation_id,
            trace_id=trace_id, question=(question or "")[:500], status="queued",
            filename=filename, path="", row_count=0, truncated=0, error="",
            created_at=now, updated_at=now, expires_at=expires_at,
        )
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO export_job_v1(id,user_id,conversation_id,trace_id,question,status,filename,path,"
                "row_count,truncated,error,created_at,updated_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job.id, job.user_id, job.conversation_id, job.trace_id, job.question, job.status,
                 job.filename, job.path, job.row_count, job.truncated, job.error,
                 job.created_at, job.updated_at, job.expires_at),
            )
        return job

    def update(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ",".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE export_job_v1 SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Optional[ExportJob]:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM export_job_v1 WHERE id=?", (job_id,)).fetchone()
        return ExportJob(**dict(r)) if r else None

    def list_for_user(self, user_id: str, limit: int = 50) -> list[ExportJob]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM export_job_v1 WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, int(limit)),
            ).fetchall()
        return [ExportJob(**dict(r)) for r in rows]

    def delete(self, job_id: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM export_job_v1 WHERE id=?", (job_id,))
            return int(cur.rowcount or 0) > 0

    def count_active_for_user(self, user_id: str) -> int:
        """该用户在途（queued|running）导出数 —— 每用户背压（多 worker 共享同一 SQLite）。"""
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS n FROM export_job_v1 WHERE user_id=? AND status IN ('queued','running')",
                (user_id,),
            ).fetchone()
        return int(r["n"] if r else 0)

    def count_active(self) -> int:
        """全局在途（queued|running）导出数 —— 全局队列深度上限。"""
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS n FROM export_job_v1 WHERE status IN ('queued','running')"
            ).fetchone()
        return int(r["n"] if r else 0)

    def expire_due(self, now: Optional[float] = None) -> list[ExportJob]:
        """把已过期但仍标 ready 的 job 标 expired，返回它们（调用方删文件）。"""
        now = now or time.time()
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM export_job_v1 WHERE status='ready' AND expires_at>0 AND expires_at<?",
                (now,),
            ).fetchall()
            jobs = [ExportJob(**dict(r)) for r in rows]
            if jobs:
                c.execute(
                    "UPDATE export_job_v1 SET status='expired', updated_at=? "
                    "WHERE status='ready' AND expires_at>0 AND expires_at<?",
                    (now, now),
                )
        return jobs


_singleton: Optional[ExportStore] = None
_lock = threading.RLock()


def get_export_store() -> ExportStore:
    global _singleton
    if _singleton is not None:
        return _singleton
    with _lock:
        if _singleton is None:
            from app.core.config import load_config
            backend_root = load_config().app.semantic_path.parent.parent
            default_path = str(backend_root / "logs" / "exports.db")
            _singleton = ExportStore(Path(os.environ.get("DATACHAT_EXPORT_DB", default_path)))
        return _singleton
