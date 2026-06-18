"""LLM provider 目录与请求级路由上下文（从 router.py 拆出，零行为变化）。

包含：
  · 请求级 provider override（ContextVar，跨 asyncio/SSE 调用栈自动传播）；
  · `_clean_key` —— key 归一化（占位符/非 ASCII 视为未配置，杜绝 Bearer 头编码崩溃）；
  · `available_providers` / `default_provider` —— 右上角下拉目录与默认高亮。

这些都是**模块级纯函数**（只依赖 config/stores），与 LLMRouter 实例状态无关，
故独立成文件；router.py 仍 re-export 它们，调用方 import 路径不变。
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.core.config import load_config
from app.core.llm_settings import get_llm_settings_store
from app.core.llm_presets import get_llm_presets_store

# 请求级模型路由 override：右上角下拉框选择"百炼 / 飞鹤"时由 main.py 在请求入口设置，
# 通过 ContextVar 自动在 asyncio / SSE 流式调用栈里传播，不需要在 5+ 个方法签名里硬塞参数。
_provider_override: ContextVar[str | None] = ContextVar(
    "datachat_llm_provider_override", default=None,
)


def _clean_key(raw: Any) -> str:
    """归一化 API key：去空白；占位符（请填写/PLEASE_REPLACE）与**非 ASCII** 值一律视为未配置。

    审计 P2：中文占位符（"请填写…"）一旦被当成 key 拼进 Authorization 头，httpx 用 latin-1
    编码会直接抛 UnicodeEncodeError，连累问数/embedding 报"ASCII 编码错误"。真实 key 必为
    纯 ASCII，故非 ASCII 直接拒用，从源头杜绝该崩溃。
    """
    v = (str(raw or "")).strip()
    if not v:
        return ""
    low = v.lower()
    if any(m in low for m in ("请填写", "请替换", "please_replace")):
        return ""
    try:
        v.encode("ascii")
    except UnicodeEncodeError:
        return ""
    return v


def set_request_provider(provider: str | None) -> None:
    """请求入口调用，设置本次请求的 LLM provider（'bailian' / 'feihe' / None=用 env 默认）。"""
    norm = (provider or "").strip().lower() or None
    _provider_override.set(norm)


def get_request_provider() -> str | None:
    return _provider_override.get()


def available_providers() -> list[dict[str, Any]]:
    """右上角下拉。**无条件**列出项目内置的两套（飞鹤 kaier_znws / 百炼 qwen）+ 管理页的所有 preset。

    设计：
      · legacy 两条始终出现 —— 即便 key/AES_KEY 未配置也保留位置，
        让用户能在下拉里看到"它们存在"，hint 里写明是否需要去 .env / 管理页补 key
      · legacy 内置项 id 固定为 'legacy_bailian' / 'legacy_feihe'，飞鹤 = kaier_znws
      · 不回显任何 key/secret
    """
    out: list[dict[str, Any]] = []
    cfg = load_config()
    store = get_llm_settings_store()

    # 1) legacy: 飞鹤公司网关（kaier-znws）—— 默认排第一条
    feihe_agent_code = "kaier_znws"
    feihe_configured = False
    try:
        from app.core.llm.feihe_gateway import FeiheGatewayClient
        fc = FeiheGatewayClient()
        feihe_agent_code = fc.agent_code or feihe_agent_code
        feihe_configured = bool(fc.configured)
    except Exception:  # noqa: BLE001
        pass
    out.append({
        "id": "legacy_feihe",
        "label": f"飞鹤 · {feihe_agent_code}（内置）",
        "hint": (
            "公司 ADP Agent，业务数据不出公司网；AES_KEY 来自服务器 .env。"
            if feihe_configured else
            "公司 ADP Agent（kaier-znws）—— 需服务器 .env 配 AES_KEY 才能调用，但下拉永远保留它。"
        ),
        "provider": "feihe",
        "is_default": False,
        "is_legacy": True,
        "is_configured": feihe_configured,
    })

    # 2) legacy: 百炼 qwen（服务器 env / llm_settings 单条）
    bailian_key = store.get("DASHSCOPE_API_KEY", default=cfg.llm.bailian_api_key).strip()
    bailian_model = (
        store.get("DASHSCOPE_MODEL", default=cfg.llm.bailian_chat_model).strip()
        or cfg.llm.bailian_chat_model
        or "qwen3.6-max-preview"
    )
    # 占位符 / 提示文案视为"未配置"
    bailian_configured = bool(bailian_key) and not bailian_key.startswith("sk-请")
    out.append({
        "id": "legacy_bailian",
        "label": f"百炼 · {bailian_model}（内置）",
        "hint": (
            "DashScope 直连，AK 来自服务器 .env / 旧版单条 LLM 设置。"
            if bailian_configured else
            "百炼 qwen —— 需在 .env 或 LLM 设置里填 DASHSCOPE_API_KEY 才能调用，但下拉永远保留它。"
        ),
        "provider": "bailian",
        "is_default": False,
        "is_legacy": True,
        "is_configured": bailian_configured,
    })

    # 3) 管理页的 DB preset（用户在 LLM 设置页新建/编辑的那些）
    for p in get_llm_presets_store().list_all(include_inactive=False):
        out.append({
            "id": p.id,
            "label": p.name,
            "hint": ("百炼 · " if p.provider == "bailian" else "飞鹤 · ") + (p.model or ""),
            "provider": p.provider,
            "is_default": p.is_default,
            "is_legacy": False,
            "is_configured": bool(p.api_key) if p.provider == "bailian" else True,
        })

    # 4) SmartQ（Quick BI 智能小Q）—— 仅在启用且配置就绪时出现在下拉里
    try:
        from app.integrations.smartq.config import load_smartq_config
        scfg = load_smartq_config()
        if scfg.ready:
            out.append({
                "id": "smartq",
                "label": "智能小Q · Quick BI",
                "hint": "阿里 Quick BI 智能小Q（SmartQ）—— 选择数据集后用自然语言问数。",
                "provider": "smartq",
                "is_default": False,
                "is_legacy": False,
                "is_configured": True,
            })
    except Exception:  # noqa: BLE001
        pass

    # 标记当前 default（前端用 is_default 高亮 + 下拉初值）
    default_id = default_provider()
    for item in out:
        item["is_default"] = (item["id"] == default_id)
    return out


def default_provider() -> str:
    """默认选项 id 选择链（**只决定右上角下拉的默认高亮**，不决定实际调用路由）：
      1) DB preset.is_default=1 → 该 preset.id
      2) llm_settings 里写过 LLM_DEFAULT_PROVIDER_ID（管理页"内置设为默认"时写） → 该值
      3) 兜底 'legacy_feihe'（kaier-znws）—— 业务方要求

    特别说明：LLM_PROVIDER（env / config/env/*.env / 单条 llm_settings）
    只控制**没有 override 时**的 legacy 路由（_use_feihe 用），
    不参与下拉默认 —— 因为业务方明确要求 kaier-znws 永远是下拉默认。
    """
    p = get_llm_presets_store().get_default()
    if p:
        return p.id
    store = get_llm_settings_store()
    explicit = (store.get("LLM_DEFAULT_PROVIDER_ID", default="") or "").strip()
    if explicit in ("legacy_bailian", "legacy_feihe"):
        return explicit
    return "legacy_feihe"
