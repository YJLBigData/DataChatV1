"""数据导出服务 —— 后台线程池把可信结果写成 XLSX（流式、有行上限、有背压）。

数据来源：服务端可信结果 (conversation_id, trace_id)。**绝不**接受前端任意 SQL。
  · 优先用存档 SQL 经 MySQL **流式**重跑（服务端游标，常量内存，受 EXPORT_MAX_ROWS 保护）；
  · 本地/无库或重跑失败 → 退回存档的结果表（display_rows）。
写出用 openpyxl write_only（常量内存、可写百万行）。Excel 物理上限 1,048,576 行，
预留表头，业务上限默认 1,040,000 行；超出按规则截断并在文件尾标注。

并发与背压：
  · 物理文件名按 **job id** 命名（exp_xxx.xlsx），杜绝"同一秒两个 job 同名互相覆盖"；
  · 每用户在途上限 EXPORT_PER_USER_MAX、全局在途上限 EXPORT_QUEUE_MAX（计数来自共享
    SQLite，多 worker 一致）；超限直接业务拒绝（ExportRejected），不是 500；
  · 运行中周期性回写已写行数（进度）并检查是否被取消/删除（取消即删 job 记录）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .store import ExportJob, get_export_store

logger = logging.getLogger("datachat.exports")


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _max_rows() -> int:
    # Excel 物理上限 1,048,576；预留表头/缓冲，业务默认 1,040,000。
    return min(_env_int("EXPORT_MAX_ROWS", 1_040_000), 1_048_575)


def _ttl() -> int:
    return _env_int("EXPORT_TTL_SECONDS", 86_400)


def _per_user_max() -> int:
    return _env_int("EXPORT_PER_USER_MAX", 3)


def _queue_max() -> int:
    return _env_int("EXPORT_QUEUE_MAX", 50)


def _progress_every() -> int:
    return _env_int("EXPORT_PROGRESS_EVERY", 20_000)


def _out_dir() -> Path:
    from app.core.config import load_config
    backend_root = load_config().app.semantic_path.parent.parent
    d = Path(os.environ.get("DATACHAT_EXPORT_DIR", str(backend_root / "reports" / "exports")))
    d.mkdir(parents=True, exist_ok=True)
    return d


class ExportRejected(Exception):
    """背压/队列上限拒绝 —— 路由层据此返回业务错误（非 500）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ExportService:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=_env_int("EXPORT_JOB_MAX_WORKERS", 2),
                                        thread_name_prefix="export-job")
        self._lock = threading.RLock()

    # ----------------------------------------------------------------- submit

    def submit(self, *, user_id: str, conversation_id: str, trace_id: str, trusted: dict[str, Any]) -> ExportJob:
        store = get_export_store()
        # 背压：每用户 / 全局在途上限（计数来自共享 SQLite，多 worker 一致）。
        if store.count_active_for_user(user_id) >= _per_user_max():
            raise ExportRejected("您有较多导出任务在排队/生成中，请等其完成后再导出。")
        if store.count_active() >= _queue_max():
            raise ExportRejected("当前导出任务较多，请稍后再试。")

        question = str(trusted.get("question") or "导出")
        ts = time.strftime("%Y%m%d_%H%M%S")
        # 下载用文件名（人类友好，可重复）；物理文件名稍后按 job id 唯一命名。
        download_name = f"datachat_export_{ts}.xlsx"
        job = store.create(
            user_id=user_id, conversation_id=conversation_id, trace_id=trace_id,
            question=question, filename=download_name, expires_at=time.time() + _ttl(),
        )
        # trusted 在提交线程里已取好（含归属校验），直接带进后台线程，避免再查会话
        self._pool.submit(self._run, job.id, trusted)
        return job

    # -------------------------------------------------------------------- run

    def _run(self, job_id: str, trusted: dict[str, Any]) -> None:
        store = get_export_store()
        store.update(job_id, status="running", row_count=0)
        # 物理路径按 job id 唯一命名 —— 同一秒提交的多个 job 各自独立文件，绝不互相覆盖。
        path = _out_dir() / f"{job_id}.xlsx"
        cap = _max_rows()
        try:
            columns, rows_iter = _row_source(trusted, cap=cap)
            written, truncated = _write_xlsx(
                path, columns, rows_iter, cap=cap,
                on_progress=lambda n: self._progress(job_id, n),
                is_cancelled=lambda: self._is_cancelled(job_id),
            )
            if self._is_cancelled(job_id):
                # 用户在生成途中取消/删除了 job —— 清理半成品文件，不落 ready。
                try:
                    if path.exists():
                        path.unlink()
                except Exception:  # noqa: BLE001
                    pass
                logger.info("[export %s] cancelled mid-run", job_id)
                return
            store.update(job_id, status="ready", path=str(path), row_count=written,
                         truncated=1 if truncated else 0)
            logger.info("[export %s] ready rows=%s truncated=%s", job_id, written, truncated)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[export %s] failed: %s", job_id, exc)
            try:
                if path.exists():
                    path.unlink()
            except Exception:  # noqa: BLE001
                pass
            store.update(job_id, status="error", error="导出失败，请稍后重试或联系管理员。")

    def _progress(self, job_id: str, written: int) -> None:
        try:
            get_export_store().update(job_id, row_count=written)
        except Exception:  # noqa: BLE001
            pass

    def _is_cancelled(self, job_id: str) -> bool:
        """取消 = 删除 job 记录（DELETE 接口）。记录消失或被标 expired → 视为取消。"""
        job = get_export_store().get(job_id)
        return job is None or job.status == "expired"

    # ------------------------------------------------------------- maintenance

    def cleanup_expired(self) -> None:
        for job in get_export_store().expire_due():
            try:
                if job.path and Path(job.path).exists():
                    Path(job.path).unlink()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------- helpers

def _row_source(trusted: dict[str, Any], *, cap: int) -> tuple[list[str], Iterable[list[Any]]]:
    """返回 (表头, 行迭代器)。优先 SQL **流式**重跑，失败回退存档结果表。"""
    answer = trusted.get("answer") or {}
    sql = str(trusted.get("sql") or "")
    if sql:
        try:
            from app.core.exec.mysql_exec import get_executor
            gen = get_executor().stream_select(sql, max_rows=cap)
            columns = list(next(gen))  # 第一个产出是表头
            return [str(c) for c in columns], gen
        except StopIteration:
            return [], iter(())
        except Exception as exc:  # noqa: BLE001
            logger.info("export SQL stream unavailable, fall back to stored rows: %s", exc)
    table = answer.get("table") or {}
    display_cols = table.get("display_columns") or []
    columns = [str(c.get("label") or c.get("key") or "") for c in display_cols] if display_cols else list(table.get("columns") or [])
    rows = table.get("display_rows") or table.get("rows") or []
    return columns, rows


def _write_xlsx(path: Path, columns: list[str], rows: Iterable[list[Any]], *, cap: int,
                on_progress=None, is_cancelled=None) -> tuple[int, bool]:
    """openpyxl write_only 流式写出；行数超 cap 截断并尾注。返回 (写入行数, 是否截断)。

    每写 EXPORT_PROGRESS_EVERY 行回写一次进度，并检查是否被取消（取消则提前收尾）。
    """
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("数据")
    if columns:
        ws.append([str(c) for c in columns])
    written, truncated = 0, False
    step = _progress_every()
    for row in rows:
        if written >= cap:
            truncated = True
            break
        if is_cancelled is not None and written and written % step == 0 and is_cancelled():
            break
        if isinstance(row, (list, tuple)):
            ws.append([_safe_cell(v) for v in row])
        else:
            ws.append([_safe_cell(row)])
        written += 1
        if on_progress is not None and written % step == 0:
            on_progress(written)
    if truncated:
        ws.append([f"（已达导出上限 {cap} 行，超出部分未包含）"])
    wb.save(str(path))
    return written, truncated


def _safe_cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


_service: Optional[ExportService] = None
_service_lock = threading.RLock()


def get_export_service() -> ExportService:
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = ExportService()
        return _service
