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


class FolderNotFound(Exception):
    """收藏目标文件夹不存在或不属于该用户 —— 路由层据此返回 404。"""

    def __init__(self, folder_id: str = ""):
        super().__init__(f"folder not found: {folder_id}")
        self.folder_id = folder_id


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

    _db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            from app.core.sqlite_util import open_tuned
            self._db = open_tuned(self.path)
        return self._db

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

    def get_folder(self, user_id: str, folder_id: str) -> Optional[Folder]:
        """按 (user_id, folder_id) 取文件夹 —— 收藏前用它做归属校验，杜绝悬挂收藏。"""
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id,user_id,name,color,created_at FROM folder WHERE id=? AND user_id=?",
                (folder_id, user_id),
            ).fetchone()
        return Folder(**dict(r)) if r else None

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
        """改名（含改色）。文件夹不存在 / 不属于该用户 → FolderNotFound（不再静默 no-op）。"""
        with self._lock, self._conn() as c:
            if color is None:
                cur = c.execute("UPDATE folder SET name=? WHERE id=? AND user_id=?", (name, folder_id, user_id))
            else:
                cur = c.execute("UPDATE folder SET name=?,color=? WHERE id=? AND user_id=?",
                                (name, color, folder_id, user_id))
            if cur.rowcount <= 0:
                raise FolderNotFound(folder_id)

    def delete_folder(self, user_id: str, folder_id: str) -> None:
        """删除文件夹（连带解除其下收藏）。文件夹不存在 / 不属于该用户 → FolderNotFound。"""
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM folder WHERE id=? AND user_id=?", (folder_id, user_id))
            if cur.rowcount <= 0:
                raise FolderNotFound(folder_id)
            c.execute("DELETE FROM conversation_collection WHERE user_id=? AND folder_id=?",
                      (user_id, folder_id))

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
            # 归属校验（防御性，二次把关）：目标文件夹必须存在且属于该用户，
            # 否则会写出指向不存在文件夹的悬挂收藏记录。
            owns = c.execute(
                "SELECT 1 FROM folder WHERE id=? AND user_id=?", (folder_id, user_id)
            ).fetchone()
            if not owns:
                raise FolderNotFound(folder_id)
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

    def folder_ids_for_conversations(self, user_id: str, conversation_ids: list[str]) -> dict[str, list[str]]:
        """批量版：一次查询拿到多个会话的收藏文件夹，消除前端 N+1。返回 {conv_id: [folder_id,...]}。"""
        ids = [c for c in (conversation_ids or []) if c]
        if not ids:
            return {}
        out: dict[str, list[str]] = {}
        # SQLite 变量上限 999，分批查询稳妥
        with self._lock, self._conn() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = c.execute(
                    f"SELECT conversation_id, folder_id FROM conversation_collection "
                    f"WHERE user_id=? AND conversation_id IN ({placeholders})",
                    (user_id, *chunk),
                ).fetchall()
                for r in rows:
                    out.setdefault(r["conversation_id"], []).append(r["folder_id"])
        return out


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
