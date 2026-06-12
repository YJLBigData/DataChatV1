"""语义层 CRUD 编辑器 —— 把 semantic.yaml 当成主事实源，做安全 mutate 后写盘。

支持：
  · 表 tables.<name>:  CRUD
  · 维度 dimensions.<name>: CRUD
  · 指标 metrics.<name>:  CRUD
  · LLM 自动分析: 给定 schema 名 + 物理表，连接 MySQL 拉 INFORMATION_SCHEMA + 样本，
    交给 qwen3.6-max-preview 生成候选 dimensions/metrics/table description，
    用户在前端审阅后保存。

所有 mutation 都在文件锁内执行，写完后调用 SemanticLayer.reload() 热生效。
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("datachat.semantic_editor")

_FILE_LOCK = threading.RLock()


def _load_yaml(path: Path) -> dict[str, Any]:
    with _FILE_LOCK:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("semantic.yaml 根节点必须是 mapping")
        for k in ("tables", "dimensions", "metrics"):
            data.setdefault(k, {})
        return data


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    with _FILE_LOCK:
        backup = path.with_suffix(path.suffix + ".bak")
        if path.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        new_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2, width=120)
        path.write_text(new_text, encoding="utf-8")


# ============================================================ CRUD

def list_entities(path: Path, kind: str) -> dict[str, dict[str, Any]]:
    data = _load_yaml(path)
    return dict(data.get(kind) or {})


def _validate_entity(kind: str, name: str, body: dict[str, Any], data: dict[str, Any]) -> None:
    """保存前的深度 schema 校验（P2）：拦住缺必填/类型错的实体，
    避免坏配置进 semantic.yaml 后 reload/召回/SQL 生成集体崩。"""
    def _s(v: Any) -> str:
        return v if isinstance(v, str) else ""

    if kind == "tables":
        if not _s(body.get("schema")).strip():
            raise ValueError(f"表 {name} 缺少必填字段 schema（物理库名）")
        has_single = bool(_s(body.get("time_field")).strip())
        has_split = bool(_s(body.get("time_field_year")).strip() and _s(body.get("time_field_month")).strip())
        if not (has_single or has_split):
            raise ValueError(f"表 {name} 必须配置 time_field，或同时配置 time_field_year + time_field_month")
    elif kind == "dimensions":
        tc = body.get("table_columns")
        if not isinstance(tc, dict) or not tc:
            raise ValueError(f"维度 {name} 必须配置非空 table_columns（物理表→列名 映射）")
        for t, col in tc.items():
            if not isinstance(t, str) or not isinstance(col, str) or not col.strip():
                raise ValueError(f"维度 {name} 的 table_columns 项非法：{t!r}->{col!r}")
    elif kind == "metrics":
        tbl = _s(body.get("table")).strip()
        expr = _s(body.get("expression")).strip()
        if not tbl:
            raise ValueError(f"指标 {name} 缺少必填字段 table（所属物理表）")
        if not expr:
            raise ValueError(f"指标 {name} 缺少必填字段 expression（聚合表达式）")
        known_tables = set((data.get("tables") or {}).keys())
        # 允许指向"本次同批新增"的表；否则必须是已存在表
        if known_tables and tbl not in known_tables:
            raise ValueError(f"指标 {name} 的 table='{tbl}' 不在 tables 中，请先创建该表")
    # 认证状态：只接受 draft / verified（缺省按 draft 处理，不在这里强写）
    if "status" in body:
        st = str(body.get("status") or "").strip().lower()
        if st not in ("draft", "verified"):
            raise ValueError(f"{kind}.{name} 的 status 只能是 draft 或 verified，收到 {body.get('status')!r}")
        body["status"] = st


def upsert_entity(path: Path, kind: str, name: str, body: dict[str, Any]) -> dict[str, Any]:
    if kind not in ("tables", "dimensions", "metrics"):
        raise ValueError(f"unknown kind: {kind}")
    if not name or not isinstance(name, str):
        raise ValueError("name 不能为空")
    if not isinstance(body, dict):
        raise ValueError("body 必须是对象")
    data = _load_yaml(path)
    _validate_entity(kind, name, body, data)
    # 认证状态保持策略：body 未显式给 status 时，沿用已有条目的状态；
    # 全新条目默认 draft（机器起草未经认证）。显式给 status 以显式值为准。
    if "status" not in body:
        existing = (data.get(kind) or {}).get(name) or {}
        body["status"] = str(existing.get("status") or "draft").strip().lower() or "draft"
    data.setdefault(kind, {})[name] = body
    _save_yaml(path, data)
    return body


def set_status(path: Path, kind: str, name: str, status: str) -> dict[str, Any]:
    """认证工作流：单独切换某实体的 draft/verified 状态（不动其它字段）。"""
    if kind not in ("tables", "dimensions", "metrics"):
        raise ValueError(f"unknown kind: {kind}")
    st = str(status or "").strip().lower()
    if st not in ("draft", "verified"):
        raise ValueError(f"status 只能是 draft 或 verified，收到 {status!r}")
    data = _load_yaml(path)
    section = data.get(kind) or {}
    if name not in section:
        raise ValueError(f"{kind} 中不存在 {name}")
    section[name]["status"] = st
    _save_yaml(path, data)
    return {"kind": kind, "name": name, "status": st}


def certification_overview(path: Path) -> dict[str, Any]:
    """认证清单：每类实体的 (name, label, status)，给管理端"集中认证"用。"""
    data = _load_yaml(path)
    out: dict[str, Any] = {"kinds": {}, "stats": {"draft": 0, "verified": 0}}
    for kind in ("tables", "dimensions", "metrics"):
        items = []
        for name, body in (data.get(kind) or {}).items():
            st = str((body or {}).get("status") or "draft").strip().lower()
            st = st if st == "verified" else "draft"
            items.append({"name": name, "label": str((body or {}).get("label") or name), "status": st})
            out["stats"][st] += 1
        items.sort(key=lambda x: (x["status"] != "draft", x["name"]))  # 草稿排前面，方便走查
        out["kinds"][kind] = items
    return out


def delete_entity(path: Path, kind: str, name: str) -> bool:
    data = _load_yaml(path)
    section = data.get(kind) or {}
    if name not in section:
        return False
    section.pop(name)
    _save_yaml(path, data)
    return True


# ============================================================ AUTO-ANALYZE

def analyze_table(
    physical_table: str,
    *,
    schema: str,
    executor,             # app.core.exec.MySQLExecutor
    llm,                  # app.core.llm.LLMRouter
    sample_rows: int = 5,
) -> dict[str, Any]:
    """从 MySQL 拉字段元数据 + 样本行，让 LLM 推荐 dimensions/metrics/table_desc。

    返回结构：{
      "table_proposal": {label, description, grain, time_field, primary_dimensions, measures},
      "dimensions": {dim_key: {label, table_columns: {table: column}, sample_values, description}},
      "metrics":    {metric_key: {label, table, expression, unit, display_format, description}}
    }

    所有产物都需要用户审阅后保存（前端确认）— 这里只返回建议。
    """
    cols = _fetch_columns(executor, schema, physical_table)
    if not cols:
        raise ValueError(f"表 {schema}.{physical_table} 没有字段或无访问权限")
    samples = _fetch_samples(executor, schema, physical_table, sample_rows)

    cols_brief = "\n".join(
        f"  - {c['name']} : {c['type']}{' (NULL)' if c['nullable'] else ''}  {c['comment']}"
        for c in cols
    )
    sample_brief = "\n".join(
        "  | ".join(f"{k}={v}" for k, v in row.items()) for row in samples[:sample_rows]
    )

    system = (
        "你是飞鹤数仓语义建模专家。根据物理表的字段定义与样本数据，"
        "把字段分类为：维度 dimension / 指标 metric / 时间字段。"
        "硬要求："
        "1) 只输出 JSON；"
        "2) 维度 key 用蛇形小写英文；"
        "3) 指标 key 以 _total / _amount / _rate / _num 等业务后缀结尾；"
        "4) 指标 expression 必须能直接放进 SELECT，例如 SUM(col_name)、SUM(a)/NULLIF(SUM(b),0)；"
        "5) 金额单位写'元'，人数写'人'，比率 display_format='percent'；"
        "6) 优先识别 region/sub_region/city/channel/item 等业务命名维度。"
    )
    user = (
        f"物理表：{schema}.{physical_table}\n字段：\n{cols_brief}\n"
        f"样本（前 {sample_rows} 行）：\n{sample_brief}\n\n"
        f"请输出 JSON 严格符合："
    )
    schema_hint = """{
      "table_proposal": {
        "label": "中文表名",
        "description": "<这张表是干嘛的>",
        "grain": "<粒度，如 月-省区-渠道>",
        "time_field": "<时间字段名>",
        "time_field_year": "<可选年字段>",
        "time_field_month": "<可选月字段>",
        "primary_dimensions": ["dim_key_a","dim_key_b"],
        "measures": ["metric_key_a"]
      },
      "dimensions": {
        "<dim_key>": {
          "label": "中文",
          "aliases": ["别名"],
          "table_columns": {"<physical_table>": "<physical_column>"},
          "sample_values": ["示例值1","示例值2"],
          "description": "<这个维度是什么>"
        }
      },
      "metrics": {
        "<metric_key>": {
          "label": "中文",
          "aliases": ["别名"],
          "table": "<physical_table>",
          "expression": "SUM(col)",
          "unit": "元|人|...",
          "display_format": "currency_cn|integer_cn|percent|number",
          "decimals": 2,
          "description": "<这个指标含义>"
        }
      }
    }"""
    parsed, _ = llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        schema_hint=schema_hint,
        temperature=0.0,
    )
    if not isinstance(parsed, dict):
        raise ValueError("LLM 返回的不是有效 JSON 对象")
    parsed.setdefault("table_proposal", {})
    parsed.setdefault("dimensions", {})
    parsed.setdefault("metrics", {})
    return parsed


_SQL_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _fetch_columns(executor, schema: str, table: str) -> list[dict[str, Any]]:
    # 安全：schema/table 来自管理员输入，强校验为合法标识符，杜绝 SQL 注入。
    if not _SQL_IDENT_RE.match(schema or "") or not _SQL_IDENT_RE.match(table or ""):
        raise ValueError(f"非法库名/表名（仅允许字母数字下划线）：{schema!r}.{table!r}")
    sql = (
        f"SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type, IS_NULLABLE AS nullable, "
        f"COLUMN_COMMENT AS comment "
        f"FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}' "
        f"ORDER BY ORDINAL_POSITION"
    )
    try:
        result = executor.run_select(sql, max_rows=500, timeout_ms=10000)
    except Exception as exc:
        logger.warning("fetch_columns failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in result.rows:
        out.append({
            "name": row[0], "type": row[1],
            "nullable": str(row[2]).upper() == "YES",
            "comment": row[3] or "",
        })
    return out


def _fetch_samples(executor, schema: str, table: str, limit: int) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM `{schema}`.`{table}` LIMIT {int(limit)}"
    try:
        result = executor.run_select(sql, max_rows=limit, timeout_ms=10000)
    except Exception as exc:
        logger.warning("fetch_samples failed: %s", exc)
        return []
    return [dict(zip(result.columns, row)) for row in result.rows]
