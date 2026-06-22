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


def _env_first(*names: str, default: str = "") -> str:
    """Return the first non-empty env value, supporting old and canonical names."""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    low = v.lower()
    return (
        v.startswith("PLEASE_REPLACE")
        or "请填写" in v
        or "请部署" in v
        or low in {"changeme", "change-me", "none", "null"}
    )


class FeiheGatewayClient:
    """公司统一模型网关 chat 客户端。密钥仅来自环境变量。"""

    def __init__(self, *, timeout_seconds: int = 180, connect_timeout_seconds: int = 10):
        self.api_url = _env_first(
            "FEIHE_AGENT_API_URL",
            "FEIHE_LLM_BASE_URL",
            default="https://adp-test.feihe.com/adp-engine/v1/agent/chat",
        )
        self.service_open_id = _env_first(
            "FEIHE_SERVICE_OPEN_ID", "FEIHE_LLM_SERVICE_OPEN_ID", default="data_middle_platform"
        )
        self.authenticator = _env_first("FEIHE_AUTHENTICATOR", "FEIHE_LLM_AUTHENTICATOR", default="AES")
        self.agent_code = _env_first("FEIHE_AGENT_CODE", "FEIHE_LLM_AGENT_CODE", default="kaier_znws")
        self.tenant_code = _env_first(
            "FEIHE_TENANT_CODE", "FEIHE_LLM_TENANT_CODE", default="data_middle_platform"
        )
        self.channel = _env_first("FEIHE_CHANNEL", "FEIHE_LLM_CHANNEL", default="d2b-order")
        self.debug = (_env_first("FEIHE_AGENT_DEBUG", "FEIHE_LLM_DEBUG", default="true").lower() != "false")
        self.aes_key = _env_first("AES_KEY", "FEIHE_LLM_AES_KEY")
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=float(connect_timeout_seconds), read=float(timeout_seconds),
                write=30.0, pool=30.0,
            )
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_url and not _is_placeholder(self.aes_key))

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
            if isinstance(exc, httpx.TimeoutException):
                try:
                    from app.core.concurrency import note_llm_timeout
                    note_llm_timeout()
                except Exception:
                    pass
            raise FeiheGatewayError(f"飞鹤网关请求失败：{exc}")
        d = (data or {}).get("data") or {}
        content = d.get("chatResponseContent")
        if content is None:
            raise FeiheGatewayError(
                f"飞鹤网关响应无 data.chatResponseContent：{str(data)[:300]}"
            )
        return str(content), d.get("conversationId")
