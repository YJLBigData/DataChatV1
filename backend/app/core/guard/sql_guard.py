"""SQL guardrails — read-only, single-statement, capped, schema-locked."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from app.core.config import GuardConfig, load_config

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover
    sqlglot = None  # type: ignore
    exp = None  # type: ignore


class GuardError(ValueError):
    pass


@dataclass
class GuardReport:
    sql: str
    sanitized_sql: str
    tables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    has_limit: bool = False
    estimated_complexity: str = "low"


_SELECT_HEAD = re.compile(r"^\s*select\b", re.IGNORECASE)
_BLOCKED_NEAR_FRONT = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|merge|grant|revoke|rename|lock|unlock)\b",
    re.IGNORECASE,
)


class SQLGuard:
    def __init__(self, allowed_tables: Iterable[str], cfg: GuardConfig | None = None):
        self.cfg = cfg or load_config().guard
        self.allowed_tables = {t.lower() for t in allowed_tables}

    def validate(self, sql: str) -> GuardReport:
        if not sql or not sql.strip():
            raise GuardError("空 SQL")
        cleaned = sql.strip().rstrip(";").strip()
        if not _SELECT_HEAD.match(cleaned):
            raise GuardError("仅允许 SELECT 语句")
        if ";" in cleaned:
            raise GuardError("禁止多语句")
        if _BLOCKED_NEAR_FRONT.search(cleaned):
            raise GuardError("检测到危险关键字")
        if self.cfg.block_select_star and re.search(r"select\s+\*\s+from", cleaned, re.IGNORECASE):
            raise GuardError("禁止 SELECT *，请显式列出字段")

        tables = self._extract_tables(cleaned)
        unknown = [t for t in tables if t.lower() not in self.allowed_tables]
        if unknown:
            raise GuardError(f"包含未授权表：{unknown}")

        sanitized = self._enforce_limit(cleaned)
        report = GuardReport(
            sql=cleaned,
            sanitized_sql=sanitized,
            tables=tables,
            notes=[],
            has_limit=bool(re.search(r"\blimit\s+\d+", sanitized, re.IGNORECASE)),
            estimated_complexity=self._estimate_complexity(sanitized),
        )
        return report

    def _extract_tables(self, sql: str) -> list[str]:
        if sqlglot is not None:
            try:
                tree = sqlglot.parse_one(sql, dialect="mysql")
                tables: set[str] = set()
                for table in tree.find_all(exp.Table):
                    name = (table.name or "").lower()
                    if name:
                        tables.add(name)
                if tables:
                    return sorted(tables)
            except Exception:
                pass
        # fallback regex
        out: list[str] = []
        for match in re.finditer(r"(?:from|join)\s+([`a-zA-Z0-9_\.]+)", sql, re.IGNORECASE):
            tbl = match.group(1).replace("`", "").split(".")[-1]
            if tbl:
                out.append(tbl.lower())
        return sorted(set(out))

    def _enforce_limit(self, sql: str) -> str:
        if not self.cfg.require_limit:
            return sql
        if re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
            return sql
        return sql.rstrip() + f"\nLIMIT {self.cfg.max_rows}"

    def _estimate_complexity(self, sql: str) -> str:
        joins = len(re.findall(r"\bjoin\b", sql, re.IGNORECASE))
        subq = len(re.findall(r"\(\s*select\b", sql, re.IGNORECASE))
        if joins >= 3 or subq >= 2:
            return "high"
        if joins >= 1 or subq >= 1:
            return "medium"
        return "low"
