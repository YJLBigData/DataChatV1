"""问数全局在途并发闸 + 自定义 Prometheus 指标（容量方案 P0-3 / 监控）。

设计要点：
- **单进程**内用 ``threading.BoundedSemaphore`` 控制"同时在途的问数请求数"，
  同步端点 ``/api/chat`` 与异步 SSE 端点 ``/api/chat/stream`` 共用同一个闸（信号量
  跨线程/跨协程可见），因此 inflight 是真实的全局计数。
- 超过上限后**立即（或极短等待后）返回 429**，主动泄洪：避免高峰所有请求一起堆到
  LLM 读超时(90s)才失败 —— 体验从"全员转圈"变成"少量请求提示繁忙稍后重试"。
- Prometheus 指标在缺少 ``prometheus_client`` 或重复注册时**静默降级为 no-op**，
  绝不影响主流程（与项目其它可选依赖一致的防御式写法）。

环境变量：
- ``CHAT_MAX_INFLIGHT``（默认 30）：全局在途上限。
- ``CHAT_SEMAPHORE_ACQUIRE_TIMEOUT``（默认 0.2 秒）：拿不到名额时的最长等待，超时即 429。
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("datachat.concurrency")


# --------------------------------------------------------------------------- 指标
class _NoopMetric:
    """prometheus_client 不可用 / 重复注册时的占位，所有方法都是空操作。"""

    def inc(self, *a, **k) -> None:  # noqa: D401
        pass

    def dec(self, *a, **k) -> None:
        pass

    def set(self, *a, **k) -> None:
        pass

    def labels(self, *a, **k):
        return self


def _make_metric(factory, name: str, doc: str):
    """创建一个指标；缺库或重复注册（如测试里多次 import）时回退 no-op，互不影响。"""
    try:
        return factory(name, doc)
    except Exception:  # noqa: BLE001  - 缺 prometheus_client 或 Duplicated timeseries
        return _NoopMetric()


try:  # 仅为拿到 Gauge/Counter 工厂；失败则全部走 no-op
    from prometheus_client import Counter, Gauge

    CHAT_INFLIGHT = _make_metric(Gauge, "chat_inflight", "当前在途问数请求数")
    CHAT_REJECTED = _make_metric(Counter, "chat_rejected_total", "被全局并发闸拒绝(429)的问数请求数")
    LLM_TIMEOUT = _make_metric(Counter, "llm_timeout_total", "LLM 网关调用超时次数")
    DB_POOL_TIMEOUT = _make_metric(Counter, "db_pool_timeout_total", "数据库连接池获取超时次数")
except Exception:  # noqa: BLE001
    CHAT_INFLIGHT = _NoopMetric()
    CHAT_REJECTED = _NoopMetric()
    LLM_TIMEOUT = _NoopMetric()
    DB_POOL_TIMEOUT = _NoopMetric()


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------- 并发闸
class ChatCapacityGuard:
    """全局在途问数并发闸。``try_acquire`` 成功后**务必**配对 ``release``（用 try/finally）。"""

    def __init__(self, max_inflight: int, acquire_timeout: float):
        self.max_inflight = max(1, int(max_inflight))
        self.acquire_timeout = max(0.0, float(acquire_timeout))
        self._sem = threading.BoundedSemaphore(self.max_inflight)

    def try_acquire(self, timeout: float | None = None) -> bool:
        """抢一个在途名额。

        - ``timeout=None``：用默认 ``acquire_timeout``（可短暂阻塞当前线程，仅同步端点用）。
        - ``timeout=0``：非阻塞（异步 SSE 端点用，绝不阻塞事件循环）。
        返回 True=拿到名额（已 inflight+1），False=已满（已计入 rejected，调用方应回 429）。
        """
        wait = self.acquire_timeout if timeout is None else max(0.0, float(timeout))
        if wait > 0:
            ok = self._sem.acquire(blocking=True, timeout=wait)
        else:
            ok = self._sem.acquire(blocking=False)
        if ok:
            CHAT_INFLIGHT.inc()
        else:
            CHAT_REJECTED.inc()
        return bool(ok)

    def release(self) -> None:
        try:
            self._sem.release()
        except ValueError:
            # 释放次数超过获取次数（理论不应发生）；不再 dec 计数，保持 gauge 不为负。
            return
        CHAT_INFLIGHT.dec()


_GUARD: ChatCapacityGuard | None = None
_GUARD_LOCK = threading.Lock()


def get_chat_guard() -> ChatCapacityGuard:
    """进程内单例。首次调用时按环境变量构建。"""
    global _GUARD
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:
                _GUARD = ChatCapacityGuard(
                    max_inflight=_env_int("CHAT_MAX_INFLIGHT", 30),
                    acquire_timeout=_env_float("CHAT_SEMAPHORE_ACQUIRE_TIMEOUT", 0.2),
                )
                logger.info(
                    "chat capacity guard ready (max_inflight=%s, acquire_timeout=%ss)",
                    _GUARD.max_inflight, _GUARD.acquire_timeout,
                )
    return _GUARD


def note_llm_timeout() -> None:
    """LLM 网关调用超时时调用（router / feihe_gateway）。"""
    LLM_TIMEOUT.inc()


def note_db_pool_timeout() -> None:
    """DB 连接池获取超时时调用（mysql_exec）。"""
    DB_POOL_TIMEOUT.inc()
