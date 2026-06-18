"""SmartQ HTTP 接口 —— 独立 APIRouter，在 main.py 一行 include 挂载。

路由前缀 /api/smartq：
  · GET  /status                用户：是否启用/就绪（无敏感信息，决定前端是否显示小Q入口）
  · GET  /diagnostics           管理员：脱敏配置诊断
  · GET  /datasets              用户：我被授权的数据集（服务端按身份解析，绝不信前端 userId）
  · GET  /datasets/{id}/status  用户：某数据集是否已开启 SmartQ
  · POST /query                 用户：自然语言问数 → 归一化成 answer 形态

安全：UserId 一律服务端映射（SMARTQ_DEFAULT_USER_ID / USER_TOKEN），数据集权限由 SmartQ
侧 + 本侧双重把关；任何异常只回业务错误，不外泄密钥。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from app.api.deps import require_admin, require_user
from app.core.auth import User

from .client import SmartQClient, SmartQError, resolve_smartq_user_id
from .config import load_smartq_config, masked_diagnostics
from .normalize import normalize_smartq_answer, smartq_answer_is_substantive

logger = logging.getLogger("datachat.smartq")

router = APIRouter(prefix="/api/smartq", tags=["smartq"])


class SmartQQueryReq(BaseModel):
    question: str
    cube_id: Optional[str] = None
    cube_ids: Optional[list[str]] = None
    conversation_id: Optional[str] = None


@router.get("/status")
def smartq_status(_: User = Depends(require_user)) -> dict[str, Any]:
    cfg = load_smartq_config()
    return {"enabled": cfg.enabled, "ready": cfg.ready}


@router.get("/diagnostics")
def smartq_diagnostics(_: User = Depends(require_admin)) -> dict[str, Any]:
    return masked_diagnostics()


@router.get("/datasets")
def smartq_datasets(user: User = Depends(require_user)) -> dict[str, Any]:
    cfg = load_smartq_config()
    if not cfg.ready:
        return {"ok": False, "error": "智能小Q未启用或未配置完整。", "items": []}
    uid = resolve_smartq_user_id(cfg, fallback=user.email or user.username)
    try:
        items = SmartQClient(cfg).list_datasets(user_id=uid)
        return {"ok": True, "items": items}
    except SmartQError as exc:
        return {"ok": False, "error": exc.message, "items": []}
    except Exception as exc:  # noqa: BLE001
        logger.exception("smartq datasets failed: %s", exc)
        return {"ok": False, "error": "获取数据集失败，请稍后再试。", "items": []}


@router.get("/datasets/{cube_id}/status")
def smartq_dataset_status(cube_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
    cfg = load_smartq_config()
    if not cfg.ready:
        return {"ok": False, "error": "智能小Q未启用或未配置完整。"}
    try:
        return {"ok": True, **SmartQClient(cfg).dataset_status(cube_id=cube_id)}
    except SmartQError as exc:
        return {"ok": False, "error": exc.message}
    except Exception as exc:  # noqa: BLE001
        logger.exception("smartq dataset status failed: %s", exc)
        return {"ok": False, "error": "查询数据集状态失败，请稍后再试。"}


@router.post("/query")
def smartq_query(req: SmartQQueryReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
    question = (req.question or "").strip()
    if not question:
        return {"ok": False, "error": "请输入问题"}
    cfg = load_smartq_config()
    if not cfg.ready:
        return {"ok": False, "error": "智能小Q未启用或未配置完整，请联系管理员。"}
    # 身份服务端解析；前端传入的 cube 仅作查询范围。
    uid = resolve_smartq_user_id(cfg, fallback=user.email or user.username)
    client = SmartQClient(cfg)

    # 数据集授权双保险：能拿到授权清单时，越权 cube 直接业务拒绝（不发起查询）。
    # 拿不到清单（如网络/接口波动）则不拦，交由查询本身 + SmartQ 侧把关。
    if req.cube_id:
        try:
            authorized = {d["cube_id"] for d in client.list_datasets(user_id=uid)}
            if authorized and req.cube_id not in authorized:
                return {"ok": False, "error": "无权访问该数据集，或数据集不存在。"}
        except SmartQError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("smartq cube authorization check skipped: %s", exc)

    try:
        raw = client.query(
            user_question=question, cube_id=req.cube_id or "",
            cube_ids=req.cube_ids or None, user_id=uid,
        )
    except SmartQError as exc:
        return {"ok": False, "error": exc.message}
    except Exception as exc:  # noqa: BLE001
        logger.exception("smartq query failed: %s", exc)
        return {"ok": False, "error": "智能小Q查询失败，请稍后再试。"}

    answer = normalize_smartq_answer(raw, question=question)
    # 假成功防线：无行 / 无结论 / 无 SQL 的空壳响应不当成功结果展示（审计 P0）。
    if not smartq_answer_is_substantive(answer):
        logger.info("smartq query returned no substantive result for cube=%s", req.cube_id)
        return {"ok": False, "error": "智能小Q未返回有效结果，请确认所选数据集权限或换个问法。"}

    sql = str(answer.get("explainability", {}).get("sql", "") or "")
    # 落库到问数会话（与普通问数共享 trace/导出/反馈/报告/飞书链路）——拿到可信 trace_id。
    conversation_id, trace_id = _persist_smartq_turn(user, req.conversation_id, question, answer, sql)
    return {
        "ok": True, "question": question, "answer": answer, "sql": sql,
        "conversation_id": conversation_id, "trace_id": trace_id,
        "rows": int(answer.get("table", {}).get("row_count") or 0),
    }


def _persist_smartq_turn(user: User, conversation_id: Optional[str], question: str,
                         answer: dict[str, Any], sql: str) -> tuple[str, str]:
    """把一轮 SmartQ 问答落库到**问数会话存储**（真相源），返回 (conversation_id, trace_id)。

    与普通问数完全一致的 payload 形态：assistant 消息带 {answer, plan, sql, trace_id}，
    使导出/报告/飞书/反馈这些"按 (conversation_id, trace_id) 取可信结果"的链路对 SmartQ 同样生效。
    落库失败不阻断回答（trace_id 退化为空，前端据此禁用相关操作）。
    """
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    try:
        from app.core.conversation import get_conversation_store
        store = get_conversation_store()
        cid = (conversation_id or "").strip()
        if cid:
            sess = store.get_session(cid)
            if not sess or sess.user_id != user.id:
                cid = ""  # 越权/不存在 → 新建会话，绝不写入他人会话
        if not cid:
            sess = store.create_session(user.id, title=(question[:30] or "智能小Q问数"))
            cid = sess.id
        store.append_message(cid, "user", question, payload={"source": "smartq"})
        store.append_message(
            cid, "assistant", str(answer.get("narrative") or ""),
            payload={
                "answer": answer, "plan": {}, "sql": sql,
                "rows": int(answer.get("table", {}).get("row_count") or 0),
                "cached": False, "trace_id": trace_id, "source": "smartq",
            },
        )
        return cid, trace_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("smartq persist failed: %s", exc)
        return (conversation_id or ""), ""
