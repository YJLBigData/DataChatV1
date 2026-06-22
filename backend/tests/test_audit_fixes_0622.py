"""Regression tests for the 2026-06-22 audit fixes.

覆盖项（与审计报告一一对应）：
  · [P0] SmartQ 授权 fail-closed：拿不到授权列表 / 列表为空 / cube 未授权 一律拒绝，绝不放行。
  · [P1] SmartQ dataset_status 接入真实接口（不再永远 enabled=True）。
  · [P1] SmartQ / 空问题失败不创建空会话。
  · [P2] 导出队列事务级容量闸（每用户 / 全局）。
  · [P2] 已删除（取消）的排队导出任务开跑前直接退出，不取数不写文件。
  · [P2] stream_select 分类连接池获取超时（计数 + 可读错误）。

全部离线：不触达真实 quickbi / MySQL / LLM。
"""
from __future__ import annotations

import os
import sys
import time
import types
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth_audit0622.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_audit0622.db"
os.environ["DATACHAT_EXPORT_DB"] = "/tmp/datachat_test_exports_audit0622.db"
os.environ["DATACHAT_EXPORT_DIR"] = tempfile.mkdtemp(prefix="dc_exp_audit0622_")
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_conv_audit0622.db", "/tmp/datachat_test_exports_audit0622.db"):
    Path(_p).unlink(missing_ok=True)

from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None
from app.core import conversation as _conv_mod  # noqa: E402
_conv_mod._default_store = None

from app.integrations.smartq import api as smartq_api  # noqa: E402
from app.integrations.smartq.client import (  # noqa: E402
    DATASET_STATUS_PATH,
    SmartQClient,
    SmartQError,
)
from app.integrations.smartq.config import load_smartq_config  # noqa: E402


def _ready_smartq(monkeypatch) -> None:
    """让 SmartQ cfg.ready=True（仍不联网，因为我们替换掉 client 的方法）。"""
    monkeypatch.setenv("SMARTQ_ENABLED", "1")
    monkeypatch.setenv("SMARTQ_API_KEY", "k")
    monkeypatch.setenv("SMARTQ_API_SECRET", "s")
    monkeypatch.setenv("SMARTQ_SERVER_DOMAIN", "https://example.invalid")
    monkeypatch.delenv("SMARTQ_DEFAULT_USER_ID", raising=False)


def _fake_user():
    return types.SimpleNamespace(id="u1", email="tester@feihe.com", username="tester")


# =============================================== [P0] SmartQ 授权 fail-closed

def test_smartq_fail_closed_when_dataset_list_errors(monkeypatch):
    """授权列表接口报错 → 拒绝查询，且**绝不**进入真正的 query（不放行未验证 cube）。"""
    _ready_smartq(monkeypatch)
    called = {"query": False}

    def boom(self, *, user_id=None):
        raise SmartQError("授权列表暂不可用")

    def guard_query(self, **kwargs):
        called["query"] = True
        return {"success": True, "answer": {}}

    monkeypatch.setattr(SmartQClient, "get_dataset_list", boom)
    monkeypatch.setattr(SmartQClient, "query_multi_datasets", guard_query)

    out = smartq_api.execute_smartq_query(
        user=_fake_user(), question="本月各大区销售额", cube_ids=["any-cube"], persist=False,
    )
    assert out["ok"] is False
    assert called["query"] is False, "fail-closed 被破坏：授权未知时仍发起了查询"


def test_smartq_fail_closed_when_no_authorized_datasets(monkeypatch):
    """授权列表为空（无任何可用数据集）→ 拒绝，不查询。"""
    _ready_smartq(monkeypatch)
    called = {"query": False}
    monkeypatch.setattr(SmartQClient, "get_dataset_list", lambda self, *, user_id=None: [])
    monkeypatch.setattr(SmartQClient, "query_multi_datasets",
                        lambda self, **k: called.__setitem__("query", True))

    out = smartq_api.execute_smartq_query(
        user=_fake_user(), question="x", cube_ids=["c1"], persist=False,
    )
    assert out["ok"] is False and called["query"] is False


def test_smartq_fail_closed_when_cube_not_authorized(monkeypatch):
    """请求的 cube 不在授权集合内 → 拒绝，不查询（杜绝越权 cube）。"""
    _ready_smartq(monkeypatch)
    called = {"query": False}
    monkeypatch.setattr(SmartQClient, "get_dataset_list",
                        lambda self, *, user_id=None: [{"cube_id": "allowed", "name": "A"}])
    monkeypatch.setattr(SmartQClient, "query_multi_datasets",
                        lambda self, **k: called.__setitem__("query", True))

    out = smartq_api.execute_smartq_query(
        user=_fake_user(), question="x", cube_ids=["not-authorized-cube"], persist=False,
    )
    assert out["ok"] is False and "无权" in out["error"] and called["query"] is False


def test_smartq_proceeds_when_cube_authorized(monkeypatch):
    """cube 在授权集合内 → 正常进入查询并成功。"""
    _ready_smartq(monkeypatch)
    monkeypatch.setattr(SmartQClient, "get_dataset_list",
                        lambda self, *, user_id=None: [{"cube_id": "c1", "name": "数据集1"}])

    def ok_query(self, *, question, cube_ids, user_id=None, cube_names=None):
        return {
            "success": True,
            "answer": {
                "narrative": "结论", "table": {"row_count": 1, "display_rows": [["x"]]},
                "explainability": {"sql": "SELECT 1"},
            },
            "mode": "single_dataset", "results": [], "summary": "ok",
        }

    monkeypatch.setattr(SmartQClient, "query_multi_datasets", ok_query)

    out = smartq_api.execute_smartq_query(
        user=_fake_user(), question="x", cube_ids=["c1"], persist=False,
    )
    assert out["ok"] is True and out["rows"] == 1


# =============================================== [P1] dataset_status 真实接口

def test_dataset_status_calls_real_endpoint_true(monkeypatch):
    _ready_smartq(monkeypatch)
    seen: dict = {}

    def fake_request(self, method, path, *, params=None, json_body=None):
        seen["path"] = path
        seen["params"] = params
        return {"Result": True}

    monkeypatch.setattr(SmartQClient, "_request", fake_request)
    out = SmartQClient(load_smartq_config()).dataset_status(cube_id="cube-1", user_id="u9")
    assert seen["path"] == DATASET_STATUS_PATH
    assert seen["params"]["cubeId"] == "cube-1" and seen["params"]["userId"] == "u9"
    assert out["enabled"] is True and out["unchecked"] is False


def test_dataset_status_reports_disabled(monkeypatch):
    """状态接口返回 false → enabled=False（不再永远 enabled=True）。"""
    _ready_smartq(monkeypatch)
    monkeypatch.setattr(SmartQClient, "_request",
                        lambda self, m, p, *, params=None, json_body=None: {"result": False})
    out = SmartQClient(load_smartq_config()).dataset_status(cube_id="c")
    assert out["enabled"] is False and out["unchecked"] is False


# =============================================== [P1] 失败不建空会话（API 级）

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def H(client):
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _conv_count(client, headers) -> int:
    return len(client.get("/api/conversations", headers=headers).json()["items"])


def test_empty_question_creates_no_conversation(client, H):
    before = _conv_count(client, H)
    r = client.post("/api/chat", headers=H, json={"question": "   "})
    assert r.status_code == 200 and r.json().get("ok") is False
    assert _conv_count(client, H) == before, "空问题失败却创建了空会话"


def test_smartq_failure_creates_no_conversation(client, H, monkeypatch):
    """SmartQ 未就绪时带 cube 问数 → 失败，且不留下无消息空会话（审计 P1 复现）。"""
    monkeypatch.setenv("SMARTQ_ENABLED", "0")  # 强制未就绪：execute_smartq_query 直接短路，不联网
    before = _conv_count(client, H)
    r = client.post("/api/chat", headers=H,
                    json={"question": "本月各大区销售额", "smartq_cube_ids": ["unauth-cube"]})
    assert r.status_code == 200 and r.json().get("ok") is False
    assert _conv_count(client, H) == before, "SmartQ 失败却创建了空会话"


# =============================================== [P2] 导出事务级容量闸

def test_export_capacity_is_transactional(tmp_path):
    from app.exports.store import ExportStore, ExportCapacityError
    store = ExportStore(tmp_path / "exp_cap.db")
    common = dict(conversation_id="c", trace_id="t", question="q", filename="f.xlsx",
                  expires_at=time.time() + 3600)

    store.create_if_capacity(user_id="u1", per_user_max=2, queue_max=100, **common)
    store.create_if_capacity(user_id="u1", per_user_max=2, queue_max=100, **common)
    # 同一用户第三个 → 每用户上限拒绝
    with pytest.raises(ExportCapacityError):
        store.create_if_capacity(user_id="u1", per_user_max=2, queue_max=100, **common)
    # 另一用户仍可（每用户上限是分用户的）
    store.create_if_capacity(user_id="u2", per_user_max=2, queue_max=100, **common)
    # 此刻全局在途=3；全局上限=3 → 拒绝（即便该用户没满）
    with pytest.raises(ExportCapacityError):
        store.create_if_capacity(user_id="u3", per_user_max=10, queue_max=3, **common)
    assert store.count_active() == 3


# =============================================== [P2] 取消的排队任务不执行

def test_export_run_aborts_when_cancelled_before_start(tmp_path, monkeypatch):
    from app.exports import service as svc
    from app.exports.store import ExportStore

    store = ExportStore(tmp_path / "exp_run.db")
    outdir = tmp_path / "out"
    monkeypatch.setattr(svc, "get_export_store", lambda: store)
    monkeypatch.setattr(svc, "_out_dir", lambda: outdir)

    service = svc.ExportService()
    job = store.create(user_id="u1", conversation_id="c", trace_id="t", question="q",
                       filename="f.xlsx", expires_at=time.time() + 3600)
    # 取消 = 删除 job 记录（在 worker 真正开跑前）
    store.delete(job.id)

    trusted = {"answer": {"table": {"display_columns": [{"label": "a"}], "display_rows": [["1"]]}}, "sql": ""}
    service._run(job.id, trusted)  # 不应取数 / 不应写文件 / 不应复活成 ready

    assert store.get(job.id) is None
    assert not (outdir / f"{job.id}.xlsx").exists()


# =============================================== [P2] 流式查询分类连接池超时

def test_stream_select_classifies_pool_timeout(monkeypatch):
    from sqlalchemy.exc import TimeoutError as SAPoolTimeout
    from app.core.config import load_config
    from app.core.exec.mysql_exec import ExecError, MySQLExecutor

    counter = {"n": 0}
    monkeypatch.setattr("app.core.concurrency.note_db_pool_timeout",
                        lambda: counter.__setitem__("n", counter["n"] + 1))

    ex = MySQLExecutor.__new__(MySQLExecutor)  # 跳过 __init__：不连真实库
    ex.cfg = load_config()

    class _FakeEngine:
        def connect(self):
            raise SAPoolTimeout("pool exhausted")

    ex.engine = _FakeEngine()

    gen = ex.stream_select("SELECT 1", max_rows=10, timeout_ms=1000)
    with pytest.raises(ExecError) as ei:
        next(gen)
    assert "连接池繁忙" in str(ei.value)
    assert counter["n"] == 1, "连接池超时未被计数（分类丢失）"
