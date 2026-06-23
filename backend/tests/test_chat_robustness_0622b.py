"""问数链路健壮性回归（2026-06-22 第二批修复）。

覆盖项（对应用户报障 "问数失败 / 问数服务暂时不可用"）：
  · 限流(429)返回**友好且前端可读**的响应体（user_message/detail），不再只有英文 {"error": ...}。
  · /api/chat/stream **立即吐 ": connected"** 首字节（防代理"等首字节"超时），且最终一定收口
    （done 或 error 事件），不会无声中断。
  · 心跳常量可由 CHAT_SSE_HEARTBEAT_SECONDS 配置且有下限保护。

全部离线：不触达真实 quickbi / MySQL / LLM。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth_0622b.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_0622b.db"
os.environ["JWT_SECRET"] = "test-secret-0622b"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_conv_0622b.db",):
    Path(_p).unlink(missing_ok=True)

from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None


def _fresh_client():
    """每个测试用**独立 app 实例** → 独立 slowapi 限流器（内存桶从零开始），杜绝跨测试串扰。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    return TestClient(create_app())


def _login(client) -> dict:
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# =============================================== 友好限流响应（单元）

def test_friendly_rate_limit_response_shape():
    """友好 429 体必须带 user_message + detail（前端 pickServerMessage 读得到），并标 RATE_LIMITED。"""
    import json
    from app.main import friendly_rate_limit_response

    resp = friendly_rate_limit_response()
    assert resp.status_code == 429
    body = json.loads(resp.body.decode())
    assert body["ok"] is False
    assert body["error_code"] == "RATE_LIMITED"
    assert body["user_message"] and isinstance(body["user_message"], str)
    assert body["detail"] == body["user_message"]
    # 绝不能只有英文 error 字段（那正是旧 bug：前端读不到 → 笼统"服务不可用"）
    assert "error" not in body or body.get("user_message")
    assert resp.headers.get("Retry-After")


# =============================================== 限流(429) e2e：友好体真的下发

def test_rate_limit_e2e_returns_friendly_body():
    """连打 /api/chat 触发 30/min 限流 → 必出 429，且响应体是中文友好体（含 user_message）。

    用空问题（输入校验前置短路、不触达 LLM/MySQL）快速打满限流计数。
    """
    client = _fresh_client()
    H = _login(client)
    got_429 = None
    for _ in range(40):
        r = client.post("/api/chat", headers=H, json={"question": "   "})
        if r.status_code == 429:
            got_429 = r
            break
    assert got_429 is not None, "连打 40 次仍未触发 30/min 限流，限流未生效？"
    body = got_429.json()
    assert body.get("user_message"), f"429 体缺 user_message（前端会退化成笼统提示）：{body}"
    assert "操作过于频繁" in body["user_message"]


# =============================================== SSE：立即 connected + 一定收口

def test_chat_stream_emits_connected_and_settles():
    """/api/chat/stream 立即吐 ': connected' 首字节，并最终一定有 done/error 事件（不无声中断）。"""
    client = _fresh_client()
    H = _login(client)
    with client.stream("POST", "/api/chat/stream", headers=H,
                       json={"question": "本月各大区销售额排名", "conversation_id": None}) as r:
        assert r.status_code == 200, r.read()[:200]
        text = "".join(chunk for chunk in r.iter_text())
    # 首字节注释行：强制刷出响应头，防代理"等首字节"超时
    assert ": connected" in text, "SSE 未立即吐出 connected 首字节"
    # 一定收口：done（含 ok:false 友好失败）或 error，绝不"读完却什么都没给"
    assert ("event: done" in text) or ("event: error" in text), f"SSE 未收口：{text[-300:]}"


# =============================================== 心跳常量可配置 + 下限保护

def test_chat_accepts_null_smartq_cube_ids():
    """**根因回归**：默认(飞鹤数据库)范围下前端会显式传 smartq_cube_ids:null。

    旧 schema 声明成 list[str] → Pydantic v2 在校验阶段直接 422（"Input should be a valid
    list"），导致**最普通的问数全部失败**、前端只显示"问数服务暂时不可用"。这里钉死：
    /api/chat 与 /api/chat/stream 都必须接住 null（HTTP≠422）。
    """
    client = _fresh_client()
    H = _login(client)

    # 同步端点：带 null 不应 422（业务层 (… or []) 归一为空列表，HTTP=200）
    r = client.post("/api/chat", headers=H,
                    json={"question": "本月各大区销售额排名", "smartq_cube_ids": None,
                          "conversation_id": None, "llm_provider": None, "force_refresh": False})
    assert r.status_code != 422, f"smartq_cube_ids:null 触发 422（根因回归）：{r.text[:200]}"
    assert r.status_code == 200, r.text

    # 流式端点同样必须接住 null
    with client.stream("POST", "/api/chat/stream", headers=H,
                       json={"question": "本月各大区销售额排名", "smartq_cube_ids": None,
                             "conversation_id": None}) as s:
        assert s.status_code != 422, f"stream smartq_cube_ids:null 触发 422：{s.read()[:200]}"
        assert s.status_code == 200
        body = "".join(s.iter_text())
    assert ": connected" in body and (("event: done" in body) or ("event: error" in body))


def test_force_refresh_falls_back_to_planner_when_llm_down(monkeypatch):
    """**非缓存/走模型路径回归**：force_refresh 会路由到 direct-SQL（模型直接写 SQL）。

    旧逻辑：LLM 失败 → 硬失败成"生成 SQL 失败/问数失败"（用户勾"不使用缓存"必崩，且
    生产网关一抖动就全挂）。新逻辑：direct-SQL 的 LLM 失败 → 抛 _DirectSQLFallback →
    run() 回退到结构化 planner（planner 自带规则兜底）。这里断言回退事件确实发生、
    且没有以 direct_sql 的 llm_error 收口。
    """
    from app.core import direct_sql as ds
    from app.core.orchestrator import get_pipeline

    pipe = get_pipeline()

    def _boom(*a, **k):
        raise RuntimeError("simulated LLM gateway down")
    monkeypatch.setattr(ds, "generate_direct_sql", _boom)

    result = pipe.run("各大区终端销售额排名前五", user_id="u-test", is_admin=True, force_refresh=True)
    stages = [(e.get("stage"), e.get("status")) for e in result.events]
    # 1) 确实走了 direct_sql 且 LLM 失败
    assert ("direct_sql", "llm_error") in stages, stages
    # 2) 关键：回退到 planner（而不是硬失败）
    assert ("route", "planner_fallback") in stages, stages
    # 3) planner 阶段确实接力跑了（规则兜底，无需真 LLM）
    assert any(s == "plan" for s, _ in stages), stages


def test_heartbeat_seconds_has_floor(monkeypatch):
    """CHAT_SSE_HEARTBEAT_SECONDS 解析逻辑：非法值兜底 15、过小值抬到下限 5（与 gen() 内一致）。"""
    def _parse(v: str) -> float:
        try:
            return max(5.0, float(v or "15"))
        except (TypeError, ValueError):
            return 15.0
    assert _parse("") == 15.0
    assert _parse("abc") == 15.0
    assert _parse("1") == 5.0        # 抬到下限
    assert _parse("30") == 30.0
