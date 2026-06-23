"""SSE 并发/取消回归（2026-06-22 审计 P0）。

审计发现：/api/chat/stream 把并发闸的 release 写在 gen() 的 finally，客户端一断开 gen() 立即
归还名额，但 run_in_executor 里的问数 worker 仍在跑 LLM/DB —— 大量断开可绕过在途上限、
放任后台烧算力。

修复要求与本测试断言：
  1) 名额释放绑定到 **worker 真实终态**：断开后 worker 仍在跑时名额不归还；worker 结束才归还。
  2) 断开会通过取消信号让 pipeline 在阶段边界停下，不再继续昂贵的 LLM/DB（orchestrator 级单测）。

全部离线：mock 掉 worker / 不触达真实 LLM / MySQL。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth_ssecancel.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_ssecancel.db"
os.environ["JWT_SECRET"] = "test-secret-ssecancel"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_conv_ssecancel.db",):
    Path(_p).unlink(missing_ok=True)

from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None


def _client():
    from fastapi.testclient import TestClient
    from app.main import create_app
    return TestClient(create_app())


def _login(client) -> dict:
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ===================================== P0: 名额释放绑定 worker 终态（核心回归）

def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_guard_released_on_worker_terminal_state_not_on_disconnect(monkeypatch):
    """断开后 worker 仍在跑 → 名额**不**提前归还；worker 结束才归还（杜绝绕过在途上限）。

    用**真 uvicorn 进程内服务 + 真 socket**：只有这样客户端断开才是真的断开
    （TestClient / httpx.ASGITransport 都会缓冲整段 SSE，观察不到中途断开状态）。
    """
    import time as _t

    import httpx
    import uvicorn
    import app.main as m
    from app.core.concurrency import get_chat_guard

    guard = get_chat_guard()
    started = threading.Event()
    may_finish = threading.Event()
    cancel_seen = {"v": False}

    def fake_do_chat(*a, **k):
        started.set()
        # 模拟仍在跑的 LLM/DB：阻塞直到放行。同时观察 cancel_event 是否在断开后被置位。
        ce = k.get("cancel_event")
        may_finish.wait(timeout=20)
        if ce is not None and ce.is_set():
            cancel_seen["v"] = True
        return {"ok": True, "trace_id": "t", "conversation_id": "c", "question": "x",
                "answer": {"narrative": "done"}, "plan": {}, "sql": "", "rows": 0,
                "cached": False, "elapsed_ms": 1}

    monkeypatch.setattr(m, "_do_chat", fake_do_chat)

    app = m.create_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        # 等服务起来
        for _ in range(100):
            if server.started:
                break
            _t.sleep(0.05)
        assert server.started, "uvicorn 未启动"

        base_url = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base_url, timeout=10.0) as c:
            tok = c.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"}).json()["token"]
            H = {"Authorization": f"Bearer {tok}"}
            base = guard.in_flight

            # 真流式：开流、读到 ": connected" 即返回，worker 在后台阻塞运行
            with c.stream("POST", "/api/chat/stream", headers=H,
                          json={"question": "本月各大区销售额", "conversation_id": None}) as resp:
                assert resp.status_code == 200
                for line in resp.iter_lines():
                    if "connected" in line:
                        break
                assert started.wait(5.0), "worker 未启动"
                assert guard.in_flight == base + 1, "worker 运行中应占一个在途名额"
            # 退出 with = 真断开。worker 仍阻塞在 may_finish 上：
            _t.sleep(0.5)
            assert guard.in_flight == base + 1, "断开后 worker 未结束，名额绝不能提前归还（P0 回归核心）"

            may_finish.set()  # 放行 worker → 跑完 → finally 归还名额
            deadline = _t.time() + 5
            while guard.in_flight > base and _t.time() < deadline:
                _t.sleep(0.05)
            assert guard.in_flight == base, "worker 结束后名额应已归还"
            # 断开应已把取消信号透传给 worker（worker 醒来时看得到 cancel_event 置位）
            assert cancel_seen["v"], "客户端断开应通过 cancel_event 透传取消信号给 worker"
    finally:
        may_finish.set()
        server.should_exit = True
        th.join(timeout=5)


def test_guard_no_leak_on_normal_completion(monkeypatch):
    """正常跑完（不断开）也只释放一次，名额回到基线（防双重释放/泄漏）。"""
    import app.main as m
    from app.core.concurrency import get_chat_guard

    guard = get_chat_guard()

    def fake_do_chat(*a, **k):
        return {"ok": True, "trace_id": "t", "conversation_id": "c", "question": "x",
                "answer": {"narrative": "ok"}, "plan": {}, "sql": "", "rows": 0,
                "cached": False, "elapsed_ms": 1}

    monkeypatch.setattr(m, "_do_chat", fake_do_chat)
    client = _client()
    H = _login(client)
    base = guard.in_flight
    with client.stream("POST", "/api/chat/stream", headers=H,
                       json={"question": "本月各大区销售额", "conversation_id": None}) as r:
        body = "".join(r.iter_text())
    assert ("event: done" in body) or ("event: error" in body)
    deadline = time.time() + 5
    while guard.in_flight > base and time.time() < deadline:
        time.sleep(0.05)
    assert guard.in_flight == base


# ===================================== P0: pipeline 阶段边界响应取消信号（orchestrator 级）

def test_pipeline_aborts_before_execute_when_cancelled():
    """cancel_event 置位后，run() 在阶段边界抛 PipelineCancelled，绝不进入 DB 执行。"""
    from app.core.orchestrator import get_pipeline, PipelineCancelled

    pipe = get_pipeline()
    pipe.warmup()

    called = {"execute": False}
    orig = pipe.executor.run_select

    def spy(*a, **k):
        called["execute"] = True
        return orig(*a, **k)
    pipe.executor.run_select = spy

    cancel = threading.Event()
    cancel.set()  # 进入 run 前就已取消（模拟客户端早断开）

    with pytest.raises(PipelineCancelled):
        pipe.run("2025年1月各大区销售额排名", user_id="u", is_admin=True, cancel_event=cancel)
    assert called["execute"] is False, "取消后绝不应触达数据库执行"


def test_pipeline_runs_normally_without_cancel():
    """不传 cancel_event（同步端点路径）时行为不变：能正常产出 plan/SQL（规则兜底，无需真 LLM）。"""
    from app.core.orchestrator import get_pipeline

    pipe = get_pipeline()
    pipe.warmup()

    def boom(*a, **k):
        raise RuntimeError("force rule-only")
    pipe.planner.llm.chat_json = boom

    # 不触达真实 DB：不强求具体阶段（可能命中缓存），核心断言是**没有被取消**、能正常出结果。
    result = pipe.run("各大区销售额排名 不取消用例", user_id="u", is_admin=True)
    stages = [e.get("stage") for e in result.events]
    assert "cancelled" not in stages
    assert result.trace_id
