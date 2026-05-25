"""阶段 2.2 占位：Celery 应用 + 一个示例后台任务。

为什么是占位、不切主链路：
  · 50 人量级 + SSE 流式 + 多 worker uvicorn + 线程池 + 限流 已足以扛
  · 把 `/api/chat` 切到 Celery 等于把 SSE 流推链路打散，复杂度/故障面陡增
  · 真正适合 Celery 的是「跨请求的重活儿」：DOCX 报告批量生成、定时数据导出、
    异步邮件/webhook 通知、定时 retrieval index 重建……当未来真要做时
    把任务函数挂到 `tasks.py` 即可，broker / worker / systemd unit 都已就绪。

注意：本模块对外只暴露 `celery_app`；如果环境没装 celery（本地早期开发），
导入仍可成功（celery_app=None），调用方需自行判空。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("datachat.tasks")

celery_app = None
try:
    from celery import Celery  # type: ignore

    _BROKER = os.environ.get("CELERY_BROKER_URL") or os.environ.get("DATACHAT_REDIS_URL", "redis://127.0.0.1:6379/3")
    _BACKEND = os.environ.get("CELERY_RESULT_BACKEND") or _BROKER
    celery_app = Celery(
        "datachatv1",
        broker=_BROKER,
        backend=_BACKEND,
        include=["app.core.tasks"],
    )
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Shanghai",
        enable_utc=False,
        worker_max_tasks_per_child=200,  # 防止内存泄漏：每 200 个任务重启一次 worker
        worker_prefetch_multiplier=1,    # 长任务公平分发
        task_acks_late=True,             # 任务结束后才 ack，崩了能重投
        task_reject_on_worker_lost=True,
        broker_connection_retry_on_startup=True,
    )

    @celery_app.task(name="datachatv1.demo.ping", bind=True, max_retries=2)
    def demo_ping(self, msg: str = "pong") -> dict:
        """健康自检任务：worker 起来后跑一发就知道队列、broker、backend 三件套都通。"""
        logger.info("[celery] demo.ping received: %s", msg)
        return {"ok": True, "msg": msg, "task_id": self.request.id}

    @celery_app.task(name="datachatv1.report.generate_async", bind=True, max_retries=1, soft_time_limit=180)
    def generate_report_async(self, *, template_id: str, conversation_id: str, user_id: str) -> dict:
        """占位：将来"报告生成"如果要异步可挂到这里。
        当前 /api/report/generate 仍走同步链路；要切换时改 endpoint 投递任务即可。"""
        logger.info("[celery] report.generate_async placeholder template=%s conv=%s user=%s",
                    template_id, conversation_id, user_id)
        return {"ok": True, "note": "placeholder — 实现搬运自 generate_report() 时替换此处"}

    logger.info("celery_app configured: broker=%s", _BROKER)

except Exception as exc:  # pragma: no cover - celery 未安装时降级
    logger.warning("celery not available, async task queue disabled: %s", exc)
    celery_app = None
