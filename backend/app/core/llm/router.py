"""LLM Router — single endpoint via Aliyun Bailian (DashScope) OpenAI-compatible API.

- All calls use httpx with retry + timeout + structured JSON enforcement.
- Stage temperature defaults to 0 — accuracy first.
- Reasoning models can be sped up via `enable_thinking=False`.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.core.config import LLMConfig, V1Config, load_config

logger = logging.getLogger("datachat.llm")


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    raw: dict[str, Any]
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, Any]


class LLMRouter:
    """Routes chat / json / embedding calls to the best available provider."""

    def __init__(self, cfg: V1Config | None = None):
        self.cfg = cfg or load_config()
        self.llm: LLMConfig = self.cfg.llm
        # Separate connect-timeout and read-timeout: connect should fail fast,
        # read should be generous because reasoning models can take ~60-90s.
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=float(self.llm.connect_timeout_seconds),
                read=float(self.llm.timeout_seconds),
                write=30.0,
                pool=30.0,
            )
        )
        self._embedding_dim: int | None = None
        self._feihe = None  # lazy 飞鹤网关

    # --------------------------------------------------------- provider switch

    def _use_feihe(self) -> bool:
        import os
        prov = (os.environ.get("LLM_PROVIDER") or self.cfg.llm.primary_provider or "").strip().lower()
        if prov != "feihe":
            return False
        try:
            from app.core.llm.feihe_gateway import FeiheGatewayClient
            if self._feihe is None:
                self._feihe = FeiheGatewayClient(
                    timeout_seconds=self.llm.timeout_seconds,
                    connect_timeout_seconds=self.llm.connect_timeout_seconds,
                )
            return bool(self._feihe.configured)
        except Exception as exc:  # noqa: BLE001
            logger.warning("feihe gateway unavailable, fallback to bailian: %s", exc)
            return False

    # ------------------------------------------------------------------ chat

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        model: str | None = None,
    ) -> LLMResult:
        if self._use_feihe():
            return self._chat_feihe(messages)
        return self._chat_bailian(messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode, model=model)

    def _chat_feihe(self, messages: list[dict[str, str]]) -> LLMResult:
        from app.core.llm.feihe_gateway import FeiheGatewayError
        prompt = "\n\n".join(f"[{m.get('role','user')}]\n{m.get('content','')}" for m in messages)
        started = time.perf_counter()
        try:
            text, conv_id = self._feihe.chat(prompt)
        except FeiheGatewayError as exc:
            raise LLMError(str(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResult(
            text=text or "", raw={"conversationId": conv_id}, provider="feihe",
            model=self._feihe.agent_code, latency_ms=latency_ms, usage={},
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema_hint: str | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> tuple[Any, LLMResult]:
        sys_msg = (messages[0]["content"] if messages and messages[0].get("role") == "system" else "")
        if schema_hint:
            sys_msg = (sys_msg + "\n" if sys_msg else "") + (
                "你必须仅返回符合下面 JSON Schema 的纯 JSON，不要任何解释、不要 ```json 标记。\n"
                f"Schema: {schema_hint}"
            )
        if messages and messages[0].get("role") == "system":
            messages = [{"role": "system", "content": sys_msg}, *messages[1:]]
        else:
            messages = [{"role": "system", "content": sys_msg or "你必须仅返回纯 JSON。"}, *messages]

        result = self.chat(messages, temperature=temperature, json_mode=True, model=model)
        text = result.text or ""
        parsed = _safe_json_parse(text)
        if parsed is None:
            # one repair pass — ask the model to re-emit JSON only
            repair_msgs = [
                {"role": "system", "content": "你只输出 JSON。下面这段不是合法 JSON。请只重新输出合法 JSON 对象。"},
                {"role": "user", "content": text[:4000]},
            ]
            second = self.chat(repair_msgs, temperature=0.0, json_mode=True, model=model)
            parsed = _safe_json_parse(second.text or "")
            result = second
        if parsed is None:
            raise LLMError(f"LLM did not return JSON. Last text: {text[:500]}")
        return parsed, result

    # ------------------------------------------------------------ embeddings

    def embed(self, inputs: Iterable[str], *, model: str | None = None) -> list[list[float]]:
        items = [str(x).strip() for x in inputs if str(x).strip()]
        if not items:
            return []
        chosen = model or self.llm.bailian_embed_model
        url = self.llm.bailian_base_url.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self.llm.bailian_api_key}",
            "Content-Type": "application/json",
        }
        out: list[list[float]] = []
        # DashScope text-embedding-v3 caps batch at 10
        batch_size = 10
        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            payload = {"model": chosen, "input": batch, "encoding_format": "float"}
            resp = self._post_with_retry(url, headers=headers, json=payload, retries=3)
            data = resp.json()
            data_arr = data.get("data") or []
            data_arr.sort(key=lambda x: int(x.get("index") or 0))
            for entry in data_arr:
                vec = entry.get("embedding") or []
                if vec:
                    out.append([float(v) for v in vec])
                    if self._embedding_dim is None:
                        self._embedding_dim = len(vec)
        return out

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim or 1024

    # ----------------------------------------------------------- providers

    def _chat_bailian(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
        model: str | None,
    ) -> LLMResult:
        if not self.llm.bailian_api_key:
            raise LLMError("Bailian / DashScope API key not configured (DASHSCOPE_API_KEY).")
        chosen = model or self.llm.bailian_chat_model
        url = self.llm.bailian_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.llm.bailian_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
            "temperature": (temperature if temperature is not None else self.llm.chat_temperature),
            "max_tokens": (max_tokens or self.llm.max_tokens),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # Disable chain-of-thought reasoning for speed — DashScope-specific.
        # Saves 30-60s per call on qwen3.6-max-preview / qwen3-thinking models.
        if self.llm.disable_thinking:
            payload["enable_thinking"] = False
        started = time.perf_counter()
        resp = self._post_with_retry(url, headers=headers, json=payload, retries=self.llm.max_retries)
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = resp.json()
        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except Exception:
            text = ""
        usage = data.get("usage") or {}
        return LLMResult(text=text, raw=data, provider="bailian", model=chosen, latency_ms=latency_ms, usage=usage)

    # ----------------------------------------------------------- internals

    def _post_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        retries: int = 1,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        attempts = max(1, int(retries) + 1)
        for attempt in range(1, attempts + 1):
            try:
                resp = self._client.post(url, headers=headers, json=json)
                if resp.status_code == 400:
                    # Likely "enable_thinking" not supported → drop & retry once.
                    if isinstance(json, dict) and json.pop("enable_thinking", None) is not None:
                        logger.info("LLM rejected enable_thinking=False — retrying without it")
                        continue
                    raise LLMError(f"bad request: {resp.text[:300]}")
                if resp.status_code >= 500:
                    raise LLMError(f"upstream {resp.status_code}: {resp.text[:200]}")
                if resp.status_code == 429:
                    raise LLMError(f"rate limited: {resp.text[:200]}")
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.HTTPError, LLMError) as exc:
                last_exc = exc
                if attempt >= attempts:
                    break
                wait = min(2 ** attempt, 4)
                logger.warning("LLM call attempt %s/%s failed: %s (sleep %ss)", attempt, attempts, exc, wait)
                time.sleep(wait)
        assert last_exc is not None
        raise LLMError(str(last_exc))


def _safe_json_parse(text: str) -> Any | None:
    if not text:
        return None
    text = text.strip()
    # strip ```json ... ``` fences if present
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    # find outermost JSON braces
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return None


_router_singleton: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = LLMRouter()
    return _router_singleton
