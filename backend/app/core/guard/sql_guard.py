"""SQL guardrails — read-only, single-statement, capped, schema-locked.

阶段 3.1 升级：当 `DATACHAT_ALLOW_MULTI_TABLE=1` 且传入了 semantic_layer 时，
允许 SQL 包含多张表，**但每两两之间必须在 semantic.yaml 声明 join 路径**；
默认（flag 关闭）保留旧行为：多表 SQL 一律拒绝（防 LLM 瞎 JOIN）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

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
    def __init__(
        self,
        allowed_tables: Iterable[str],
        cfg: GuardConfig | None = None,
        semantic_layer: Any | None = None,
    ):
        self.cfg = cfg or load_config().guard
        self.allowed_tables = {t.lower() for t in allowed_tables}
        # 阶段 3.1：semantic_layer 用于 can_join() 校验多表 JOIN；不传则只能跑单表
        self.semantic_layer = semantic_layer

    @staticmethod
    def _multi_table_allowed() -> bool:
        return (os.environ.get("DATACHAT_ALLOW_MULTI_TABLE") or "0").strip().lower() in ("1", "true", "yes", "on")

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

        # 阶段 3.1：多表 JOIN 校验
        if len(tables) > 1:
            if not self._multi_table_allowed():
                raise GuardError(
                    f"未启用多表 JOIN（DATACHAT_ALLOW_MULTI_TABLE=0）：SQL 含 {len(tables)} 张表 {tables}"
                )
            if self.semantic_layer is None:
                raise GuardError("多表 JOIN 需配置 semantic_layer 才能校验 join 图")
            if not self.semantic_layer.can_join(tables):
                raise GuardError(
                    f"SQL 中的表对未在 semantic.yaml 声明 join 路径：{tables}"
                )

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
