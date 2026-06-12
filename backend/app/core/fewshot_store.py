"""采纳沉淀 few-shot 库 — Vanna 路线的"准确率飞轮"。

用户对某次回答点"采纳"后，把 (问题, QueryPlan) 沉淀为可检索的验证样例；
planner 在召回阶段按"同数据域"把它们合并进候选 prompt（few_shots 区）。
验证过的问答对是长期准确率的复利来源（Vanna / Uber QueryGPT golden mappings 同思路）。

安全/口径要点：
  · 沉淀前剥离行级权限注入的 filters（raw == "(数据权限)"）——权限过滤不是
    问题口径的一部分，也绝不能把 A 用户的行级范围泄露给同表的其他用户；
  · 只沉淀结构化 plan（metric 非空、未澄清、未拒答）；direct_sql 模式不沉淀；
  · 检索按表分域：用户只能召回自己表范围内的沉淀样例；
  · 点"踩"也入库（vote=down，不参与召回）——这是评测集挖 bad case 的金矿。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("datachat.fewshot")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


def _tokens(text: str) -> set[str]:
    """轻量分词：ASCII 词 + 中文双字 n-gram。独立实现，避免依赖检索模块。"""
    text = (text or "").lower().strip()
    out: set[str] = set()
    chars = _TOKEN_RE.findall(text)
    cjk_run: list[str] = []
    for tok in chars:
        if len(tok) == 1 and "一" <= tok <= "鿿":
            cjk_run.append(tok)
        else:
            out.add(tok)
            cjk_run = []
            continue
        if len(cjk_run) >= 2:
            out.add("".join(cjk_run[-2:]))
    return out


def _clean_intent(plan: dict[str, Any]) -> dict[str, Any]:
    """把 plan 收敛成可复用的 intent：剥权限注入 filters、剥 LLM 抖动字段。"""
    filters: list[dict[str, Any]] = []
    for f in plan.get("filters") or []:
        if not isinstance(f, dict):
            continue
        if str(f.get("raw") or "") == "(数据权限)":
            continue
        filters.append({
            "dimension": str(f.get("dimension") or ""),
            "op": str(f.get("op") or "eq"),
            "values": [str(v) for v in (f.get("values") or [])],
        })
    return {
        "metric": str(plan.get("metric") or ""),
        "extra_metrics": [str(m) for m in (plan.get("extra_metrics") or [])],
        "table": str(plan.get("table") or ""),
        "group_by": [str(g) for g in (plan.get("group_by") or [])],
        "filters": filters,
        "time_range": plan.get("time_range") or {},
        "calculation": str(plan.get("calculation") or ""),
        "order_by": plan.get("order_by") or [],
        "limit": int(plan.get("limit") or 0),
    }


class FewShotStore:
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
                CREATE TABLE IF NOT EXISTS fewshot_v1 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    question_norm TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    vote TEXT NOT NULL DEFAULT 'up',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fewshot_table ON fewshot_v1(table_name, vote);
                """
            )

    # ------------------------------------------------------------- write

    def add_adopted(self, user_id: str, question: str, plan: dict[str, Any]) -> bool:
        """采纳沉淀。不合格（无指标/澄清/拒答/直接SQL）返回 False。
        同一问题 + 同一表重复采纳 → 覆盖更新（最新的 plan 胜出）。"""
        question = (question or "").strip()
        if not question or not isinstance(plan, dict):
            return False
        if plan.get("needs_clarify") or plan.get("out_of_scope"):
            return False
        intent = _clean_intent(plan)
        if not intent["metric"] or not intent["table"]:
            return False
        qnorm = " ".join(question.lower().split())
        row_id = hashlib.sha1(f"{qnorm}|{intent['table']}".encode("utf-8")).hexdigest()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO fewshot_v1"
                "(id, user_id, question, question_norm, intent_json, table_name, metric, vote, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (row_id, user_id, question, qnorm,
                 json.dumps(intent, ensure_ascii=False), intent["table"], intent["metric"],
                 "up", time.time()),
            )
        logger.info("fewshot.adopted user=%s table=%s metric=%s q=%r",
                    user_id, intent["table"], intent["metric"], question[:60])
        return True

    def record_downvote(self, user_id: str, question: str, plan: dict[str, Any]) -> None:
        """点踩入库（vote=down，不参与召回）：评测集挖 bad case 的直接素材。"""
        question = (question or "").strip()
        if not question:
            return
        intent = _clean_intent(plan if isinstance(plan, dict) else {})
        qnorm = " ".join(question.lower().split())
        row_id = hashlib.sha1(f"down|{qnorm}|{intent['table']}|{time.time()}".encode("utf-8")).hexdigest()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO fewshot_v1"
                "(id, user_id, question, question_norm, intent_json, table_name, metric, vote, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (row_id, user_id, question, qnorm,
                 json.dumps(intent, ensure_ascii=False), intent["table"], intent["metric"],
                 "down", time.time()),
            )

    # ------------------------------------------------------------- read

    def search(
        self,
        question: str,
        *,
        allowed_tables: "set[str] | frozenset[str] | None" = None,
        limit: int = 3,
        min_overlap: float = 0.15,
    ) -> list[dict[str, Any]]:
        """同域相似样例检索：token Jaccard 排序，表范围过滤。"""
        q_tokens = _tokens(question)
        if not q_tokens:
            return []
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT question, intent_json, table_name FROM fewshot_v1"
                " WHERE vote='up' ORDER BY created_at DESC LIMIT 2000"
            ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in rows:
            if allowed_tables is not None and r["table_name"] not in allowed_tables:
                continue
            t = _tokens(r["question"])
            if not t:
                continue
            inter = len(q_tokens & t)
            union = len(q_tokens | t)
            score = inter / union if union else 0.0
            if score < min_overlap:
                continue
            try:
                intent = json.loads(r["intent_json"]) or {}
            except Exception:
                continue
            scored.append((score, {"question": r["question"], "intent": intent, "score": round(score, 3)}))
        scored.sort(key=lambda x: -x[0])
        return [item for _, item in scored[: max(0, int(limit))]]

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            up = c.execute("SELECT COUNT(*) AS n FROM fewshot_v1 WHERE vote='up'").fetchone()["n"]
            down = c.execute("SELECT COUNT(*) AS n FROM fewshot_v1 WHERE vote='down'").fetchone()["n"]
            by_table = {
                r["table_name"]: r["n"]
                for r in c.execute(
                    "SELECT table_name, COUNT(*) AS n FROM fewshot_v1 WHERE vote='up' GROUP BY table_name"
                ).fetchall()
            }
        return {"adopted": up, "downvoted": down, "by_table": by_table}


_store_singleton: Optional[FewShotStore] = None
_lock = threading.RLock()


def get_fewshot_store() -> FewShotStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _lock:
        if _store_singleton is not None:
            return _store_singleton
        from app.core.config import load_config
        cfg = load_config()
        backend_root = cfg.app.semantic_path.parent.parent
        default_path = str(backend_root / "logs" / "fewshots.db")
        path = Path(os.environ.get("DATACHAT_FEWSHOT_DB", default_path))
        _store_singleton = FewShotStore(path)
        return _store_singleton
