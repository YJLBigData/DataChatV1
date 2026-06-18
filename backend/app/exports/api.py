"""数据导出队列 HTTP 接口 —— 独立 APIRouter，在 main.py 一行挂载。

路由前缀 /api/exports：
  · POST /                 从可信 (conversation_id, trace_id) 提交导出 job → {ok, job}
  · GET  /                 列出我的导出 job（顺带清理过期）
  · GET  /{id}             查 job 状态
  · GET  /{id}/download    就绪后浏览器下载（归属 + 未过期 + 文件存在）
  · DELETE /{id}           取消/删除我的 job（连带删文件）

安全：内容一律取服务端可信结果，**绝不**接受前端 SQL；归属校验贯穿每个接口。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import require_user
from app.core.auth import User

from .service import ExportRejected, get_export_service
from .store import get_export_store

logger = logging.getLogger("datachat.exports")

router = APIRouter(prefix="/api/exports", tags=["exports"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportCreateReq(BaseModel):
    conversation_id: str
    trace_id: str


@router.post("")
def create_export(req: ExportCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    # 可信结果（含归属校验）：复用共享的 trusted_result_for_trace（app.api.support）。
    from app.api.support import trusted_result_for_trace
    from app.core.conversation import get_conversation_store
    trusted = trusted_result_for_trace(get_conversation_store(), user, req.conversation_id, req.trace_id)
    try:
        job = get_export_service().submit(
            user_id=user.id, conversation_id=req.conversation_id, trace_id=req.trace_id, trusted=trusted,
        )
    except ExportRejected as exc:
        # 背压/队列上限：业务拒绝，返回 200 + ok:false（前端弹友好提示，不当作崩溃）。
        return {"ok": False, "error": exc.message}
    return {"ok": True, "job": job.to_public()}


@router.get("")
def list_exports(user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_export_service().cleanup_expired()
    except Exception:  # noqa: BLE001
        pass
    items = get_export_store().list_for_user(user.id)
    return {"items": [j.to_public() for j in items]}


@router.get("/{job_id}")
def get_export(job_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    job = get_export_store().get(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return job.to_public()


@router.get("/{job_id}/download")
def download_export(job_id: str, user: User = Depends(require_user)):
    store = get_export_store()
    job = store.get(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if job.status == "expired":
        raise HTTPException(status_code=410, detail="该导出文件已过期，请重新导出")
    if job.status != "ready" or not job.path or not Path(job.path).exists():
        raise HTTPException(status_code=409, detail="导出尚未就绪或文件不可用")
    return FileResponse(job.path, filename=job.filename, media_type=_XLSX_MIME)


@router.delete("/{job_id}")
def delete_export(job_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    store = get_export_store()
    job = store.get(job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    file_deleted = False
    try:
        if job.path and Path(job.path).exists():
            Path(job.path).unlink()
            file_deleted = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete export file failed job=%s user=%s path=%s: %s", job_id, user.id, job.path, exc)
    deleted = store.delete(job_id)
    logger.info("delete export job=%s user=%s deleted=%s file_deleted=%s", job_id, user.id, deleted, file_deleted)
    return {"ok": deleted, "deleted": deleted, "job_id": job_id, "file_deleted": file_deleted}
