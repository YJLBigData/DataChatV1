"""Semantic layer loader.

Loads `backend/config/semantic.yaml` and exposes typed accessors.
Designed to be small, deterministic, and reload-safe.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("datachat.semantic")


@dataclass
class TableDef:
    name: str
    label: str
    schema: str
    grain: str
    description: str
    time_field: str | None
    time_field_year: str | None
    time_field_month: str | None
    time_format: str
    primary_dimensions: list[str]
    measures: list[str]
    notes: list[str]

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def is_year_month_split(self) -> bool:
        return bool(self.time_field_year and self.time_field_month)


@dataclass
class DimensionDef:
    name: str
    label: str
    aliases: list[str]
    table_columns: dict[str, str]
    sample_values: list[str] = field(default_factory=list)
    value_dict: dict[str, str] = field(default_factory=dict)
    description: str = ""
    code_columns: dict[str, str] = field(default_factory=dict)

    def column_in(self, table: str) -> str | None:
        return self.table_columns.get(table)

    def all_aliases(self) -> list[str]:
        out = {self.label, self.name, *self.aliases}
        return [a for a in out if a]


@dataclass
class MetricDef:
    name: str
    label: str
    aliases: list[str]
    table: str
    expression: str
    unit: str
    display_format: str
    decimals: int = 2
    higher_is_better: bool = True
    description: str = ""
    domain: str = "general"
    typical_dimensions: list[str] = field(default_factory=list)
    typical_questions: list[str] = field(default_factory=list)

    def all_aliases(self) -> list[str]:
        return [a for a in {self.label, self.name, *self.aliases} if a]


@dataclass
class JoinDef:
    name: str
    left: str
    right: str
    on: list[tuple[str, str]]
    notes: str
    safe: bool


@dataclass
class CalculationDef:
    name: str
    label: str
    aliases: list[str]
    formula: str = ""


@dataclass
class FewShotDef:
    question: str
    intent: dict[str, Any]
    sql_hint: str = ""


class SemanticLayer:
    """In-memory semantic layer (single-tenant Feihe)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._raw: dict[str, Any] = {}
        self.version = 0
        self.profile = ""
        self.default_time_grain = "month"
        self.data_range_earliest = ""
        self.data_range_latest = ""
        self.tables: dict[str, TableDef] = {}
        self.dimensions: dict[str, DimensionDef] = {}
        self.metrics: dict[str, MetricDef] = {}
        self.joins: dict[str, JoinDef] = {}
        self.calculations: dict[str, CalculationDef] = {}
        self.few_shots: list[FewShotDef] = []
        self.synonyms: dict[str, str] = {}
        self.time_dimensions: dict[str, dict[str, Any]] = {}
        self.reload()

    # -- public API -----------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            self._raw = data
            self.version = int(data.get("version") or 1)
            self.profile = str(data.get("profile") or "default")
            self.default_time_grain = str(data.get("default_time_grain") or "month")
            dr = data.get("data_range") or {}
            self.data_range_earliest = str(dr.get("earliest") or "")
            self.data_range_latest = str(dr.get("latest") or "")
            self._load_tables(data.get("tables") or {})
            self._load_dimensions(data.get("dimensions") or {})
            self._load_metrics(data.get("metrics") or {})
            self._load_joins(data.get("joins") or {})
            self._load_calculations(data.get("calculations") or {})
            self._load_few_shots(data.get("few_shots") or [])
            self.time_dimensions = data.get("time_dimensions") or {}
            self.synonyms = {str(k): str(v) for k, v in (data.get("synonyms") or {}).items()}

    def metric(self, name: str) -> MetricDef | None:
        return self.metrics.get(name)

    def dimension(self, name: str) -> DimensionDef | None:
        return self.dimensions.get(name)

    def table(self, name: str) -> TableDef | None:
        return self.tables.get(name)

    def list_tables(self) -> list[TableDef]:
        return list(self.tables.values())

    def list_metrics(self) -> list[MetricDef]:
        return list(self.metrics.values())

    def list_dimensions(self) -> list[DimensionDef]:
        return list(self.dimensions.values())

    def find_metric_by_alias(self, alias: str) -> MetricDef | None:
        token = (alias or "").strip().lower()
        if not token:
            return None
        for m in self.metrics.values():
            for a in m.all_aliases():
                if a.lower() == token:
                    return m
        return None

    def find_dimension_by_alias(self, alias: str) -> DimensionDef | None:
        token = (alias or "").strip().lower()
        if not token:
            return None
        for d in self.dimensions.values():
            for a in d.all_aliases():
                if a.lower() == token:
                    return d
        return None

    def calculation_by_alias(self, alias: str) -> CalculationDef | None:
        token = (alias or "").strip().lower()
        if not token:
            return None
        for c in self.calculations.values():
            for a in [c.label, c.name, *c.aliases]:
                if a and a.lower() == token:
                    return c
        return None

    # -- internal -------------------------------------------------------------

    def _load_tables(self, raw: dict[str, Any]) -> None:
        self.tables.clear()
        # 业务库名以环境变量为准（线上 .env: MYSQL_DATABASE=hs_poc；本地 dev: chatbi）。
        # semantic.yaml 里写死 schema 不可避免会和服务器实际 DB 名不一致——必须 env 优先，
        # 否则 compiler 会输出 `FROM chatbi.xxx` 跑到错误的库里去。
        db_override = (
            os.environ.get("DATACHAT_BUSINESS_DB")
            or os.environ.get("MYSQL_DATABASE")
            or os.environ.get("DB_NAME")
            or ""
        ).strip()
        if db_override:
            logger.info("semantic.schema_override -> %s (from env)", db_override)
        for name, body in raw.items():
            yaml_schema = str(body.get("schema") or "")
            schema = db_override or yaml_schema
            self.tables[name] = TableDef(
                name=name,
                label=str(body.get("label") or name),
                schema=schema,
                grain=str(body.get("grain") or ""),
                description=str(body.get("description") or "").strip(),
                time_field=body.get("time_field"),
                time_field_year=body.get("time_field_year"),
                time_field_month=body.get("time_field_month"),
                time_format=str(body.get("time_format") or ""),
                primary_dimensions=list(body.get("primary_dimensions") or []),
                measures=list(body.get("measures") or []),
                notes=list(body.get("notes") or []),
            )

    def _load_dimensions(self, raw: dict[str, Any]) -> None:
        self.dimensions.clear()
        for name, body in raw.items():
            self.dimensions[name] = DimensionDef(
                name=name,
                label=str(body.get("label") or name),
                aliases=list(body.get("aliases") or []),
                table_columns={str(k): str(v) for k, v in (body.get("table_columns") or {}).items()},
                sample_values=[str(s) for s in (body.get("sample_values") or [])],
                value_dict={str(k): str(v) for k, v in (body.get("value_dict") or {}).items()},
                description=str(body.get("description") or "").strip(),
                code_columns={str(k): str(v) for k, v in (body.get("code_column") or {}).items()},
            )

    def _load_metrics(self, raw: dict[str, Any]) -> None:
        self.metrics.clear()
        for name, body in raw.items():
            self.metrics[name] = MetricDef(
                name=name,
                label=str(body.get("label") or name),
                aliases=list(body.get("aliases") or []),
                table=str(body.get("table") or ""),
                expression=str(body.get("expression") or ""),
                unit=str(body.get("unit") or ""),
                display_format=str(body.get("display_format") or "number"),
                decimals=int(body.get("decimals") or 2),
                higher_is_better=bool(body.get("higher_is_better", True)),
                description=str(body.get("description") or "").strip(),
                domain=str(body.get("domain") or "general"),
                typical_dimensions=list(body.get("typical_dimensions") or []),
                typical_questions=list(body.get("typical_questions") or []),
            )

    def _load_joins(self, raw: dict[str, Any]) -> None:
        self.joins.clear()
        for name, body in raw.items():
            on_pairs = []
            for pair in body.get("on") or []:
                if isinstance(pair, list) and len(pair) == 2:
                    on_pairs.append((str(pair[0]), str(pair[1])))
            self.joins[name] = JoinDef(
                name=name,
                left=str(body.get("left") or ""),
                right=str(body.get("right") or ""),
                on=on_pairs,
                notes=str(body.get("notes") or ""),
                safe=bool(body.get("safe", True)),
            )

    def _load_calculations(self, raw: dict[str, Any]) -> None:
        self.calculations.clear()
        for name, body in raw.items():
            self.calculations[name] = CalculationDef(
                name=name,
                label=str(body.get("label") or name),
                aliases=list(body.get("aliases") or []),
                formula=str(body.get("formula") or ""),
            )

    def _load_few_shots(self, raw: list[Any]) -> None:
        self.few_shots = [
            FewShotDef(
                question=str(item.get("question") or ""),
                intent=dict(item.get("intent") or {}),
                sql_hint=str(item.get("sql_hint") or ""),
            )
            for item in raw
            if item and item.get("question")
        ]
