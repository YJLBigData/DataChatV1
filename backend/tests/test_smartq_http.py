"""HTTP-level SmartQ tests — false-success defenses + cube authorization + trace
persistence. Offline: the SmartQ transport is monkeypatched (never touches网络).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_smartq.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
# 让 SmartQ 配置就绪（transport 被 monkeypatch，不会真的联网）
os.environ["SMARTQ_ENABLED"] = "1"
os.environ["SMARTQ_API_KEY"] = "k"
os.environ["SMARTQ_API_SECRET"] = "s"
os.environ["SMARTQ_SERVER_DOMAIN"] = "https://example.invalid"
Path("/tmp/datachat_test_conv_smartq.db").unlink(missing_ok=True)

from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None
from app.core import conversation as _conv_mod  # noqa: E402
_conv_mod._default_store = None


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers(client):
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _patch_client(monkeypatch, *, datasets, query_result):
    """把 api 模块里用到的 SmartQClient 换成假实现。"""
    import app.integrations.smartq.api as api_mod

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def list_datasets(self, *, user_id):
            return datasets

        def query(self, **kwargs):
            return query_result

    monkeypatch.setattr(api_mod, "SmartQClient", _Fake)


def test_smartq_empty_result_is_not_success(client, auth_headers, monkeypatch):
    """空壳响应（无行/无结论/无 SQL）必须 ok:false，绝不展示成"已查询成功"。"""
    _patch_client(monkeypatch, datasets=[{"cube_id": "cube1", "name": "销售", "theme": ""}],
                  query_result={})
    r = client.post("/api/smartq/query", headers=auth_headers,
                    json={"question": "本月销售额", "cube_id": "cube1"}).json()
    assert r["ok"] is False
    assert "answer" not in r or not r.get("answer")


def test_smartq_unauthorized_cube_rejected(client, auth_headers, monkeypatch):
    """越权 cube（不在授权清单）直接业务拒绝，不发起查询。"""
    _patch_client(monkeypatch, datasets=[{"cube_id": "cube1", "name": "销售", "theme": ""}],
                  query_result={"ConclusionText": "不该看到"})
    r = client.post("/api/smartq/query", headers=auth_headers,
                    json={"question": "x", "cube_id": "not-authorized-cube"}).json()
    assert r["ok"] is False and "无权" in r["error"]


def test_smartq_substantive_persists_with_trace(client, auth_headers, monkeypatch):
    """有实质结果 → ok:true + 落库到问数会话，返回可信 (conversation_id, trace_id)，
    且该 trace 可被导出链路取回（与普通问数共享）。"""
    _patch_client(
        monkeypatch,
        datasets=[{"cube_id": "cube1", "name": "销售", "theme": ""}],
        query_result={
            "ConclusionText": "华东第一",
            "LogicSql": "SELECT region, sales FROM t",
            "Columns": [{"Name": "region", "Label": "大区"}, {"Name": "sales", "Label": "销售额"}],
            "Values": [{"region": "华东", "sales": 120}, {"region": "华北", "sales": 90}],
        },
    )
    r = client.post("/api/smartq/query", headers=auth_headers,
                    json={"question": "各大区销售额", "cube_id": "cube1"}).json()
    assert r["ok"] is True
    cid, trace = r["conversation_id"], r["trace_id"]
    assert cid and trace
    assert r["rows"] == 2

    # 落库可信：导出链路按 (cid, trace) 能取回结果（不再 trace_id 为空无法导出/报告）
    from app.core.conversation import get_conversation_store
    from app.main import _trusted_result_for_trace
    from app.core.auth import get_auth_store
    user = get_auth_store().get_by_username("admin")
    trusted = _trusted_result_for_trace(get_conversation_store(), user, cid, trace)
    assert trusted["question"] == "各大区销售额"
    assert trusted["answer"]["table"]["row_count"] == 2
