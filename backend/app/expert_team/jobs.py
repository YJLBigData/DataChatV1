"""专家团后台任务管理器 —— 进程内线程池 + job 注册表（企业级加固版）。

为什么进程内、不用 Celery：`core/tasks.py` 明确是占位，没有 worker 在跑；50 人量级
+ 长任务（总监路由→多专家串行 LLM→合成，30s~2min）用**有界线程池**足矣，还能顺带
限制对 LLM 网关的并发压力。

加固点（相对最初版）：
  · 全部容量参数走环境变量（worker 数 / TTL / 每用户并发上限 / 全局队列深度）；
  · 每用户背压：同一用户在跑/排队的 job 超限直接拒绝（业务错误，非 500）；
  · 取消：排队中可即时取消；运行中 best-effort（编排无法中断，跑完丢弃结果不落库）；
  · job 记录落库（`expert_job_v1`）→ 审计 + 重启后历史可见；进程启动时把上次残留的
    queued/running 收敛为 interrupted。

实时进度/状态放内存供前端轮询；最终结果落库到专家团**会话**存储（真相源）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("datachat.expert_team")


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


class JobRejected(Exception):
    """提交被背压/队列上限拒绝 —— 路由层据此返回业务错误（非 500）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class JobState:
    job_id: str
    conversation_id: str
    user_id: str
    question: str
    status: str = "queued"             # queued | running | done | error | cancelled
    events: list[dict[str, Any]] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    error: str = ""
    cancel_requested: bool = False
    created_at: float = 0.0
    finished_at: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "events": list(self.events),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


_ACTIVE = ("queued", "running")


class ExpertJobManager:
    def __init__(self) -> None:
        self.max_workers = _env_int("EXPERT_JOB_MAX_WORKERS", 4)
        self.ttl = float(_env_int("EXPERT_JOB_TTL", 3600))
        self.per_user_limit = _env_int("EXPERT_JOB_PER_USER_LIMIT", 2)
        self.queue_max = _env_int("EXPERT_JOB_QUEUE_MAX", 50)
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="expert-job")
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.RLock()
        # 进程启动：把上次残留的 active job 记录收敛为 interrupted（上次进程已退出）。
        try:
            from .store import get_expert_store
            get_expert_store().reset_stale_active_jobs()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- submit/get

    def submit(
        self,
        *,
        conversation_id: str,
        user_id: str,
        is_admin: bool,
        question: str,
        expert_ids: Optional[list[str]],
        want_report: bool,
        llm_provider: Optional[str],
        smartq_cube_ids: Optional[list[str]] = None,
        history: Optional[list[dict[str, str]]] = None,
    ) -> str:
        now = time.time()
        # 背压计数取「本进程内存」与「共享 DB」的较大值：单 worker 时内存即准；
        # 多 worker 时 DB 让每用户/全局上限跨进程一致（审计 P0：多 worker 下限制不再 per-process）。
        db_user_active, db_total_active = self._db_active_counts(user_id)
        with self._lock:
            self._prune_locked(now)
            mem_user_active = sum(1 for j in self._jobs.values() if j.user_id == user_id and j.status in _ACTIVE)
            if max(mem_user_active, db_user_active) >= self.per_user_limit:
                raise JobRejected("您有正在进行的专家团分析，请等它完成后再提交新的分析。")
            mem_total_active = sum(1 for j in self._jobs.values() if j.status in _ACTIVE)
            if max(mem_total_active, db_total_active) >= self.queue_max:
                raise JobRejected("系统当前分析任务较多，请稍后再试。")
            job_id = "job_" + uuid.uuid4().hex[:16]
            job = JobState(job_id=job_id, conversation_id=conversation_id, user_id=user_id,
                           question=question, status="queued", created_at=now)
            self._jobs[job_id] = job
        self._record(job, status="queued")
        self._pool.submit(
            self._run, job,
            is_admin=is_admin, expert_ids=expert_ids, want_report=want_report,
            llm_provider=llm_provider, smartq_cube_ids=smartq_cube_ids, history=history,
        )
        return job_id

    def get(self, job_id: str, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                if user_id is not None and job.user_id != user_id:
                    return None
                return job.snapshot()
        # 内存里没有（多 worker：job 跑在别的进程；或本进程刚启动重挂）→ 回退共享 DB。
        return self._snapshot_from_db(job_id, user_id)

    def list_for_user(self, user_id: str, status: Optional[str] = None) -> list[dict[str, Any]]:
        """列出该用户的 job（默认全部；status='active' = queued|running）。
        前端刷新后据此**重新挂上**仍在跑的后台分析（survive SPA reload）。
        合并「本进程内存」与「共享 DB」记录（多 worker 下也能看全）。"""
        def _match(s: dict[str, Any]) -> bool:
            if status is None:
                return True
            if status == "active":
                return s.get("status") in _ACTIVE
            return s.get("status") == status

        out: dict[str, dict[str, Any]] = {}
        with self._lock:
            for j in self._jobs.values():
                if j.user_id == user_id:
                    snap = j.snapshot()
                    if _match(snap):
                        out[j.job_id] = snap
        # DB 里的在途 job（其他 worker 提交的）补进来，内存已有的不覆盖。
        if status in (None, "active", "queued", "running"):
            for rec in _db_list_active(user_id):
                jid = rec.get("job_id")
                if jid and jid not in out and _match(rec):
                    out[jid] = {
                        "job_id": jid, "conversation_id": rec.get("conversation_id"),
                        "status": rec.get("status"), "events": [], "result": None,
                        "error": rec.get("error") or "", "created_at": rec.get("created_at") or 0,
                        "finished_at": rec.get("finished_at") or 0,
                    }
        items = list(out.values())
        items.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
        return items

    def _db_active_counts(self, user_id: str) -> tuple[int, int]:
        try:
            from .store import get_expert_store
            store = get_expert_store()
            return store.count_active_jobs(user_id), store.count_active_global()
        except Exception:  # noqa: BLE001
            return 0, 0

    def _snapshot_from_db(self, job_id: str, user_id: Optional[str]) -> Optional[dict[str, Any]]:
        """跨 worker 轮询：从共享 DB 还原 job 快照。终态时从专家团会话取回最终结果。"""
        try:
            from .store import get_expert_store
            rec = get_expert_store().get_job(job_id)
        except Exception:  # noqa: BLE001
            rec = None
        if not rec:
            return None
        if user_id is not None and rec.get("user_id") != user_id:
            return None  # 归属校验：绝不跨用户泄露
        status = str(rec.get("status") or "")
        # interrupted（上次进程残留）对前端等价于 missing → 让前端标"已中断，请重试"。
        if status == "interrupted":
            return None
        result = None
        if status in ("done", "error"):
            result = self._result_from_conversation(rec.get("conversation_id"), user_id or rec.get("user_id"))
        return {
            "job_id": job_id, "conversation_id": rec.get("conversation_id"),
            "status": status, "events": [], "result": result,
            "error": rec.get("error") or "", "created_at": rec.get("created_at") or 0,
            "finished_at": rec.get("finished_at") or 0,
        }

    @staticmethod
    def _result_from_conversation(conversation_id: Optional[str], user_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not conversation_id:
            return None
        try:
            from .history import get_expert_conversation_store
            store = get_expert_conversation_store()
            sess = store.get_session(conversation_id)
            if not sess or (user_id is not None and sess.user_id != user_id):
                return None
            for m in reversed(store.list_messages(conversation_id, limit=50)):
                if m.role == "assistant":
                    res = (m.payload or {}).get("result")
                    return res if isinstance(res, dict) else None
        except Exception:  # noqa: BLE001
            return None
        return None

    def cancel(self, job_id: str, user_id: str) -> bool:
        """取消：排队中即时取消；运行中标记取消（编排跑完丢弃结果，不落 assistant 消息）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.user_id != user_id or job.status not in _ACTIVE:
                return False
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
        if job.status == "cancelled":
            self._update(job_id, "cancelled", finished_at=job.finished_at)
        return True

    # ----------------------------------------------------------------- worker

    def _run(self, job: JobState, *, is_admin: bool, expert_ids, want_report: bool,
             llm_provider, smartq_cube_ids, history) -> None:
        with self._lock:
            if job.cancel_requested:
                job.status = "cancelled"
                job.finished_at = time.time()
        if job.status == "cancelled":
            self._update(job.job_id, "cancelled", finished_at=job.finished_at)
            return
        with self._lock:
            job.status = "running"
        self._update(job.job_id, "running")

        # ContextVar 不会自动传播到线程池工作线程，必须在线程内重设 provider。
        try:
            from app.core.llm.router import set_request_provider
            set_request_provider(llm_provider)
        except Exception:  # noqa: BLE001
            pass

        def on_event(stage: str, payload: dict[str, Any]) -> None:
            try:
                with self._lock:
                    job.events.append({"stage": stage, **payload})
            except Exception:  # noqa: BLE001
                pass

        try:
            from .members import split_for_orchestrator
            from .orchestrator import get_orchestrator

            user_skills, overrides = split_for_orchestrator(job.user_id)
            result = get_orchestrator().run(
                job.question,
                user_id=job.user_id,
                is_admin=is_admin,
                selected_expert_ids=expert_ids or None,
                user_skills=user_skills,
                overrides=overrides,
                want_report=want_report,
                smartq_cube_ids=smartq_cube_ids or None,
                history=history,
                on_event=on_event,
            )
            with self._lock:
                cancelled = job.cancel_requested
            if cancelled:
                with self._lock:
                    job.status = "cancelled"
                    job.finished_at = time.time()
                self._update(job.job_id, "cancelled", finished_at=job.finished_at)
                return
            result["events"] = list(job.events)
            self._persist_message(job, result)
            # 编排返回 ok:false（全部专家失败 / 合成失败）→ job 落 error 终态，
            # 绝不把失败结果当成"done 的成功答案"（审计 P0）。
            ok = bool(result.get("ok", True))
            final_status = "done" if ok else "error"
            err = "" if ok else str(result.get("error") or "专家团分析未成功")
            with self._lock:
                job.result = result
                job.status = final_status
                job.error = err
                job.finished_at = time.time()
            self._update(job.job_id, final_status, error=err, finished_at=job.finished_at)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[expert job %s] crashed: %s", job.job_id, exc)
            err_result = {"ok": False, "error": "专家团分析失败，请稍后重试。", "report": "",
                          "experts": [], "plan": "", "route": "", "elapsed_ms": 0}
            self._persist_message(job, err_result)
            with self._lock:
                job.result = err_result
                job.status = "error"
                job.error = err_result["error"]
                job.finished_at = time.time()
            self._update(job.job_id, "error", error=err_result["error"], finished_at=job.finished_at)

    # ------------------------------------------------------------- persistence

    @staticmethod
    def _persist_message(job: JobState, result: dict[str, Any]) -> None:
        """把 assistant 产出落库到专家团会话（真相源，供刷新/重载/红点）。best-effort。"""
        try:
            from .history import get_expert_conversation_store
            store = get_expert_conversation_store()
            report = str(result.get("report") or result.get("error") or "")
            store.append_message(job.conversation_id, "assistant", report, payload={"result": result})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[expert job %s] persist failed: %s", job.job_id, exc)
        try:
            from .store import get_expert_store
            get_expert_store().log_run(job.user_id, job.question, {"result": result})
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _record(job: JobState, *, status: str) -> None:
        try:
            from .store import get_expert_store
            get_expert_store().upsert_job(
                job_id=job.job_id, user_id=job.user_id, conversation_id=job.conversation_id,
                question=job.question, status=status, created_at=job.created_at,
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _update(job_id: str, status: str, *, error: str = "", finished_at: float = 0.0) -> None:
        try:
            from .store import get_expert_store
            get_expert_store().update_job_status(job_id, status, error=error, finished_at=finished_at)
        except Exception:  # noqa: BLE001
            pass

    def _prune_locked(self, now: float) -> None:
        stale = [jid for jid, j in self._jobs.items()
                 if j.finished_at and (now - j.finished_at) > self.ttl]
        for jid in stale:
            self._jobs.pop(jid, None)


def _db_list_active(user_id: str) -> list[dict[str, Any]]:
    try:
        from .store import get_expert_store
        return get_expert_store().list_active_jobs(user_id)
    except Exception:  # noqa: BLE001
        return []


_manager_singleton: Optional[ExpertJobManager] = None
_manager_lock = threading.RLock()


def get_expert_job_manager() -> ExpertJobManager:
    global _manager_singleton
    if _manager_singleton is not None:
        return _manager_singleton
    with _manager_lock:
        if _manager_singleton is None:
            _manager_singleton = ExpertJobManager()
        return _manager_singleton
