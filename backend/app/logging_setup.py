"""统一日志初始化（阶段 2.4）。

· 默认 JSON 行式输出（每行一个 JSON 对象），便于 Loki/ELK/grep+jq 处理；
· 通过 env `DATACHAT_LOG_FORMAT=plain` 可回退到人类可读格式（本地调试用）；
· 注入 trace_id（contextvar）和 pid，方便多 worker 串日志；
· 自动幂等：可重复调用，不会重复添加 handler。
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
import time
from contextvars import ContextVar

# 请求级 trace_id，业务侧 set_trace_id(...) 后整个调用栈日志都会带上
_trace_id_var: ContextVar[str] = ContextVar("datachat_trace_id", default="")


def set_trace_id(tid: str) -> None:
    _trace_id_var.set((tid or "")[:32])


def get_trace_id() -> str:
    return _trace_id_var.get()


class _JsonFormatter(logging.Formatter):
    """轻量 JSON 行 formatter（不引入外部依赖，CentOS7 离线源也能跑）。

    输出字段：ts(ISO8601), level, logger, msg, pid, trace_id?, exc?
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
        ts = f"{ts}.{int(record.msecs):03d}"
        payload: dict[str, object] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": record.process,
        }
        tid = _trace_id_var.get()
        if tid:
            payload["trace_id"] = tid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = False


def configure_logging(force: bool = False) -> None:
    """每个 uvicorn worker 启动时调用一次。重复调用是安全的（除非 force=True）。"""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    fmt = (os.environ.get("DATACHAT_LOG_FORMAT") or "json").strip().lower()
    level = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()

    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "plain":
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [pid=%(process)d] %(message)s"
        ))
    else:
        handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    # 清掉 uvicorn / FastAPI 默认装的 handler 以免双写
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # 让 uvicorn 自身的 logger 也走 root，避免 access 日志格式不一致
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    _CONFIGURED = True
