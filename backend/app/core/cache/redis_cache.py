"""Redis-backed cache with three logical layers.

L1: question_text + user_role → final answer payload
L2: question_signature → QueryPlan IR (cheap to re-execute, expensive to plan)
L3: sql signature → result rows
plus: embedding cache (text → vector)

All keys are namespaced and JSON-encoded.
Falls back to NullCache when Redis is unavailable so the system never blocks.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

from app.core.config import CacheConfig, load_config

logger = logging.getLogger("datachat.cache")


def _fingerprint(*parts: Any) -> str:
    raw = "||".join(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class NullCache:
    enabled = False

    def get(self, key: str) -> Any:
        return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def get_question(self, *args, **kwargs):
        return None

    def set_question(self, *args, **kwargs):
        return None

    def get_plan(self, *args, **kwargs):
        return None

    def set_plan(self, *args, **kwargs):
        return None

    def get_sql_result(self, *args, **kwargs):
        return None

    def set_sql_result(self, *args, **kwargs):
        return None

    def get_embedding(self, *args, **kwargs):
        return None

    def set_embedding(self, *args, **kwargs):
        return None


class RedisCache:
    enabled = True

    def __init__(self, cfg: CacheConfig | None = None):
        self.cfg = cfg or load_config().cache
        if redis is None:
            raise RuntimeError("redis package not installed")
        self.client = redis.Redis.from_url(self.cfg.redis_url, decode_responses=True)
        # touch
        self.client.ping()

    # ------------------------------------------------------------ generic

    def _k(self, *segments: str) -> str:
        return self.cfg.namespace + ":" + ":".join(segments)

    def get(self, key: str) -> Any:
        try:
            raw = self.client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("cache get failed: %s", exc)
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
        except Exception as exc:
            logger.warning("cache set failed: %s", exc)

    def delete(self, key: str) -> None:
        try:
            self.client.delete(key)
        except Exception as exc:
            logger.warning("cache delete failed: %s", exc)

    # ----------------------------------------------------------- L1 question

    def get_question(self, question: str, user_id: str, ctx_fp: str = "") -> Any:
        key = self._k("q", _fingerprint(question, user_id, ctx_fp))
        return self.get(key)

    def set_question(self, question: str, user_id: str, ctx_fp: str, payload: Any) -> None:
        key = self._k("q", _fingerprint(question, user_id, ctx_fp))
        self.set(key, payload, ttl=self.cfg.ttl_question)

    # ----------------------------------------------------------------- L2 plan

    def get_plan(self, plan_signature: str) -> Any:
        return self.get(self._k("plan", plan_signature))

    def set_plan(self, plan_signature: str, plan: Any) -> None:
        self.set(self._k("plan", plan_signature), plan, ttl=self.cfg.ttl_plan)

    # -------------------------------------------------------------- L3 result

    def get_sql_result(self, sql: str) -> Any:
        return self.get(self._k("sql", _fingerprint(sql)))

    def set_sql_result(self, sql: str, payload: Any) -> None:
        self.set(self._k("sql", _fingerprint(sql)), payload, ttl=self.cfg.ttl_sql_result)

    # ------------------------------------------------------------- embedding

    def get_embedding(self, text: str, model: str) -> Any:
        return self.get(self._k("emb", model, _fingerprint(text)))

    def set_embedding(self, text: str, model: str, vec: list[float]) -> None:
        self.set(self._k("emb", model, _fingerprint(text)), vec, ttl=self.cfg.ttl_embedding)


def cache_status() -> dict[str, Any]:
    """健康检查用：准确反映 redis 包是否安装 / server 是否可连 / 是否启用 / 降级原因。"""
    cfg = load_config().cache
    pkg_installed = redis is not None
    inst = get_cache()
    inst_enabled = bool(getattr(inst, "enabled", False))
    connected = False
    reason = ""
    if not pkg_installed:
        reason = "redis package not installed"
    elif not cfg.enabled:
        reason = "cache disabled by config (DATACHAT_CACHE_ENABLED=0)"
    elif not inst_enabled:
        reason = "redis server unreachable — degraded to NullCache"
    else:
        try:
            inst.client.ping()
            connected = True
        except Exception as exc:
            reason = f"redis ping failed: {exc}"
    return {
        "enabled": bool(inst_enabled and connected),
        "redis_package_installed": pkg_installed,
        "redis_server_connected": connected,
        "redis_url": cfg.redis_url,
        "degrade_reason": reason,
    }


_cache_singleton: Any = None


def get_cache() -> Any:
    global _cache_singleton
    if _cache_singleton is not None:
        return _cache_singleton
    cfg = load_config().cache
    if not cfg.enabled:
        _cache_singleton = NullCache()
        return _cache_singleton
    try:
        _cache_singleton = RedisCache(cfg)
        logger.info("Redis cache connected at %s", cfg.redis_url)
    except Exception as exc:
        logger.warning("Redis cache disabled (%s) — using NullCache", exc)
        _cache_singleton = NullCache()
    return _cache_singleton
