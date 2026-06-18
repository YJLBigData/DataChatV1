"""SmartQ（阿里 Quick BI 智能小Q）配置 + 脱敏诊断。

全部从环境变量读取（本地放 gitignored 的 backend/config/runtime.local.env，
生产走真实环境变量）。**绝不**在任何返回/日志里吐明文密钥——一律脱敏。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class SmartQConfig:
    enabled: bool
    api_key: str
    api_secret: str
    user_token: str
    server_domain: str
    default_user_id: str
    api_base: str          # 私有化部署 OpenAPI 前缀；飞鹤环境为空，保留兼容其它网关前缀
    timeout: float
    debug: bool

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.server_domain)

    @property
    def ready(self) -> bool:
        return self.enabled and self.configured


def load_smartq_config() -> SmartQConfig:
    try:
        timeout = float(os.environ.get("SMARTQ_TIMEOUT_SECONDS", "30") or "30")
    except ValueError:
        timeout = 30.0
    return SmartQConfig(
        enabled=_truthy(os.environ.get("SMARTQ_ENABLED", "0")),
        api_key=(os.environ.get("SMARTQ_API_KEY", "") or "").strip(),
        api_secret=(os.environ.get("SMARTQ_API_SECRET", "") or "").strip(),
        user_token=(os.environ.get("SMARTQ_USER_TOKEN", "") or "").strip(),
        server_domain=(os.environ.get("SMARTQ_SERVER_DOMAIN", "") or "").strip().rstrip("/"),
        default_user_id=(os.environ.get("SMARTQ_DEFAULT_USER_ID", "") or "").strip(),
        api_base=(os.environ.get("SMARTQ_API_BASE", "") or "").strip().rstrip("/"),
        timeout=timeout if timeout > 0 else 30.0,
        debug=_truthy(os.environ.get("SMARTQ_DEBUG", "0")),
    )


def _mask(v: str) -> str:
    if not v:
        return ""
    n = len(v)
    if n <= 8:
        return "***"
    return f"{v[:3]}***{v[-4:]}"


def masked_diagnostics() -> dict:
    """管理员可见的脱敏诊断：是否启用/配置齐全/域名 + 脱敏后的 key 片段。"""
    cfg = load_smartq_config()
    return {
        "enabled": cfg.enabled,
        "configured": cfg.configured,
        "ready": cfg.ready,
        "server_domain": cfg.server_domain or "(未配置)",
        "api_base": cfg.api_base,
        "timeout": cfg.timeout,
        "debug": cfg.debug,
        "api_key": _mask(cfg.api_key),
        "api_secret": _mask(cfg.api_secret),
        "user_token": _mask(cfg.user_token),
        "default_user_id": cfg.default_user_id or "(未配置)",
    }
