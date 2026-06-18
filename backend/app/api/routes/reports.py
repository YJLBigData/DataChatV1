"""报告生成 + 报告模板路由（从 main.py 拆出，零行为变化）。

安全（P0）：报告内容由后端按 (conversation_id, trace_id) 取**可信结果**生成，不信任前端 payload。
模板归属：admin 通用；普通用户只能用系统模板或自己的、只能改/删自己的。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.deps import require_user
from app.api.schemas import ReportRequest, ReportTemplatePatchReq, ReportTemplateReq
from app.api.support import trusted_result_for_trace
from app.core.auth import User
from app.core.conversation import get_conversation_store
from app.core.report import generate_report
from app.core.report_templates import get_report_template_store

logger = logging.getLogger("datachat.api")

router = APIRouter(tags=["reports"])


@router.post("/api/report/generate")
def api_report(req: ReportRequest = Body(...), user: User = Depends(require_user)):
    backend_root = Path(__file__).resolve().parent.parent.parent.parent
    out_dir = backend_root / "reports" / "generated"
    # 安全（P0）：报告内容从会话存储按 trace 取可信结果，不信任前端 payload。
    trusted = trusted_result_for_trace(get_conversation_store(), user, req.conversation_id, req.trace_id)
    store = get_report_template_store()
    # 模板归属校验：admin 通用；普通用户只能用系统模板或自己的
    tpl = store.get(req.template_id) if req.template_id else None
    if tpl and user.role != "admin" and tpl.user_id and tpl.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权使用该模板")
    if not tpl:
        tpl = store.get_default_for_user(user.id)
    prompt = tpl.prompt if tpl else None
    name = tpl.name if tpl else "标准商业分析报告"
    try:
        path = generate_report(
            trusted["question"], trusted["answer"], trusted["plan"], trusted["sql"],
            output_dir=out_dir, template_prompt=prompt, template_name=name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[user=%s] report failed: %s", user.username, exc)
        raise HTTPException(status_code=500, detail="报告生成失败，请稍后重试，或联系管理员。")
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ====================================== 报告模板（user 隔离）
# 普通用户：看「系统默认」+「自己创建的」；只能改自己的
# admin：看全部，按用户筛选；可以改任何

@router.get("/api/report/templates")
def api_list_report_templates(user: User = Depends(require_user), owner: Optional[str] = Query(None)) -> dict[str, Any]:
    store = get_report_template_store()
    if user.role == "admin":
        if owner:
            items = [t for t in store.list_all() if t.user_id == owner or (owner == "system" and not t.user_id)]
        else:
            items = store.list_all()
    else:
        items = store.list_for_user(user.id)
    return {"items": [{"id": t.id, "name": t.name, "prompt": t.prompt,
                       "is_default": t.is_default, "user_id": t.user_id,
                       "is_system": not t.user_id,
                       "is_mine": t.user_id == user.id,
                       "created_at": t.created_at, "updated_at": t.updated_at} for t in items]}


@router.post("/api/report/templates")
def api_create_template(req: ReportTemplateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    """普通用户创建私有模板（user_id=自己）；admin 可选 user_id="" 创建系统模板。"""
    target_user_id = user.id if user.role != "admin" else (user.id if not getattr(req, "system", False) else "")
    try:
        t = get_report_template_store().create(name=req.name, prompt=req.prompt,
                                               is_default=req.is_default, user_id=target_user_id)
    except ValueError as exc:
        # 业务校验信息（如名称为空）对管理员可见即可，不含内部细节
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("[user=%s] create report template failed: %s", user.username, exc)
        raise HTTPException(status_code=500, detail="模板保存失败，请稍后重试或联系管理员。")
    return {"id": t.id, "name": t.name, "is_default": t.is_default, "user_id": t.user_id}


@router.patch("/api/report/templates/{tid}")
def api_update_template(tid: str, req: ReportTemplatePatchReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_report_template_store().update(
            tid, name=req.name, prompt=req.prompt, is_default=req.is_default,
            requester_user_id=user.id, requester_is_admin=(user.role == "admin"),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=403 if "无权" in str(exc) else 400, detail=str(exc))
    return {"ok": True}


@router.delete("/api/report/templates/{tid}")
def api_delete_template(tid: str, user: User = Depends(require_user)) -> dict[str, Any]:
    try:
        get_report_template_store().delete(
            tid,
            requester_user_id=user.id, requester_is_admin=(user.role == "admin"),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=403 if "无权" in str(exc) else 400, detail=str(exc))
    return {"ok": True}
