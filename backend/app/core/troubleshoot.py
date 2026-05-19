"""排查专用日志 — 一个可直接下载发给开发的单文件日志。

目的：线上出现"问数被迫澄清/答错/报错/串表串口径"等问题时，运维只需把
``backend/logs/troubleshoot.log`` 下载发回，开发即可据此定位，无需 SSH 翻库。

写入内容：
  · 每次 /api/chat 一条结构化 JSON（问句 / QueryPlan / SQL / 行数 / 耗时 /
    是否缓存 / 是否澄清 / 错误 / 各阶段 trace），即排查问题所需的全部上下文；
  · 所有 ``datachat.*`` 模块日志（planner trace、orchestrator 告警、异常栈等）
    也一并落到同一个文件，便于交叉对照。

存储：``<backend>/logs/troubleshoot.log``（与 query_log.db 同级，服务器即
``/opt/datachatv1/logs/troubleshoot.log``）。可用环境变量覆盖：
  · DATACHAT_TROUBLESHOOT_LOG    文件路径
  · DATACHAT_TROUBLESHOOT_LEVEL  级别（默认 INFO；设 DEBUG 抓更细）
轮转：单文件 5MB × 5 份，避免撑爆磁盘。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("datachat.troubleshoot")

_HANDLER_TAG = "_datachat_troubleshoot_handler"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5
_TEXT_CAP = 6000  # 单字段最长保留字符，超出截断（SQL/plan 仍足够排查）

_lock = threading.RLock()
_configured = False


def troubleshoot_log_path() -> Path:
    env = os.environ.get("DATACHAT_TROUBLESHOOT_LOG")
    if env:
        return Path(env)
    try:
        from app.core.config import load_config

        backend_root = load_config().app.semantic_path.parent.parent
    except Exception:
        backend_root = Path(__file__).resolve().parents[2]
    return backend_root / "logs" / "troubleshoot.log"


def configure_troubleshoot_logging() -> None:
    """把 RotatingFileHandler 挂到 ``datachat`` 根 logger（幂等，可重复调用）。"""
    global _configured
    with _lock:
        if _configured:
            return
        root = logging.getLogger("datachat")
        for h in root.handlers:
            if getattr(h, _HANDLER_TAG, False):
                _configured = True
                return
        try:
            path = troubleshoot_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            level_name = (os.environ.get("DATACHAT_TROUBLESHOOT_LEVEL") or "INFO").upper()
            level = getattr(logging, level_name, logging.INFO)
            handler = RotatingFileHandler(
                str(path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
            handler.setLevel(level)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            setattr(handler, _HANDLER_TAG, True)
            root.addHandler(handler)
            if root.level == logging.NOTSET or root.level > level:
                root.setLevel(level)
            _configured = True
            logger.info("troubleshoot logging enabled -> %s (level=%s)", path, level_name)
        except Exception as exc:  # 永不因为日志配置失败而影响主流程
            logging.getLogger("datachat.troubleshoot").warning(
                "configure_troubleshoot_logging failed: %s", exc
            )


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _TEXT_CAP:
        return value[:_TEXT_CAP] + f"...<truncated {len(value) - _TEXT_CAP} chars>"
    return value


def log_event(event: str, **fields: Any) -> None:
    """写一行结构化 JSON 到排查日志。绝不抛异常。"""
    try:
        configure_troubleshoot_logging()
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        for k, v in fields.items():
            record[k] = _clip(v)
        logger.info("DIAG %s", json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        pass


def snapshot_chat(
    *,
    trace_id: str,
    user_id: str = "",
    username: str = "",
    conversation_id: str = "",
    question: str = "",
    plan: Any = None,
    sql: str = "",
    rows: int = 0,
    elapsed_ms: int = 0,
    cached: bool = False,
    needs_clarify: bool = False,
    status: str = "",
    error: str = "",
    events: Any = None,
) -> None:
    """记录一次问数的完整上下文 —— 这是发回排查的核心素材。"""
    clarify_reason = ""
    if isinstance(plan, dict):
        clarify_reason = str(plan.get("clarify_reason") or "")
    log_event(
        "chat",
        trace_id=trace_id,
        user=username or user_id,
        conversation_id=conversation_id,
        status=status,
        needs_clarify=needs_clarify,
        clarify_reason=clarify_reason,
        question=question,
        rows=rows,
        elapsed_ms=elapsed_ms,
        cached=cached,
        error=error,
        plan=plan if isinstance(plan, dict) else {},
        sql=sql,
        events=events if isinstance(events, list) else [],
    )
