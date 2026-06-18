"""飞书推送路由（从 main.py 拆出，零行为变化）。

安全（P0）：推送的经营结论一律由后端按 (conversation_id, trace_id) 从会话存储取**可信结果**
生成，绝不信任前端传入的 narrative/highlights/rows_preview。收件人解析：管理员须显式传
user_email（系统账号查不到 open_id）；普通用户用自己绑定邮箱，请求体里的 user_email 忽略。
禁止请求体指定任意 webhook/url（SSRF）。失败只回友好错误，真实异常仅进日志。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends

from app.api.deps import require_user
from app.api.schemas import FeishuPushReq
from app.api.support import trusted_result_for_trace
from app.core.auth import User
from app.core.conversation import get_conversation_store
from app.core.feishu import FeishuError, push as feishu_push

logger = logging.getLogger("datachat.api")

router = APIRouter(tags=["feishu"])


@router.post("/api/feishu/push")
def api_feishu_push(req: FeishuPushReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    trace_id = uuid.uuid4().hex
    # 安全（P0）：推送的经营结论必须由后端按 trace 从会话存储取可信结果生成，
    # 不信任前端传入的 narrative/highlights/rows_preview，杜绝伪造结论推送。
    trusted = trusted_result_for_trace(get_conversation_store(), user, req.conversation_id, req.trace_id)
    answer = trusted["answer"]
    narrative = str(answer.get("narrative") or "")
    highlights = [str(h) for h in (answer.get("highlights") or []) if str(h).strip()][:10]
    table = answer.get("table") if isinstance(answer.get("table"), dict) else {}
    display_rows = table.get("display_rows") or table.get("rows") or []
    rows_preview = [
        " | ".join(str(c) for c in row)
        for row in display_rows[:5] if isinstance(row, (list, tuple))
    ]
    title = (trusted["question"] or "").strip()[:30] or "飞鹤小Q · 经营分析"

    # 安全（P0）：记录后端实际推送内容的指纹（sha256），便于审计追溯 / 防篡改取证。
    content_sha256 = hashlib.sha256(
        "\n".join([narrative, *highlights, *rows_preview]).encode("utf-8")
    ).hexdigest()

    # 安全（P1）：禁止请求体指定任意 webhook/url（SSRF / 内网探测）。
    # 推送目标只允许：服务端配置的 webhook，或按"用户邮箱→open_id"个人推送。
    #
    # 收件人解析规则：
    #   · 管理员：必须显式传 user_email（admin@feihe.com 这种系统账号在飞书查不到
    #     open_id，绝不应该 fallback 到 user.email 去试，会必失败）；
    #     若没传，target_email=None → 落到 env 里 FEISHU_WEBHOOK 兜底（管理群）。
    #   · 普通用户：用自己绑定的飞书邮箱 user.email。请求体里 user_email 一概忽略，
    #     防止越权推给别人。
    if user.role == "admin":
        target_email = (req.user_email or "").strip() or None
    else:
        target_email = (user.email or "").strip() or None
    logger.info("[trace=%s chat_trace=%s user=%s] feishu push content_sha256=%s to=%s",
                trace_id, req.trace_id, user.username, content_sha256, target_email or "(webhook)")
    try:
        feishu_push(
            title, narrative, highlights, rows_preview,
            user_email=target_email, webhook=None, url=None,
        )
        return {"ok": True, "trace_id": trace_id, "content_sha256": content_sha256}
    except FeishuError as exc:
        # 真实异常（含底层网络错误）只进日志，绝不回传用户侧
        logger.warning("[trace=%s user=%s] feishu push failed: %s", trace_id, user.username, exc)
        return {"ok": False, "error_code": "FEISHU_PUSH_FAILED",
                "user_message": "飞书推送失败，请确认已配置推送或联系管理员。",
                "trace_id": trace_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[trace=%s user=%s] feishu push crashed: %s", trace_id, user.username, exc)
        return {"ok": False, "error_code": "FEISHU_PUSH_ERROR",
                "user_message": "飞书推送失败，请稍后重试或联系管理员。",
                "trace_id": trace_id}
