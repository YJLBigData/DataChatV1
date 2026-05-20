"""SQL executor adapter (MySQL via SQLAlchemy + pymysql).

- Read-only by design (statement is guarded earlier; we still apply a hard read pool).
- Per-query timeout via MAX_EXECUTION_TIME hint (MySQL 5.7+) and pool timeout.
- Returns columns + rows + row_count + elapsed_ms.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.core.config import V1Config, load_config

logger = logging.getLogger("datachat.exec")


class ExecError(RuntimeError):
    pass


@dataclass
class ExecResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: int
    sql: str


_SELECT_HEAD = re.compile(r"^\s*select\b", re.IGNORECASE)


class MySQLExecutor:
    def __init__(self, cfg: V1Config | None = None):
        self.cfg = cfg or load_config()
        self.engine: Engine = create_engine(
            self.cfg.mysql.sqlalchemy_url,
            pool_size=self.cfg.mysql.pool_size,
            pool_recycle=self.cfg.mysql.pool_recycle,
            pool_pre_ping=True,
            future=True,
        )

    def health(self) -> dict[str, Any]:
        try:
            with self.engine.connect() as conn:
                value = conn.execute(text("SELECT 1")).scalar()
                return {"ok": value == 1, "database": self.cfg.mysql.database, "host": self.cfg.mysql.host}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def run_select(self, sql: str, *, max_rows: int | None = None, timeout_ms: int | None = None) -> ExecResult:
        if not _SELECT_HEAD.match(sql):
            raise ExecError("Only SELECT statements are allowed.")
        timeout_ms = int(timeout_ms or self.cfg.mysql.statement_timeout_ms)
        max_rows = int(max_rows or self.cfg.guard.max_rows)
        # MySQL 5.7+ supports the MAX_EXECUTION_TIME hint; safe even if older.
        sql_with_hint = re.sub(
            r"^\s*select\b",
            f"SELECT /*+ MAX_EXECUTION_TIME({timeout_ms}) */",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
        started = time.perf_counter()
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET SESSION group_concat_max_len = 4096"))
                result = conn.execute(text(sql_with_hint))
                columns = list(result.keys())
                rows: list[list[Any]] = []
                for row in result:
                    rows.append([_normalize_value(v) for v in row])
                    if len(rows) >= max_rows:
                        break
        except DBAPIError as exc:
            raise ExecError(f"SQL execution failed: {exc.orig if hasattr(exc, 'orig') else exc}") from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return ExecResult(columns=columns, rows=rows, row_count=len(rows), elapsed_ms=elapsed_ms, sql=sql)


def _normalize_value(value: Any) -> Any:
    import datetime as _dt
    from decimal import Decimal

    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.date, _dt.datetime, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    return value


_executor_singleton: MySQLExecutor | None = None


def get_executor() -> MySQLExecutor:
    global _executor_singleton
    if _executor_singleton is None:
        _executor_singleton = MySQLExecutor()
    return _executor_singleton
