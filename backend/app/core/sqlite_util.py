"""SQLite 连接调优 + 复用工具。

历史问题：各本地存储（会话/文件夹/few-shot/审计日志…）每次读写都
`sqlite3.connect()` 新开连接并 `PRAGMA journal_mode=WAL`。连接频繁开关有两个代价：
  1) 每次 connect + PRAGMA 的固定开销叠加到每个请求上（点哪个模块都先卡一下）；
  2) WAL 长期得不到 checkpoint 而越涨越大，读放大、越来越慢。

这里提供：
  · open_tuned(path)：建一条调好参数的连接（WAL + NORMAL + busy_timeout + 一次性截断 WAL）；
  · CachedConn：进程内复用单连接的 mixin/helper，受调用方自己的锁串行化。

注意：sqlite3 的 `with conn:` 只提交事务、**不关闭连接**，所以把 `_conn()` 改成返回
复用连接后，原有 `with self._lock, self._conn() as c:` 调用点无需改动即可生效。
"""
from __future__ import annotations

import sqlite3


def open_tuned(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA temp_store=MEMORY")
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # 回收历史遗留的大 WAL
    except Exception:
        pass
    return c
