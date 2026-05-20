"""报告提示词模板 — SQLite 存储 + 默认提示词管理。

默认模板：飞鹤上市报告标准商业分析报告提示词（产品要求）。
管理员可在 /api/admin/report-templates 增删改查；前端下载报告时选择 template_id。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.report_templates")


DEFAULT_FEIHE_IPO_PROMPT = """你是一名服务于飞鹤管理层的资深商业分析专家。请基于查询结果生成上市公司级别的经营分析报告，要求：
1. 面向管理层，语言专业、克制、结论先行。
2. 不堆砌明细，不暴露技术字段名，不展示 SQL。
3. 必须包含：核心结论、关键指标表现、区域/渠道差异、异常与风险、原因假设、管理建议、后续跟进问题。
4. 对无法计算或分母为 0 的指标要明确说明，不能臆测。
5. 结论必须来自数据，不允许编造。
6. 数值口径要保持一致，百分比保留 1 位小数，金额保留 2 位小数。
7. 风格接近正式经营分析报告，而不是聊天回答。"""


@dataclass
class ReportTemplate:
    id: str
    name: str
    prompt: str
    is_default: bool
    created_at: float
    updated_at: float
    user_id: str = ""              # "" 表示系统模板，所有用户都能看到
    owner_username: str = ""        # 仅用于 admin 视图展示


class ReportTemplateStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._ensure_default()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_schema(self) -> None:
        with self._lock, self._conn() as c:
            # 1) 先确保基础表存在（注意：旧库可能没有 user_id 列）
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS report_template (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            # 2) 检查/补 user_id 列（迁移旧库）
            cols = {r[1] for r in c.execute("PRAGMA table_info(report_template)").fetchall()}
            if "user_id" not in cols:
                c.execute("ALTER TABLE report_template ADD COLUMN user_id TEXT DEFAULT ''")
            # 3) 最后建索引（此时 user_id 列必存在）
            c.execute("CREATE INDEX IF NOT EXISTS idx_report_template_user ON report_template(user_id)")

    def _ensure_default(self) -> None:
        """确保至少一份系统级模板存在（user_id='', is_default=1）。"""
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) FROM report_template WHERE user_id='' OR user_id IS NULL"
            ).fetchone()
            if r and r[0]:
                return
        self.create(
            name="飞鹤上市报告标准商业分析报告",
            prompt=DEFAULT_FEIHE_IPO_PROMPT,
            is_default=True,
            user_id="",
        )

    # ----------------------------------------------------------- CRUD（带 owner 隔离）

    def create(self, *, name: str, prompt: str, is_default: bool = False,
               user_id: str = "") -> ReportTemplate:
        """user_id='' 创建系统模板（admin 才能）；其它值创建该用户的私有模板。"""
        if not name or not prompt:
            raise ValueError("name 和 prompt 都不能为空")
        now = time.time()
        tid = uuid.uuid4().hex
        with self._lock, self._conn() as c:
            if is_default:
                # 默认设置只影响"同一所有者范围"
                c.execute("UPDATE report_template SET is_default=0 WHERE user_id=?", (user_id,))
            c.execute(
                "INSERT INTO report_template(id,name,prompt,is_default,created_at,updated_at,user_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (tid, name, prompt, 1 if is_default else 0, now, now, user_id),
            )
        return ReportTemplate(id=tid, name=name, prompt=prompt, is_default=is_default,
                              created_at=now, updated_at=now, user_id=user_id)

    def list_for_user(self, user_id: str) -> list[ReportTemplate]:
        """返回该用户能看到的模板：系统模板 + 自己创建的私有模板。"""
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id,name,prompt,is_default,created_at,updated_at,COALESCE(user_id,'') AS user_id "
                "FROM report_template WHERE user_id='' OR user_id IS NULL OR user_id=? "
                "ORDER BY is_default DESC, user_id DESC, created_at DESC",
                (user_id,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_all(self) -> list[ReportTemplate]:
        """admin 看所有。"""
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id,name,prompt,is_default,created_at,updated_at,COALESCE(user_id,'') AS user_id "
                "FROM report_template ORDER BY is_default DESC, created_at DESC"
            ).fetchall()
        return [self._row(r) for r in rows]

    def _row(self, r: Any) -> ReportTemplate:
        return ReportTemplate(
            id=r["id"], name=r["name"], prompt=r["prompt"],
            is_default=bool(r["is_default"]),
            created_at=r["created_at"], updated_at=r["updated_at"],
            user_id=r["user_id"] if "user_id" in r.keys() else "",
        )

    def get(self, tid: str) -> Optional[ReportTemplate]:
        with self._lock, self._conn() as c:
            r = c.execute(
                "SELECT id,name,prompt,is_default,created_at,updated_at,COALESCE(user_id,'') AS user_id "
                "FROM report_template WHERE id=?", (tid,)
            ).fetchone()
        return self._row(r) if r else None

    def get_default_for_user(self, user_id: str) -> Optional[ReportTemplate]:
        """优先用户自己的默认，没有就用系统默认。"""
        items = self.list_for_user(user_id)
        # 先找用户的默认
        for it in items:
            if it.user_id == user_id and it.is_default:
                return it
        # 再找系统默认
        for it in items:
            if it.user_id == "" and it.is_default:
                return it
        return items[0] if items else None

    def update(self, tid: str, *, name: Optional[str] = None, prompt: Optional[str] = None,
               is_default: Optional[bool] = None, requester_user_id: Optional[str] = None,
               requester_is_admin: bool = False) -> None:
        now = time.time()
        with self._lock, self._conn() as c:
            existing = c.execute(
                "SELECT name,prompt,user_id FROM report_template WHERE id=?", (tid,)
            ).fetchone()
            if not existing:
                raise ValueError("模板不存在")
            owner = existing["user_id"] or ""
            # 权限：admin 全权；普通用户只能改自己的；系统模板（owner='')仅 admin 能改
            if not requester_is_admin:
                if not requester_user_id or owner != requester_user_id:
                    raise ValueError("无权修改该模板")
            new_name = name if name is not None else existing["name"]
            new_prompt = prompt if prompt is not None else existing["prompt"]
            if is_default is True:
                c.execute("UPDATE report_template SET is_default=0 WHERE user_id=?", (owner,))
            c.execute(
                "UPDATE report_template SET name=?,prompt=?,is_default=COALESCE(?,is_default),updated_at=? WHERE id=?",
                (new_name, new_prompt, (1 if is_default is True else (0 if is_default is False else None)), now, tid),
            )

    def delete(self, tid: str, *, requester_user_id: Optional[str] = None, requester_is_admin: bool = False) -> None:
        with self._lock, self._conn() as c:
            r = c.execute("SELECT is_default, user_id FROM report_template WHERE id=?", (tid,)).fetchone()
            if not r:
                return
            owner = r["user_id"] or ""
            if not requester_is_admin:
                if not requester_user_id or owner != requester_user_id:
                    raise ValueError("无权删除该模板")
            c.execute("DELETE FROM report_template WHERE id=?", (tid,))
            if r["is_default"]:
                pick = c.execute(
                    "SELECT id FROM report_template WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
                    (owner,),
                ).fetchone()
                if pick:
                    c.execute("UPDATE report_template SET is_default=1 WHERE id=?", (pick["id"],))


_singleton: Optional[ReportTemplateStore] = None
_lock = threading.RLock()


def get_report_template_store() -> ReportTemplateStore:
    global _singleton
    if _singleton is not None:
        return _singleton
    with _lock:
        if _singleton is not None:
            return _singleton
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        path = Path(os.environ.get("DATACHAT_REPORT_TEMPLATE_DB",
                                   str(backend_root / "logs" / "report_templates.db")))
        _singleton = ReportTemplateStore(path)
        return _singleton
