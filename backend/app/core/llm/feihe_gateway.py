"""飞鹤统一模型网关（ADP Agent）客户端。

安全要求：
  · 任何密钥（AES_KEY）只从环境变量读取，绝不硬编码、绝不写入仓库。
  · 日志绝不打印 AES_KEY / x-sign 明文（只打印长度/前缀掩码）。

签名算法（与公司网关约定）：
  raw  = f"{service_open_id}_{authenticator}_{timestamp_ms}_{AES_KEY}"
  md5  = MD5(raw).hexdigest().upper()
  key  = base64decode(AES_KEY)            # AES 密钥
  enc  = AES/ECB/PKCS5Padding(key).encrypt(md5)
  sign = base64encode(enc)                # → 请求头 x-sign
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx

logger = logging.getLogger("datachat.llm.feihe")


class FeiheGatewayError(RuntimeError):
    pass


def build_sign(service_open_id: str, authenticator: str, timestamp_ms: int, aes_key_b64: str) -> str:
    """纯函数：生成 x-sign。可被单测确定性验证（固定 AES_KEY + timestamp）。"""
    if not aes_key_b64:
        raise FeiheGatewayError("AES_KEY 未配置：无法生成网关签名")
    from Crypto.Cipher import AES  # pycryptodome
    from Crypto.Util.Padding import pad

    raw = f"{service_open_id}_{authenticator}_{timestamp_ms}_{aes_key_b64}"
    md5_upper = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    try:
        key = base64.b64decode(aes_key_b64)
    except Exception as exc:  # noqa: BLE001
        raise FeiheGatewayError(f"AES_KEY 不是合法 Base64：{exc}")
    cipher = AES.new(key, AES.MODE_ECB)
    enc = cipher.encrypt(pad(md5_upper.encode("utf-8"), AES.block_size))
    return base64.b64encode(enc).decode("utf-8")


def _mask(secret: str) -> str:
    if not secret:
        return "(empty)"
    return f"len={len(secret)} prefix={secret[:2]}***"


class FeiheGatewayClient:
    """公司统一模型网关 chat 客户端。密钥仅来自环境变量。"""

    def __init__(self, *, timeout_seconds: int = 180, connect_timeout_seconds: int = 10):
        self.api_url = os.environ.get(
            "FEIHE_AGENT_API_URL", "https://adp-test.feihe.com/adp-engine/v1/agent/chat"
        )
        self.service_open_id = os.environ.get("FEIHE_SERVICE_OPEN_ID", "data_middle_platform")
        self.authenticator = os.environ.get("FEIHE_AUTHENTICATOR", "AES")
        self.agent_code = os.environ.get("FEIHE_AGENT_CODE", "kaier_znws")
        self.tenant_code = os.environ.get("FEIHE_TENANT_CODE", "data_middle_platform")
        self.channel = os.environ.get("FEIHE_CHANNEL", "d2b-order")
        self.debug = (os.environ.get("FEIHE_AGENT_DEBUG", "true").strip().lower() != "false")
        self.aes_key = os.environ.get("AES_KEY", "")
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=float(connect_timeout_seconds), read=float(timeout_seconds),
                write=30.0, pool=30.0,
            )
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.aes_key and self.aes_key != "PLEASE_REPLACE_AES_KEY")

    def chat(self, prompt: str, *, uid: str = "system", customer_id: str = "system",
             trace_id: Optional[str] = None) -> tuple[str, Any]:
        """返回 (chatResponseContent, conversationId)。失败抛 FeiheGatewayError。"""
        if not self.configured:
            raise FeiheGatewayError("飞鹤网关未配置 AES_KEY（请在服务器本地 .env 注入，勿入库）")
        ts = int(time.time() * 1000)
        sign = build_sign(self.service_open_id, self.authenticator, ts, self.aes_key)
        tid = trace_id or uuid.uuid4().hex
        headers = {
            "Content-Type": "application/json",
            "x-debug": "true" if self.debug else "false",
            "x-service-open-id": self.service_open_id,
            "x-authenticator": self.authenticator,
            "x-timestamp": str(ts),
            "x-sign": sign,
            "AGENT-CODE": self.agent_code,
        }
        body = {
            "tenantCode": self.tenant_code,
            "agentCode": self.agent_code,
            "channel": self.channel,
            "uid": str(uid or "system"),
            "traceId": tid,
            "contents": [{"type": "text", "value": prompt}],
            "extendParam": {"customerId": str(customer_id or "system")},
        }
        logger.info("feihe gateway call trace=%s aes_key=%s", tid, _mask(self.aes_key))
        try:
            resp = self._client.post(self.api_url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise FeiheGatewayError(f"飞鹤网关请求失败：{exc}")
        d = (data or {}).get("data") or {}
        content = d.get("chatResponseContent")
        if content is None:
            raise FeiheGatewayError(
                f"飞鹤网关响应无 data.chatResponseContent：{str(data)[:300]}"
            )
        return str(content), d.get("conversationId")
