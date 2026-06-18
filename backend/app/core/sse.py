"""SSE 帧编码 —— 把问数流水线事件/结果/错误编码成 text/event-stream 帧。

从 orchestrator.py 拆出（零行为变化）。`to_sse_event` 对入参鸭子类型化：任何带
`.to_dict()` 的事件对象都可用，避免与 orchestrator 的 TraceEvent 形成循环导入。
"""
from __future__ import annotations

import json
from typing import Any


def to_sse_event(event: Any) -> str:
    body = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"event: stage\ndata: {body}\n\n"


def to_sse_done(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: done\ndata: {body}\n\n"


def to_sse_error(message: str) -> str:
    body = json.dumps({"error": message}, ensure_ascii=False)
    return f"event: error\ndata: {body}\n\n"
