"""飞书推送 — 富文本卡片到群机器人 webhook 或个人 (按 email 查 open_id)。

支持两种通道，按可用性自动选择：
1. 自定义机器人 webhook（最简单，配 FEISHU_WEBHOOK 即可）
2. 企业自建应用：FEISHU_APP_ID + FEISHU_APP_SECRET，按 email 找用户

错误信息一律带上 Feishu 返回码与原始消息，方便排查。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("datachat.feishu")


class FeishuError(RuntimeError):
    pass


def _config() -> dict[str, str]:
    return {
        "app_id":              os.environ.get("FEISHU_APP_ID", "").strip(),
        "app_secret":          os.environ.get("FEISHU_APP_SECRET", "").strip(),
        "webhook":             os.environ.get("FEISHU_WEBHOOK", "").strip(),
        "default_user_email":  os.environ.get("FEISHU_DEFAULT_USER_EMAIL", "").strip(),
    }


def build_card(
    title: str,
    narrative: str,
    highlights: list[str],
    table_rows_preview: list[str],
    *,
    url: Optional[str] = None,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": narrative or "(无文案)"}},
    ]
    if highlights:
        bullets = "\n".join(f"- {h}" for h in highlights if h)
        if bullets:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**关键发现**\n" + bullets}})
    if table_rows_preview:
        rows_md = "\n".join(table_rows_preview[:5])
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**数据预览**\n```\n" + rows_md + "\n```"}})
    if url:
        elements.append({
            "tag": "action",
            "actions": [{"tag": "button", "text": {"tag": "plain_text", "content": "查看完整报告"}, "type": "primary", "url": url}],
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": (title or "飞鹤小Q · 经营分析")[:100]}, "template": "blue"},
        "elements": elements,
    }


# ----------------------------------------------------------- token cache
_token_lock = threading.Lock()
_token_cache: dict[str, tuple[str, float]] = {}   # app_id -> (token, expires_at_ts)


def _get_tenant_token(app_id: str, app_secret: str) -> str:
    with _token_lock:
        cached = _token_cache.get(app_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = httpx.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    except httpx.HTTPError as exc:
        raise FeishuError(f"获取 tenant_access_token 网络失败: {exc}") from exc
    if resp.status_code != 200:
        raise FeishuError(f"tenant_access_token HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(f"tenant_access_token 失败 [code={data.get('code')}]: {data.get('msg')}")
    token = str(data.get("tenant_access_token") or "")
    expire_in = int(data.get("expire") or 7200)
    with _token_lock:
        _token_cache[app_id] = (token, time.time() + expire_in)
    return token


def _email_to_open_id(token: str, email: str) -> str:
    """按 email 查 open_id。Feishu 接口返回 user_list[].user_id (实际是 open_id)。"""
    url = "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id?user_id_type=open_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"emails": [email]}
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=10)
    except httpx.HTTPError as exc:
        raise FeishuError(f"按 email 查 open_id 网络失败: {exc}") from exc
    if resp.status_code != 200:
        raise FeishuError(f"按 email 查 open_id HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(f"按 email 查 open_id 失败 [code={data.get('code')}]: {data.get('msg')}")
    user_list = ((data.get("data") or {}).get("user_list") or [])
    for item in user_list:
        # 同时兼容 open_id 和 user_id 两种字段名
        oid = item.get("open_id") or item.get("user_id") or ""
        if oid:
            return str(oid)
    raise FeishuError(f"未找到 email={email} 对应的飞书用户（可能不在企业内或未授权）")


def _send_webhook(card: dict[str, Any], webhook: str) -> dict[str, Any]:
    payload = {"msg_type": "interactive", "card": card}
    try:
        resp = httpx.post(webhook, json=payload, timeout=15)
    except httpx.HTTPError as exc:
        raise FeishuError(f"webhook 网络失败: {exc}") from exc
    if resp.status_code != 200:
        raise FeishuError(f"webhook HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    # 飞书自定义机器人成功是 {"StatusCode":0, ...} 或 {"code":0,...}
    code = data.get("code", data.get("StatusCode", 0))
    if code != 0:
        raise FeishuError(f"webhook 推送失败 [code={code}]: {data}")
    return data


def _send_app_to_user(card: dict[str, Any], app_id: str, app_secret: str, email: str) -> dict[str, Any]:
    token = _get_tenant_token(app_id, app_secret)
    open_id = _email_to_open_id(token, email)
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"receive_id": open_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=15)
    except httpx.HTTPError as exc:
        raise FeishuError(f"发送消息网络失败: {exc}") from exc
    if resp.status_code != 200:
        raise FeishuError(f"发送消息 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise FeishuError(f"发送消息失败 [code={data.get('code')}]: {data.get('msg')}")
    return data


def push(
    title: str,
    narrative: str,
    highlights: list[str],
    table_rows_preview: list[str],
    *,
    user_email: Optional[str] = None,
    webhook: Optional[str] = None,
    url: Optional[str] = None,
) -> dict[str, Any]:
    """统一入口。返回飞书 API 原始响应。

    路由策略：
      1. 显式传入 webhook 参数 → 走 webhook
      2. 显式传入 user_email 参数 → 走 app token + open_id
      3. 环境变量 FEISHU_WEBHOOK 存在 → 走 webhook
      4. 环境变量 FEISHU_APP_ID/SECRET + DEFAULT_USER_EMAIL 都存在 → 走 app
      5. 其他 → 抛出明确错误
    """
    cfg = _config()
    card = build_card(title, narrative, highlights, table_rows_preview, url=url)

    # 显式 webhook
    if webhook:
        return _send_webhook(card, webhook)
    # 显式 email + 应用配置
    if user_email and cfg["app_id"] and cfg["app_secret"]:
        return _send_app_to_user(card, cfg["app_id"], cfg["app_secret"], user_email)

    # 环境配置兜底
    if cfg["webhook"]:
        return _send_webhook(card, cfg["webhook"])
    if cfg["app_id"] and cfg["app_secret"] and cfg["default_user_email"]:
        return _send_app_to_user(card, cfg["app_id"], cfg["app_secret"], cfg["default_user_email"])

    # 完全未配置
    raise FeishuError(
        "飞书未配置。请在 backend/.env 中至少设置以下任一组合：\n"
        "  · FEISHU_WEBHOOK=自定义机器人 webhook URL\n"
        "  · FEISHU_APP_ID + FEISHU_APP_SECRET + FEISHU_DEFAULT_USER_EMAIL"
    )
