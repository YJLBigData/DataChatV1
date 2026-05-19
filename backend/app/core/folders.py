"""会话文件夹 + 会话收藏 —— SQLite。

模型：
  folder(id, user_id, name, color, created_at)
  conversation_collection(id, user_id, conversation_id, folder_id, created_at)

约束：
  · 一个会话可以放进多个文件夹（n:m）
  · 用户隔离 — folder/collection 都带 user_id
  · 删除文件夹只解除收藏，不删原会话
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
class Folder:
    id: str
    user_id: str
    name: str
    color: str
    created_at: float


@dataclass
class Collection:
    id: str
    user_id: str
    conversation_id: str
    folder_id: str
    created_at: float


class FoldersStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS folder (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_folder_user ON folder(user_id);

                CREATE TABLE IF NOT EXISTS conversation_collection (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    folder_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(user_id, conversation_id, folder_id)
                );
                CREATE INDEX IF NOT EXISTS idx_collect_user_folder
                    ON conversation_collection(user_id, folder_id);
                CREATE INDEX IF NOT EXISTS idx_collect_user_conv
                    ON conversation_collection(user_id, conversation_id);
                """
            )

    # ----------------------------------------------------------- folders

    def list_folders(self, user_id: str) -> list[Folder]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id,user_id,name,color,created_at FROM folder WHERE user_id=? ORDER BY created_at ASC",
                (user_id,),
            ).fetchall()
        return [Folder(**dict(r)) for r in rows]

    def create_folder(self, user_id: str, name: str, color: str = "") -> Folder:
        fid = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO folder(id,user_id,name,color,created_at) VALUES (?,?,?,?,?)",
                (fid, user_id, name or "未命名", color or "", now),
            )
        return Folder(id=fid, user_id=user_id, name=name or "未命名", color=color or "", created_at=now)

    def rename_folder(self, user_id: str, folder_id: str, name: str, color: Optional[str] = None) -> None:
        with self._lock, self._conn() as c:
            if color is None:
                c.execute("UPDATE folder SET name=? WHERE id=? AND user_id=?", (name, folder_id, user_id))
            else:
                c.execute("UPDATE folder SET name=?,color=? WHERE id=? AND user_id=?",
                          (name, color, folder_id, user_id))

    def delete_folder(self, user_id: str, folder_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM conversation_collection WHERE user_id=? AND folder_id=?",
                      (user_id, folder_id))
            c.execute("DELETE FROM folder WHERE id=? AND user_id=?", (folder_id, user_id))

    # --------------------------------------------------------- collections

    def list_collections(self, user_id: str, folder_id: Optional[str] = None) -> list[Collection]:
        with self._lock, self._conn() as c:
            if folder_id:
                rows = c.execute(
                    "SELECT id,user_id,conversation_id,folder_id,created_at "
                    "FROM conversation_collection WHERE user_id=? AND folder_id=? ORDER BY created_at DESC",
                    (user_id, folder_id),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT id,user_id,conversation_id,folder_id,created_at "
                    "FROM conversation_collection WHERE user_id=? ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
        return [Collection(**dict(r)) for r in rows]

    def add(self, user_id: str, conversation_id: str, folder_id: str) -> Collection:
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn() as c:
            try:
                c.execute(
                    "INSERT OR IGNORE INTO conversation_collection(id,user_id,conversation_id,folder_id,created_at) VALUES (?,?,?,?,?)",
                    (cid, user_id, conversation_id, folder_id, now),
                )
                # 如果是 IGNORE 没插入，再查回来
                r = c.execute(
                    "SELECT id,user_id,conversation_id,folder_id,created_at FROM conversation_collection "
                    "WHERE user_id=? AND conversation_id=? AND folder_id=?",
                    (user_id, conversation_id, folder_id),
                ).fetchone()
            except Exception:
                raise
        return Collection(**dict(r))

    def remove(self, user_id: str, conversation_id: str, folder_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "DELETE FROM conversation_collection WHERE user_id=? AND conversation_id=? AND folder_id=?",
                (user_id, conversation_id, folder_id),
            )

    def folder_ids_for_conversation(self, user_id: str, conversation_id: str) -> list[str]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT folder_id FROM conversation_collection WHERE user_id=? AND conversation_id=?",
                (user_id, conversation_id),
            ).fetchall()
        return [r["folder_id"] for r in rows]


_singleton: Optional[FoldersStore] = None
_lock = threading.RLock()


def get_folders_store() -> FoldersStore:
    global _singleton
    if _singleton is not None:
        return _singleton
    with _lock:
        if _singleton is not None:
            return _singleton
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        path = Path(os.environ.get("DATACHAT_FOLDERS_DB", str(backend_root / "logs" / "folders.db")))
        _singleton = FoldersStore(path)
        return _singleton
