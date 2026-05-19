"""问数审计日志 — 每一次 /api/chat 落地一条记录。

用途：
  · 管理员日志页查看历史问数
  · 排查 trace_id 对应的 SQL/计划/耗时
  · 后续做 Golden Case 沉淀的素材源

存储：SQLite 单文件 backend/logs/query_log.db，与会话库同级。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.query_log")


@dataclass
class QueryLogEntry:
    id: str
    trace_id: str
    user_id: str
    username: str
    conversation_id: str
    question: str
    metric: str
    table: str
    sql: str
    rows: int
    elapsed_ms: int
    cached: bool
    needs_clarify: bool
    status: str           # ok | clarify | error
    error: str
    created_at: float


class QueryLogStore:
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
                CREATE TABLE IF NOT EXISTS query_log (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    sql TEXT NOT NULL,
                    rows INTEGER NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    cached INTEGER NOT NULL,
                    needs_clarify INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            # 先把旧库缺失列补齐，再建索引（否则旧库上 idx_log_user 引用 user_id 会失败）
            self._migrate_schema(c)
            self._rebuild_legacy_schema_if_needed(c)
            c.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_log_created ON query_log(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_log_user    ON query_log(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_log_trace   ON query_log(trace_id);
                """
            )

    # 旧库（缺 username / plan_json 等列）幂等迁移：仅 ADD COLUMN，绝不清空历史。
    _EXPECTED_COLUMNS: list[tuple[str, str]] = [
        ("trace_id", "TEXT NOT NULL DEFAULT ''"),
        ("user_id", "TEXT NOT NULL DEFAULT ''"),
        ("username", "TEXT NOT NULL DEFAULT ''"),
        ("conversation_id", "TEXT NOT NULL DEFAULT ''"),
        ("question", "TEXT NOT NULL DEFAULT ''"),
        ("metric", "TEXT NOT NULL DEFAULT ''"),
        ("table_name", "TEXT NOT NULL DEFAULT ''"),
        ("sql", "TEXT NOT NULL DEFAULT ''"),
        ("rows", "INTEGER NOT NULL DEFAULT 0"),
        ("elapsed_ms", "INTEGER NOT NULL DEFAULT 0"),
        ("cached", "INTEGER NOT NULL DEFAULT 0"),
        ("needs_clarify", "INTEGER NOT NULL DEFAULT 0"),
        ("status", "TEXT NOT NULL DEFAULT 'ok'"),
        ("error", "TEXT NOT NULL DEFAULT ''"),
        ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("created_at", "REAL NOT NULL DEFAULT 0"),
    ]

    def _migrate_schema(self, c: sqlite3.Connection) -> None:
        try:
            existing = {row[1] for row in c.execute("PRAGMA table_info(query_log)").fetchall()}
        except Exception as exc:
            logger.warning("query_log PRAGMA table_info failed: %s", exc)
            return
        if not existing:
            return  # 全新库由 CREATE TABLE 建好，无需迁移
        for col, ddl in self._EXPECTED_COLUMNS:
            if col not in existing:
                try:
                    c.execute(f"ALTER TABLE query_log ADD COLUMN {col} {ddl}")
                    logger.info("query_log migrated: added column %s", col)
                except Exception as exc:
                    logger.warning("query_log add column %s failed: %s", col, exc)

    def _rebuild_legacy_schema_if_needed(self, c: sqlite3.Connection) -> None:
        """旧 DataChatV1 审计库可能是 integer id + ISO created_at。

        新版本写入 UUID 文本主键；如果继续沿用 integer id 表，INSERT 会触发
        sqlite `datatype mismatch`，进而导致问数可用但审计日志永远写不进去。
        这里做一次保守重建：保留能映射的旧字段，统一落到规范 schema。
        """
        info = {row[1]: (row[2] or "").upper() for row in c.execute("PRAGMA table_info(query_log)").fetchall()}
        id_type = info.get("id", "")
        created_type = info.get("created_at", "")
        if id_type == "TEXT" and created_type == "REAL":
            return

        try:
            rows = [dict(r) for r in c.execute("SELECT * FROM query_log").fetchall()]
        except Exception as exc:
            logger.warning("query_log legacy rebuild skipped, read failed: %s", exc)
            return

        logger.info("query_log legacy schema rebuild: id=%s created_at=%s rows=%s", id_type, created_type, len(rows))
        c.executescript(
            """
            DROP TABLE IF EXISTS query_log_rebuild;
            CREATE TABLE query_log_rebuild (
                id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                question TEXT NOT NULL,
                metric TEXT NOT NULL,
                table_name TEXT NOT NULL,
                sql TEXT NOT NULL,
                rows INTEGER NOT NULL,
                elapsed_ms INTEGER NOT NULL,
                cached INTEGER NOT NULL,
                needs_clarify INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )

        insert_sql = (
            "INSERT INTO query_log_rebuild("
            "id, trace_id, user_id, username, conversation_id, question,"
            " metric, table_name, sql, rows, elapsed_ms,"
            " cached, needs_clarify, status, error, plan_json, created_at"
            ") VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)"
        )
        for old in rows:
            error = _first_text(old, "error", "error_msg")
            needs_clarify = _truthy(old.get("needs_clarify")) or str(old.get("status") or "").lower() == "clarify"
            # 审计可信度(P2)：旧库无明确信号时标 'legacy'，不再误标成功 'ok'
            status = _first_text(old, "status") or ("error" if error else ("clarify" if needs_clarify else "legacy"))
            c.execute(
                insert_sql,
                (
                    _first_text(old, "id") or uuid.uuid4().hex,
                    _first_text(old, "trace_id") or uuid.uuid4().hex,
                    _first_text(old, "user_id") or "legacy",
                    _first_text(old, "username") or "(legacy)",
                    _first_text(old, "conversation_id") or "",
                    _first_text(old, "question", "raw_input", "original_query") or "",
                    _first_text(old, "metric") or "",
                    _first_text(old, "table_name") or "",
                    _first_text(old, "sql", "rewritten_query", "original_query") or "",
                    _first_int(old, "rows", "result_row_count"),
                    _first_int(old, "elapsed_ms"),
                    1 if _truthy(old.get("cached")) else 0,
                    1 if needs_clarify else 0,
                    status if status in ("ok", "clarify", "error", "legacy") else ("error" if error else "legacy"),
                    error,
                    _first_text(old, "plan_json", "query_plan_json") or "{}",
                    _coerce_timestamp(old.get("created_at")),
                ),
            )

        c.executescript(
            """
            DROP TABLE query_log;
            ALTER TABLE query_log_rebuild RENAME TO query_log;
            """
        )

    # ----------------------------------------------------- record

    def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        username: str,
        conversation_id: str,
        question: str,
        plan: dict[str, Any],
        sql: str,
        rows: int,
        elapsed_ms: int,
        cached: bool,
        needs_clarify: bool,
        error: str = "",
    ) -> None:
        try:
            metric = str(plan.get("metric") or "")
            table = str(plan.get("table") or "")
            status = "error" if error else ("clarify" if needs_clarify else "ok")
            row = (
                uuid.uuid4().hex,
                trace_id, user_id or "unknown", username or "unknown", conversation_id, question,
                metric, table, sql, int(rows), int(elapsed_ms),
                1 if cached else 0, 1 if needs_clarify else 0,
                status, error,
                json.dumps(plan, ensure_ascii=False, default=str),
                time.time(),
            )
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT INTO query_log("
                    "id, trace_id, user_id, username, conversation_id, question,"
                    " metric, table_name, sql, rows, elapsed_ms,"
                    " cached, needs_clarify, status, error, plan_json, created_at"
                    ") VALUES (?,?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)",
                    row,
                )
        except Exception as exc:  # never fail chat because of audit logging
            logger.warning("query_log record failed: %s", exc)

    # -------------------------------------------------------- list

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        username_like: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []
        if username_like:
            where.append("username LIKE ?")
            params.append(f"%{username_like}%")
        if status and status in ("ok", "clarify", "error"):
            where.append("status = ?")
            params.append(status)
        if keyword:
            where.append("(question LIKE ? OR sql LIKE ? OR metric LIKE ?)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        with self._lock, self._conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM query_log {where_sql}", params).fetchone()[0]
            rows = c.execute(
                f"SELECT id, trace_id, user_id, username, conversation_id, question,"
                f" metric, table_name, sql, rows, elapsed_ms,"
                f" cached, needs_clarify, status, error, plan_json, created_at"
                f" FROM query_log {where_sql}"
                f" ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, int(limit), int(offset)),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["table"] = d.pop("table_name", "")
            d["cached"] = bool(d["cached"])
            d["needs_clarify"] = bool(d["needs_clarify"])
            try:
                d["plan"] = json.loads(d.pop("plan_json") or "{}")
            except Exception:
                d["plan"] = {}
            items.append(d)
        return items, int(total)


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _first_int(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "ok"}


def _coerce_timestamp(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


_store_singleton: Optional[QueryLogStore] = None
_lock = threading.RLock()


def get_query_log_store() -> QueryLogStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _lock:
        if _store_singleton is not None:
            return _store_singleton
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        path = Path(os.environ.get("DATACHAT_QUERY_LOG_DB", str(backend_root / "logs" / "query_log.db")))
        _store_singleton = QueryLogStore(path)
        return _store_singleton
