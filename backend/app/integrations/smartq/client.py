"""SmartQ（Quick BI 私有化 OpenAPI）客户端。

安全：
  · 身份**服务端映射**——调用 SmartqQueryAbility 时的 UserId 一律由服务端按
    DataChat 身份解析（默认 SMARTQ_DEFAULT_USER_ID / USER_TOKEN），**绝不**信任前端传入；
  · 任何异常只回友好业务错误，不外泄密钥/底层细节；
  · 未启用/未配置时直接 SmartQError("未启用")。

签名：私有化 Quick BI OpenAPI 通常经阿里 API 网关暴露，采用 app 级 HMAC-SHA256
（x-ca-* 头）。该方案集中在 `_signed_headers`，若部署使用别的签名，改这一处即可。
真实联调需生产凭证；本地无法触达 quickbi.feihe.com，故调用失败时优雅降级。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Optional

import httpx

from .config import SmartQConfig, load_smartq_config

logger = logging.getLogger("datachat.smartq")

_TIMEOUT = 30.0


class SmartQError(Exception):
    """对用户友好的 SmartQ 错误（路由层据此回业务错误，不暴露内部细节）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SmartQClient:
    def __init__(self, cfg: Optional[SmartQConfig] = None):
        self.cfg = cfg or load_smartq_config()

    # ----------------------------------------------------------------- 公共 API

    def list_datasets(self, *, user_id: str) -> list[dict[str, Any]]:
        """QueryLlmCubeWithThemeListByUserId：列出该用户被授权的数据集/主题。"""
        data = self._request("QueryLlmCubeWithThemeListByUserId", {"UserId": user_id})
        result = data.get("Result") or data.get("result") or []
        out: list[dict[str, Any]] = []
        items = result if isinstance(result, list) else (result.get("CubeList") if isinstance(result, dict) else [])
        for it in items or []:
            if not isinstance(it, dict):
                continue
            out.append({
                "cube_id": str(it.get("CubeId") or it.get("cubeId") or it.get("Id") or ""),
                "name": str(it.get("CubeName") or it.get("Name") or it.get("cubeName") or "未命名数据集"),
                "theme": str(it.get("ThemeName") or it.get("Theme") or ""),
            })
        return [d for d in out if d["cube_id"]]

    def dataset_status(self, *, cube_id: str) -> dict[str, Any]:
        """QueryDatasetSmartqStatus：查某数据集是否已开启 SmartQ。"""
        data = self._request("QueryDatasetSmartqStatus", {"CubeId": cube_id})
        result = data.get("Result")
        if isinstance(result, dict):
            status = result.get("SmartqStatus") if "SmartqStatus" in result else result.get("Status")
        else:
            status = result
        return {"cube_id": cube_id, "enabled": str(status).lower() in ("true", "1", "on", "enabled")}

    def query(self, *, user_question: str, cube_id: str = "", cube_ids: Optional[list[str]] = None,
              user_id: str) -> dict[str, Any]:
        """SmartqQueryAbility：自然语言问数。返回原始 Result（交给 normalize 归一化）。"""
        params: dict[str, Any] = {"UserQuestion": user_question, "UserId": user_id}
        if cube_ids:
            params["MultipleCubeIds"] = ",".join(cube_ids)
        elif cube_id:
            params["CubeId"] = cube_id
        data = self._request("SmartqQueryAbility", params)
        return data.get("Result") if isinstance(data.get("Result"), dict) else data

    # ------------------------------------------------------------------ 传输层

    def _request(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.ready:
            raise SmartQError("智能小Q（SmartQ）未启用或未配置完整，请联系管理员。")
        # 身份必须服务端给定，绝不信任前端
        body = {**params, "UserToken": self.cfg.user_token}
        url = f"{self.cfg.server_domain}{self.cfg.api_base}/{action}"
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = self._signed_headers("POST", f"{self.cfg.api_base}/{action}", raw)
        try:
            with httpx.Client(timeout=_TIMEOUT) as c:
                resp = c.post(url, content=raw, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[smartq] %s transport error: %s", action, exc)
            raise SmartQError("智能小Q服务暂时不可用，请稍后再试。")
        if resp.status_code >= 400:
            logger.warning("[smartq] %s HTTP %s: %s", action, resp.status_code, resp.text[:300])
            raise SmartQError("智能小Q请求失败，请稍后再试或联系管理员。")
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise SmartQError("智能小Q返回异常，请稍后再试。")
        if not isinstance(data, dict):
            raise SmartQError("智能小Q返回异常，请稍后再试。")
        # 官方返回校验：Success 出现且为 False → 业务失败；Success 缺失但也无 Result/result
        # → 视为异常返回（绝不把空壳响应当成成功）。
        if data.get("Success") is False or str(data.get("Code") or "").lower() in ("false", "error"):
            logger.warning("[smartq] %s business error: code=%s msg=%s",
                           action, data.get("Code"), str(data.get("Message"))[:200])
            raise SmartQError("智能小Q无法回答该问题，请换个问法或检查数据集权限。")
        if "Success" not in data and "Result" not in data and "result" not in data:
            logger.warning("[smartq] %s unexpected response shape: %s", action, str(data)[:200])
            raise SmartQError("智能小Q返回异常，请稍后再试。")
        return data

    def _signed_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        """阿里 API 网关 app 级 HMAC-SHA256 签名（x-ca-*）。部署若用别的方案改这里。"""
        nonce = uuid.uuid4().hex
        ts = str(int(time.time() * 1000))
        content_md5 = base64.b64encode(hashlib.md5(body).digest()).decode() if body else ""
        accept, ctype = "application/json", "application/json"
        signed = {"x-ca-key": self.cfg.api_key, "x-ca-nonce": nonce, "x-ca-timestamp": ts}
        signed_str = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
        string_to_sign = (
            f"{method}\n{accept}\n{content_md5}\n{ctype}\n\n{signed_str}{path}"
        )
        signature = base64.b64encode(
            hmac.new(self.cfg.api_secret.encode(), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode()
        headers = {
            "Accept": accept,
            "Content-Type": ctype,
            "x-ca-key": self.cfg.api_key,
            "x-ca-nonce": nonce,
            "x-ca-timestamp": ts,
            "x-ca-signature-method": "HmacSHA256",
            "x-ca-signature-headers": "x-ca-key,x-ca-nonce,x-ca-timestamp",
            "x-ca-signature": signature,
        }
        if content_md5:
            headers["Content-MD5"] = content_md5
        return headers


def resolve_smartq_user_id(cfg: SmartQConfig, *, fallback: str = "") -> str:
    """服务端身份映射：优先 SMARTQ_DEFAULT_USER_ID，其次 USER_TOKEN，最后传入兜底。
    永远不接受前端传来的 userId。"""
    return cfg.default_user_id or cfg.user_token or fallback
