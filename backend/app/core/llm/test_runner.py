"""LLM 候选配置「保存前测试」—— 用最小代价问一句"你是什么模型"。

只有真的拿到非空响应才返回 ok=True；超时 / HTTP 非 200 / 空文本 → ok=False。
**不动 router 单例**，不影响线上正在跑的请求。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("datachat.llm.test")

DEFAULT_TEST_PROMPT = "请用一句话告诉我你是什么模型？"
DEFAULT_TIMEOUT = 20.0  # 秒；够 qwen-plus 一般 5-10s，长 chain reasoning 不在此用例


def test_bailian(*, api_key: str, base_url: str, model: str,
                 prompt: str = DEFAULT_TEST_PROMPT,
                 timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """直发一发 bailian/DashScope compatible-mode chat。"""
    if not api_key:
        return {"ok": False, "error": "缺 api_key", "latency_ms": 0, "text": ""}
    if not model:
        return {"ok": False, "error": "缺 model", "latency_ms": 0, "text": ""}
    url = (base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "temperature": 0,
        "enable_thinking": False,  # qwen reasoning 系列加速
    }
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=10.0)) as c:
            r = c.post(url, headers=headers, json=payload)
        elapsed = int((time.perf_counter() - started) * 1000)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}",
                    "latency_ms": elapsed, "text": ""}
        data = r.json()
        choices = data.get("choices") or []
        text = ""
        if choices:
            text = (choices[0].get("message", {}) or {}).get("content", "") or ""
        text = text.strip()
        if not text:
            return {"ok": False, "error": "模型回复为空", "latency_ms": elapsed, "text": "",
                    "raw": data}
        return {"ok": True, "latency_ms": elapsed, "text": text, "model_echo": data.get("model", model)}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"超时（>{timeout}s）", "latency_ms": int((time.perf_counter()-started)*1000), "text": ""}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"网络错误: {exc!s}"[:200],
                "latency_ms": int((time.perf_counter()-started)*1000), "text": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"未知错误: {exc!s}"[:200],
                "latency_ms": int((time.perf_counter()-started)*1000), "text": ""}


def test_feihe(*, model: str = "", prompt: str = DEFAULT_TEST_PROMPT,
               timeout: float = DEFAULT_TIMEOUT * 3) -> dict[str, Any]:
    """飞鹤 Agent 网关 ping。AES_KEY/URL 都在服务器 .env，preset 只决定"调不调"。"""
    try:
        from app.core.llm.feihe_gateway import FeiheGatewayClient, FeiheGatewayError
        client = FeiheGatewayClient(timeout_seconds=int(timeout))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"飞鹤网关模块加载失败: {exc!s}"[:200], "latency_ms": 0, "text": ""}
    if not client.configured:
        return {"ok": False, "error": "服务器 .env 未配置 FEIHE_AGENT_API_URL / AES_KEY",
                "latency_ms": 0, "text": ""}
    started = time.perf_counter()
    try:
        text, conv_id = client.chat(prompt)
        elapsed = int((time.perf_counter() - started) * 1000)
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "飞鹤回复为空", "latency_ms": elapsed, "text": ""}
        return {"ok": True, "latency_ms": elapsed, "text": text, "conv_id": conv_id}
    except FeiheGatewayError as exc:
        return {"ok": False, "error": f"飞鹤网关错误: {exc!s}"[:200],
                "latency_ms": int((time.perf_counter()-started)*1000), "text": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"未知错误: {exc!s}"[:200],
                "latency_ms": int((time.perf_counter()-started)*1000), "text": ""}


def test_preset_config(provider: str, *, api_key: str = "", base_url: str = "",
                       model: str = "", **_ignored) -> dict[str, Any]:
    """根据 provider 调用对应测试函数。"""
    prov = (provider or "").strip().lower()
    if prov == "bailian":
        return test_bailian(api_key=api_key, base_url=base_url, model=model)
    if prov == "feihe":
        return test_feihe(model=model)
    return {"ok": False, "error": f"不支持的 provider: {provider}", "latency_ms": 0, "text": ""}
