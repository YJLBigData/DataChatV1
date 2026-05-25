"""LLM Router — single endpoint via Aliyun Bailian (DashScope) OpenAI-compatible API.

- All calls use httpx with retry + timeout + structured JSON enforcement.
- Stage temperature defaults to 0 — accuracy first.
- Reasoning models can be sped up via `enable_thinking=False`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.core.config import LLMConfig, V1Config, load_config
from app.core.llm_settings import get_llm_settings_store

# 请求级模型路由 override：右上角下拉框选择"百炼 / 飞鹤"时由 main.py 在请求入口设置，
# 通过 ContextVar 自动在 asyncio / SSE 流式调用栈里传播，不需要在 5+ 个方法签名里硬塞参数。
_provider_override: ContextVar[str | None] = ContextVar(
    "datachat_llm_provider_override", default=None,
)


def set_request_provider(provider: str | None) -> None:
    """请求入口调用，设置本次请求的 LLM provider（'bailian' / 'feihe' / None=用 env 默认）。"""
    norm = (provider or "").strip().lower() or None
    _provider_override.set(norm)


def get_request_provider() -> str | None:
    return _provider_override.get()


def available_providers() -> list[dict[str, Any]]:
    """探测当前**生效**配置允许哪些 provider；优先级 DB(管理页) > env > cfg 默认。不回显 key。"""
    cfg = load_config()
    store = get_llm_settings_store()
    out: list[dict[str, Any]] = []
    bailian_key = store.get("DASHSCOPE_API_KEY", default=cfg.llm.bailian_api_key).strip()
    bailian_model = store.get("DASHSCOPE_MODEL", default=cfg.llm.bailian_chat_model).strip()
    if bailian_key:
        out.append({
            "id": "bailian",
            "label": f"百炼 · {bailian_model}",
            "hint": "DashScope 直连，单次 ~10s；走你的 AK，业务数据会发到阿里云。",
        })
    try:
        from app.core.llm.feihe_gateway import FeiheGatewayClient
        c = FeiheGatewayClient()
        if c.configured:
            out.append({
                "id": "feihe",
                "label": f"飞鹤 · {c.agent_code}",
                "hint": "公司 ADP Agent，业务数据不出公司网；单次 ~30-120s。",
            })
    except Exception:  # noqa: BLE001
        pass
    return out


def default_provider() -> str:
    store = get_llm_settings_store()
    return store.get("LLM_PROVIDER", default=(load_config().llm.primary_provider or "feihe")).strip().lower() or "feihe"

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
        self._settings = get_llm_settings_store()  # 阶段 4：管理页热改的 DB 存储

    # ---- 阶段 4：可热改配置（DB 优先 → env → cfg 默认）----
    def _api_key(self) -> str:
        return (self._settings.get("DASHSCOPE_API_KEY", default=self.llm.bailian_api_key) or "").strip()

    def _base_url(self) -> str:
        return (self._settings.get("DASHSCOPE_BASE_URL", default=self.llm.bailian_base_url) or self.llm.bailian_base_url).strip()

    def _chat_model(self) -> str:
        return (self._settings.get("DASHSCOPE_MODEL", default=self.llm.bailian_chat_model) or self.llm.bailian_chat_model).strip()

    def _bailian_embed_model(self) -> str:
        return (self._settings.get("DASHSCOPE_EMBED_MODEL", default=self.llm.bailian_embed_model) or self.llm.bailian_embed_model).strip()

    # 兼容 hybrid retriever 里直接读 `self.llm.bailian_embed_model` 的老代码
    # 那是 LLMConfig 实例属性，无法动态改；提供一个等价的 router 属性供新代码使用。
    @property
    def bailian_embed_model(self) -> str:
        return self._bailian_embed_model()

    # --------------------------------------------------------- provider switch

    def _use_feihe(self) -> bool:
        # 路由优先级：① 本次请求 override（前端右上角下拉） ② DB/env LLM_PROVIDER ③ config primary
        override = _provider_override.get()
        if override:
            prov = override
        else:
            prov = self._settings.get("LLM_PROVIDER", default=self.cfg.llm.primary_provider or "").strip().lower()
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
        """Ask the LLM for pure JSON.

        飞鹤 kaier_znws 是 "Agent 模式"，倾向于输出 ```json 代码块 + 前置解释，
        每次 repair pass 等于一次完整的二次调用（~30-60s 净增延迟）。这里采用：
          1) system prompt 重锤强约束：第一个字符必须是 { 或 [
          2) _safe_json_parse 已加强对 fenced code block / 前置文字的剥壳
          3) 必要时再做一次 repair（兜底）
        """
        # ---- 重锤 system prompt：把"只输出 JSON"的要求放在最前面、最末尾各一次 ----
        original_sys = (messages[0]["content"] if messages and messages[0].get("role") == "system" else "")
        json_constraints = [
            "==== 输出格式硬性要求（违反任何一条都将被视为失败） ====",
            "1. 你的回复必须是【纯 JSON】，第一个字符必须是 { 或 [，最后一个字符必须是 } 或 ]。",
            "2. 不要输出 ```json、```、markdown 代码块、引号包裹或任何解释性前言/后语。",
            "3. 不要在 JSON 前后加任何文字（包括 \"以下是结果：\" / \"Here is...\" 等）。",
            "4. 不要带任何 emoji、HTML、注释（// 或 /* */）。",
            "5. 字符串值内部如果必须含双引号，请使用 \\\" 转义；其它字段忠实于 schema。",
        ]
        if schema_hint:
            json_constraints.append(f"6. JSON 必须严格满足以下 Schema：{schema_hint}")
        constraint_block = "\n".join(json_constraints)
        # 业务 system prompt 在前（解释任务），约束块在后（最近邻原则，飞鹤 Agent 更易遵守）
        sys_msg = (original_sys + "\n\n" + constraint_block) if original_sys else constraint_block

        if messages and messages[0].get("role") == "system":
            messages = [{"role": "system", "content": sys_msg}, *messages[1:]]
        else:
            messages = [{"role": "system", "content": sys_msg}, *messages]
        # 在最后一条 user 消息末尾再补一句最近邻提示（飞鹤 Agent 注意力末端最强）
        if messages and messages[-1].get("role") == "user":
            tail = messages[-1].get("content", "")
            messages[-1] = {"role": "user", "content": tail + "\n\n请记住：只输出纯 JSON，第一个字符是 { 或 [。"}

        result = self.chat(messages, temperature=temperature, json_mode=True, model=model)
        text = result.text or ""
        parsed = _safe_json_parse(text)
        if parsed is not None:
            return parsed, result

        # ---- 进入 repair pass 之前先记一条 warning，便于线上排查 ----
        logger.warning(
            "chat_json: first pass returned non-JSON (len=%d, head=%r) — running repair",
            len(text), text[:80].replace("\n", " "),
        )
        repair_msgs = [
            {"role": "system", "content": (
                "你只输出 JSON。下面这段不是合法 JSON。"
                "请只重新输出合法 JSON 对象，第一个字符必须是 { 或 [，禁止任何解释或代码块标记。"
            )},
            {"role": "user", "content": text[:4000]},
        ]
        second = self.chat(repair_msgs, temperature=0.0, json_mode=True, model=model)
        parsed = _safe_json_parse(second.text or "")
        if parsed is None:
            raise LLMError(f"LLM did not return JSON. Last text: {(second.text or text)[:500]}")
        return parsed, second

    # ------------------------------------------------------------ embeddings

    def embed(self, inputs: Iterable[str], *, model: str | None = None) -> list[list[float]]:
        items = [str(x).strip() for x in inputs if str(x).strip()]
        if not items:
            return []
        chosen = model or self._bailian_embed_model()
        url = self._base_url().rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
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
        _key = self._api_key()
        if not _key:
            raise LLMError("Bailian / DashScope API key not configured (后台→LLM 设置 里配置 DASHSCOPE_API_KEY)")
        chosen = model or self._chat_model()
        url = self._base_url().rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {_key}",
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


_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?```",
    re.DOTALL,
)


def _safe_json_parse(text: str) -> Any | None:
    """从 LLM 文本中尽力解析出 JSON。

    应对场景（飞鹤 Agent 高频）：
      A. 纯 JSON：直接 json.loads
      B. ```json {...} ``` 代码块（含前置/后置文字）
      C. "以下是结果：\\n{...}" 这种前置文字
      D. JSON 后面追加了 "希望对你有帮助" 之类的尾巴
      E. 数组顶层：[ {...}, {...} ]
    每命中一种就尝试解析；任何一步成功就立刻返回，绝不再回滚到 repair pass。
    """
    if not text:
        return None
    raw = text.strip()

    # A. 直接尝试
    try:
        return json.loads(raw)
    except Exception:
        pass

    # B. 提取所有 ```json ... ``` 代码块，从最长的开始尝试
    fence_bodies = sorted(
        (m.group("body").strip() for m in _FENCE_RE.finditer(raw)),
        key=len, reverse=True,
    )
    for body in fence_bodies:
        if not body:
            continue
        try:
            return json.loads(body)
        except Exception:
            pass
        # 代码块里还套了文字 → 在代码块内部再找一次 outer braces
        for opener, closer in (("{", "}"), ("[", "]")):
            s = body.find(opener)
            e = body.rfind(closer)
            if s != -1 and e > s:
                try:
                    return json.loads(body[s : e + 1])
                except Exception:
                    pass

    # C/D. 在整段文本里找最外层的 {} 或 []，对每个开括号位置尝试到结尾
    #     用 _try_balance_extract 处理"JSON 后面有尾巴文字"的情况
    for opener, closer in (("{", "}"), ("[", "]")):
        parsed = _try_balance_extract(raw, opener, closer)
        if parsed is not None:
            return parsed

    return None


def _try_balance_extract(text: str, opener: str, closer: str) -> Any | None:
    """从每个 opener 位置出发，扫描到字符串/转义都正确的对应 closer，json.loads。

    这样能正确处理 "前置文字 {valid_json} 后置尾巴" 这种情况——
    rfind(closer) 取最后一个会失败（尾巴里若有 } 也会包进来）。
    """
    n = len(text)
    i = 0
    while True:
        start = text.find(opener, i)
        if start == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        for j in range(start, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : j + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break  # 这个 start 不对，往后再找
        i = start + 1


_router_singleton: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = LLMRouter()
    return _router_singleton
