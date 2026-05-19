"""Conversation persistence (multi-turn).

Lightweight SQLite-backed store living next to the rest of v1 metadata.
Schema: sessions(id, title, user_id, created_at, updated_at) + messages(id, session_id, role, content, payload_json, plan_signature, created_at).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("datachat.conversation")


@dataclass
class Session:
    id: str
    title: str
    user_id: str
    created_at: float
    updated_at: float


@dataclass
class Message:
    id: str
    session_id: str
    role: str  # 'user' | 'assistant'
    content: str
    payload: dict[str, Any]
    plan_signature: str
    created_at: float


class ConversationStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    plan_signature TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);
                """
            )

    # --------------------------------------------------------------- sessions

    def create_session(self, user_id: str, title: str = "新会话") -> Session:
        sid = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO sessions(id,title,user_id,created_at,updated_at) VALUES (?,?,?,?,?)",
                (sid, title or "新会话", user_id or "default", now, now),
            )
        return Session(id=sid, title=title or "新会话", user_id=user_id or "default", created_at=now, updated_at=now)

    def list_sessions(self, user_id: str, limit: int = 50) -> list[Session]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id,title,user_id,created_at,updated_at FROM sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id or "default", int(limit)),
            ).fetchall()
        return [Session(**dict(r)) for r in rows]

    def get_session(self, session_id: str) -> Session | None:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id,title,user_id,created_at,updated_at FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        return Session(**dict(r)) if r else None

    def rename_session(self, session_id: str, title: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (title, time.time(), session_id))

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            c.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    # --------------------------------------------------------------- messages

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        payload: dict[str, Any] | None = None,
        plan_signature: str = "",
    ) -> Message:
        mid = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO messages(id,session_id,role,content,payload_json,plan_signature,created_at) VALUES (?,?,?,?,?,?,?)",
                (mid, session_id, role, content or "", json.dumps(payload or {}, ensure_ascii=False), plan_signature or "", now),
            )
            c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        return Message(id=mid, session_id=session_id, role=role, content=content or "", payload=payload or {}, plan_signature=plan_signature or "", created_at=now)

    def list_messages(self, session_id: str, limit: int = 200) -> list[Message]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id,session_id,role,content,payload_json,plan_signature,created_at FROM messages WHERE session_id=? ORDER BY created_at ASC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        return [
            Message(
                id=r["id"], session_id=r["session_id"], role=r["role"], content=r["content"],
                payload=json.loads(r["payload_json"] or "{}"), plan_signature=r["plan_signature"], created_at=r["created_at"],
            )
            for r in rows
        ]

    def history_for_llm(self, session_id: str, limit: int = 6) -> list[dict[str, str]]:
        msgs = self.list_messages(session_id, limit=limit * 2)
        keep = msgs[-limit:]
        return [{"role": m.role, "content": m.content} for m in keep]

    def latest_assistant_plan_signature(self, session_id: str) -> str:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT plan_signature FROM messages WHERE session_id=? AND role='assistant' AND plan_signature<>'' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return (r["plan_signature"] if r else "") or ""


_default_store: ConversationStore | None = None


def get_conversation_store(path: str | Path | None = None) -> ConversationStore:
    global _default_store
    if _default_store is not None:
        return _default_store
    if path is None:
        from app.core.config import load_config
        backend_root = load_config().app.semantic_path.parent.parent.parent
        path = Path(backend_root) / "logs" / "datachat_conversations.db"
    _default_store = ConversationStore(path)
    return _default_store
