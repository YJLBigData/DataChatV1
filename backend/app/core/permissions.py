"""数据权限 — 三层校验。

支持的权限维度（按强度递增）：
  · 行级（row）：维度值白名单。如 alice 只能看 region IN ('北一区')。
    实现：在 QueryPlan 编译前注入到 plan.filters。
  · 表级（table）：物理表白名单。如 alice 只能看 ads_bi_month_shop_item_dan_summary_df。
    实现：候选检索时过滤；SQL guard 二次校验。
  · 字段级（column）：每张表内的列白名单。如 alice 在 hs_sale_info_df 上禁止看 dealer_name。
    实现：SQL guard 解析 AST，发现 SELECT/WHERE 出现禁列即拒绝。

特性：
  · admin 角色绕过所有限制（业务规模 20 人，1 个 admin）
  · 空规则 = deny by default（普通用户必须显式授权）。
  · 与用户问题里的过滤求交集（用户问 region=东一区，权限只有北一区 → 空集）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.permissions")


@dataclass
class PermissionBundle:
    """单个用户的完整权限快照。"""
    user_id: str
    row_rules: dict[str, list[str]] = field(default_factory=dict)            # dimension -> allowed values
    allowed_tables: list[str] = field(default_factory=list)                   # 空 = 不限制；非空 = 白名单
    allowed_columns: dict[str, list[str]] = field(default_factory=dict)       # table -> allowed columns
    deny_by_default: bool = False                                             # 是否启用默认拒绝

    def is_unrestricted(self) -> bool:
        return not self.row_rules and not self.allowed_tables and not self.allowed_columns

    def fingerprint(self) -> str:
        """权限快照指纹 — 进入 L1/q2p 缓存 key。任何权限变更（行/表/列）都让
        该用户的问题级缓存立即失效，杜绝"权限改了，旧答案还能从缓存拿到"。"""
        import hashlib
        raw = json.dumps({
            "row_rules": {k: sorted(v) for k, v in (self.row_rules or {}).items()},
            "allowed_tables": sorted(self.allowed_tables or []),
            "allowed_columns": {k: sorted(v) for k, v in (self.allowed_columns or {}).items()},
            "deny_by_default": bool(self.deny_by_default),
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class UserScope:
    """一次问数请求的"数据域"快照 — 检索分域 / 全量表卡片 / guard 白名单 / 缓存 key 共用。

    allowed_tables 语义：
      · None      = 不分域（admin，或该用户未配置表白名单）→ 全语义层可见；
      · frozenset = 该用户可用的物理表集合（已与语义层求交）。空集合合法：
                    配置的表全部不在语义层 → 检索零召回 → 超范围拒答。
    fingerprint 覆盖完整权限快照（含行级/列级），供 L1/q2p 缓存 key 使用。
    """
    user_id: str
    is_admin: bool = False
    allowed_tables: frozenset[str] | None = None
    fingerprint: str = "all"

    @property
    def restricted(self) -> bool:
        return self.allowed_tables is not None


def get_user_scope(user_id: str, *, is_admin: bool, semantic_layer: Any | None = None) -> UserScope:
    """构建用户数据域。失败时返回不分域 scope（检索照旧全量），
    强制性的权限拦截仍由 apply_to_plan / validate_sql_columns 兜底（fail closed）。"""
    if is_admin:
        return UserScope(user_id=user_id, is_admin=True)
    try:
        bundle = get_permissions_store().get_for_user(user_id)
    except Exception as exc:
        logger.warning("get_user_scope: permissions store unavailable (%s) — fallback to unscoped", exc)
        return UserScope(user_id=user_id)
    if not bundle.allowed_tables:
        # 未配表白名单：不分域（生产环境 deny_by_default 仍会在 apply_to_plan 拦截）
        return UserScope(user_id=user_id, fingerprint=bundle.fingerprint())
    tables = set(bundle.allowed_tables)
    if semantic_layer is not None:
        known = set(getattr(semantic_layer, "tables", {}) or {})
        tables &= known
    return UserScope(
        user_id=user_id,
        allowed_tables=frozenset(tables),
        fingerprint=bundle.fingerprint(),
    )


class PermissionsStore:
    """SQLite-backed.   新版表结构（向前兼容旧 permissions 表，自动迁移）：
       user_permission_v1(user_id, payload_json) — 一行一个用户，payload 是 JSON。
    """
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_legacy()
        self._secure_db_files()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_permission_v1 (
                    user_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def _migrate_legacy(self) -> None:
        """旧表 permissions(user_id,dimension,values_json) 迁移成 row_rules。"""
        with self._lock, self._conn() as c:
            try:
                rows = c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='permissions'"
                ).fetchall()
                if not rows:
                    return
                src = c.execute("SELECT user_id, dimension, values_json FROM permissions").fetchall()
                if not src:
                    return
                grouped: dict[str, dict[str, list[str]]] = {}
                for r in src:
                    try:
                        vals = json.loads(r["values_json"]) or []
                    except Exception:
                        vals = []
                    grouped.setdefault(r["user_id"], {})[r["dimension"]] = [str(v) for v in vals]
                for uid, row_rules in grouped.items():
                    existing = c.execute(
                        "SELECT payload_json FROM user_permission_v1 WHERE user_id=?",
                        (uid,),
                    ).fetchone()
                    if existing:
                        try:
                            payload = json.loads(existing["payload_json"]) or {}
                        except Exception:
                            payload = {}
                    else:
                        payload = {}
                    payload.setdefault("row_rules", {}).update(row_rules)
                    c.execute(
                        "INSERT OR REPLACE INTO user_permission_v1(user_id, payload_json) VALUES (?,?)",
                        (uid, json.dumps(payload, ensure_ascii=False)),
                    )
                # rename old table so we don't migrate twice
                c.execute("ALTER TABLE permissions RENAME TO permissions_legacy_migrated")
                logger.info("permissions migrated %s rows from legacy table", len(src))
            except Exception as exc:
                logger.warning("permissions migration skipped: %s", exc)

    def _secure_db_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            p = Path(f"{self.path}{suffix}")
            if not p.exists():
                continue
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    # ------------------------------------------------------------- public API

    def get_for_user(self, user_id: str) -> PermissionBundle:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT payload_json FROM user_permission_v1 WHERE user_id=?", (user_id,)
            ).fetchone()
        if not r:
            return PermissionBundle(user_id=user_id)
        try:
            p = json.loads(r["payload_json"]) or {}
        except Exception:
            p = {}
        return PermissionBundle(
            user_id=user_id,
            row_rules={k: list(v) for k, v in (p.get("row_rules") or {}).items()},
            allowed_tables=list(p.get("allowed_tables") or []),
            allowed_columns={k: list(v) for k, v in (p.get("allowed_columns") or {}).items()},
            deny_by_default=bool(p.get("deny_by_default")),
        )

    def set_for_user(self, user_id: str, *, row_rules: Optional[dict[str, list[str]]] = None,
                     allowed_tables: Optional[list[str]] = None,
                     allowed_columns: Optional[dict[str, list[str]]] = None,
                     deny_by_default: Optional[bool] = None) -> None:
        existing = self.get_for_user(user_id)
        payload = {
            "row_rules":        existing.row_rules        if row_rules        is None else (row_rules or {}),
            "allowed_tables":   existing.allowed_tables   if allowed_tables   is None else (allowed_tables or []),
            "allowed_columns":  existing.allowed_columns  if allowed_columns  is None else (allowed_columns or {}),
            "deny_by_default":  existing.deny_by_default  if deny_by_default  is None else bool(deny_by_default),
        }
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO user_permission_v1(user_id, payload_json) VALUES (?,?)",
                (user_id, json.dumps(payload, ensure_ascii=False)),
            )

    def list_all(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT user_id, payload_json FROM user_permission_v1").fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            try:
                p = json.loads(r["payload_json"]) or {}
            except Exception:
                p = {}
            out[r["user_id"]] = {
                "row_rules":       p.get("row_rules") or {},
                "allowed_tables":  p.get("allowed_tables") or [],
                "allowed_columns": p.get("allowed_columns") or {},
                "deny_by_default": bool(p.get("deny_by_default")),
            }
        return out


_store_singleton: Optional[PermissionsStore] = None
_lock = threading.RLock()


def get_permissions_store() -> PermissionsStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _lock:
        if _store_singleton is not None:
            return _store_singleton
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        default_path = os.environ.get("DATACHAT_AUTH_DB") or str(backend_root / "logs" / "permissions.db")
        path = Path(os.environ.get("DATACHAT_PERMISSIONS_DB", default_path))
        _store_singleton = PermissionsStore(path)
        return _store_singleton


# ============================================================ enforcement

class PermissionDenied(Exception):
    """SQL 编译/执行期权限被拒。"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def apply_to_plan(plan: Any, *, user_id: str, is_admin: bool) -> Any:
    """把行级权限合并到 QueryPlan.filters。同时校验表权限。
    admin 跳过；不满足表权限直接抛 PermissionDenied。"""
    if is_admin:
        return plan
    bundle = get_permissions_store().get_for_user(user_id)
    if bundle.is_unrestricted():
        # 安全（P1）：生产环境对"无任何权限规则"的普通用户默认拒绝，必须显式授权；
        # 本地/开发环境保持开放以免阻断调试。也可对单用户单独开 deny_by_default。
        _env = (os.environ.get("APP_ENV") or "local").strip().lower()
        _prod = _env not in ("local", "dev", "development", "test", "testing")
        if bundle.deny_by_default or _prod:
            raise PermissionDenied("PERMISSION_DENIED:no_rules")
        return plan

    # 表级
    if bundle.allowed_tables and plan.table and plan.table not in bundle.allowed_tables:
        raise PermissionDenied(f"PERMISSION_DENIED:table={plan.table}")

    # 行级
    from app.core.nl2sql.plan import PlanFilter
    existing = {f.dimension for f in plan.filters}
    for dim, vals in (bundle.row_rules or {}).items():
        if not vals:
            continue
        if dim in existing:
            for f in plan.filters:
                if f.dimension == dim:
                    keep = [v for v in f.values if v in vals]
                    f.values = keep if keep else ["__no_permission__"]
        else:
            plan.filters.append(PlanFilter(
                dimension=dim,
                op="in" if len(vals) > 1 else "eq",
                values=list(vals),
                raw="(数据权限)",
            ))
    return plan


def validate_sql_columns(sql: str, *, user_id: str, is_admin: bool, semantic_layer: Any) -> None:
    """SQL guard 二次校验字段权限。admin 跳过。

    遍历 SELECT/WHERE/ORDER BY 中出现的列名，对照 bundle.allowed_columns。
    """
    if is_admin:
        return
    bundle = get_permissions_store().get_for_user(user_id)
    if not bundle.allowed_columns:
        return
    # 有字段级限制时必须 fail closed：依赖缺失/解析失败一律拒绝，绝不放行。
    try:
        import sqlglot
        from sqlglot import exp
    except Exception as exc:
        logger.error("field-permission check requires sqlglot but it is unavailable: %s", exc)
        raise PermissionDenied("PERMISSION_DENIED:sqlglot_unavailable")

    try:
        tree = sqlglot.parse_one(sql, dialect="mysql")
    except Exception as exc:
        logger.error("field-permission check could not parse SQL (fail closed): %s", exc)
        raise PermissionDenied("PERMISSION_DENIED:sql_parse_failed")
    if tree is None:
        raise PermissionDenied("PERMISSION_DENIED:sql_parse_empty")

    # 收集 SQL 中出现的 (table, column) 引用
    tables_in_sql: set[str] = set()
    for t in tree.find_all(exp.Table):
        tables_in_sql.add((t.name or "").lower())

    for col_node in tree.find_all(exp.Column):
        col_name = (col_node.name or "").lower()
        if not col_name or col_name == "*":
            continue
        # 找到这一列属于哪张表（粗略：取 SQL 中所有授权表的并集）
        for tbl in tables_in_sql:
            allowed = [c.lower() for c in (bundle.allowed_columns.get(tbl) or [])]
            if not allowed:
                continue
            if col_name not in allowed:
                raise PermissionDenied(f"PERMISSION_DENIED:column={tbl}.{col_name}")


def inject_row_filters_into_sql(sql: str, *, user_id: str, is_admin: bool, semantic_layer: Any) -> str:
    """审计 P0 修复 — 直接 SQL 路径下，把行级权限强注入到 SQL 的 WHERE。

    对每张被引用的物理表，从语义层映射 dimension → physical column；
    把 `WHERE physical_column IN (...)` 包到现有的 WHERE 之后。

    无 sqlglot 时降级为字符串包装：`SELECT * FROM ({sql}) inner WHERE ...`
    （这种降级保证至少不会漏权限，最坏只是 SQL 性能差一点）。

    admin 跳过；无规则跳过；找不到字段映射的规则会被忽略并记录日志（避免把全表打开）。
    """
    if is_admin:
        return sql
    bundle = get_permissions_store().get_for_user(user_id)
    if not bundle.row_rules:
        return sql

    # 安全依赖：sqlglot 缺失/解析失败一律 fail closed，绝不返回未受限 SQL。
    try:
        import sqlglot
        from sqlglot import exp
    except Exception as exc:
        logger.error("row-permission injection requires sqlglot, unavailable (fail closed): %s", exc)
        raise PermissionDenied("PERMISSION_DENIED:sqlglot_unavailable")
    try:
        tree = sqlglot.parse_one(sql, dialect="mysql")
    except Exception as exc:
        logger.error("row-permission injection could not parse SQL (fail closed): %s", exc)
        raise PermissionDenied("PERMISSION_DENIED:sql_parse_failed")
    if tree is None:
        raise PermissionDenied("PERMISSION_DENIED:sql_parse_empty")

    # 预解析每条规则的维度定义；未知维度 → fail closed（绝不放开全表）
    rules: list[tuple[Any, list[str]]] = []
    for dim_name, vals in bundle.row_rules.items():
        if not vals:
            continue
        dim_def = semantic_layer.dimension(dim_name) if semantic_layer else None
        if not dim_def:
            logger.error("row_rule references unknown dimension (fail closed): %s", dim_name)
            raise PermissionDenied(f"PERMISSION_DENIED:unknown_dim={dim_name}")
        rules.append((dim_def, [str(v) for v in vals]))
    if not rules:
        return sql

    # 关键修复：把权限条件 AND 进"能看到该物理表的那个 SELECT 的 WHERE"，
    # 而不是包一层引用不到原表字段的外层（旧实现会让查询直接报错或漏权限）。
    # 兼容 简单SELECT / WHERE / GROUP BY / ORDER BY / LIMIT / 表别名 / 子查询。
    applied = 0
    for select in list(tree.find_all(exp.Select)):
        scope_tables: dict[str, str] = {}
        for tnode in select.find_all(exp.Table):
            if tnode.find_ancestor(exp.Select) is not select:
                continue  # 属于更内层子查询，留给那一层处理
            tname = (tnode.name or "").lower()
            if tname:
                scope_tables[tname] = tnode.alias_or_name
        if not scope_tables:
            continue
        for dim_def, vals in rules:
            for tname, talias in scope_tables.items():
                col = (getattr(dim_def, "table_columns", {}) or {}).get(tname)
                if not col:
                    continue
                # 用 AST 构造 `talias`.`col` IN ('北一区','南一区')，由 sqlglot 负责安全转义；
                # copy=False 原地改写（where() 默认返回副本，必须就地合并）。
                cond = exp.In(
                    this=exp.column(col, table=talias),
                    expressions=[exp.Literal.string(str(v)) for v in vals],
                )
                select.where(cond, append=True, copy=False, dialect="mysql")
                applied += 1

    if applied == 0:
        # 被引用的表均不含任何受限维度的列 → 该查询本就不触及受限数据，放行。
        logger.info("row-permission: no restricted dimension column present in queried tables")
        return sql
    return tree.sql(dialect="mysql")
