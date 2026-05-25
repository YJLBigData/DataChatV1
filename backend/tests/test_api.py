"""HTTP-level integration tests for DataChat (uses fastapi TestClient).

LLM-touching endpoints are guarded with `e2e` marker.
Auth-required endpoints log in as admin first.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Use an isolated auth.db so tests don't conflict with the running server
os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
Path("/tmp/datachat_test_auth.db").unlink(missing_ok=True)
# Reset any auth singleton possibly created by previous tests so our env wins
from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers(client):
    """Login as admin and return Authorization header dict."""
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_root_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_health_shape(client):
    """P2-6：公开 /api/health 只返回最小健康状态，不得泄露 DB host/库名、
    Redis URL、LLM provider/model 等诊断信息。"""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "DataChat"
    assert isinstance(body.get("db"), dict) and set(body["db"].keys()) == {"ok"}
    assert isinstance(body.get("cache"), dict) and set(body["cache"].keys()) == {"ok"}
    # 敏感字段绝不出现在公开接口
    assert "semantic" not in body
    assert "llm" not in body
    assert "host" not in body["db"] and "database" not in body["db"]
    assert "redis_url" not in body["cache"]
    blob = str(body).lower()
    for leak in ("aliyuncs", "redis://", "provider", "qwen"):
        assert leak not in blob


def test_admin_diagnostics_requires_admin(client, auth_headers):
    """P2-6：详细诊断仅管理员可见；未登录不可访问。"""
    assert client.get("/api/admin/diagnostics").status_code in (401, 403)
    r = client.get("/api/admin/diagnostics", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "semantic" in body and body["semantic"]["metrics"] >= 19
    assert body["llm"]["model"]


def test_bootstrap_no_auth(client):
    r = client.get("/api/bootstrap")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "DataChat"
    assert isinstance(body["suggestions"], list) and len(body["suggestions"]) > 0
    assert body["data_range"][0] and body["data_range"][1]


def test_suggestions_no_auth(client):
    r = client.get("/api/suggestions")
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


def test_semantic_overview_requires_auth(client, auth_headers):
    # without auth → 401
    r0 = client.get("/api/semantic/overview")
    assert r0.status_code == 401
    # with auth → 200
    r = client.get("/api/semantic/overview", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["metrics"]) >= 19
    assert len(body["dimensions"]) >= 17
    assert len(body["tables"]) >= 5  # 含 detail/summary/target/member/potential/hs_sale 等事实表


def test_login_wrong_password(client):
    r = client.post("/api/login", json={"username": "admin", "password": "WRONG"})
    assert r.status_code == 401


def test_me_requires_auth(client, auth_headers):
    r0 = client.get("/api/me")
    assert r0.status_code == 401
    r = client.get("/api/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert r.json()["role"] == "admin"


def test_admin_can_create_and_delete_user(client, auth_headers):
    uname = f"test_user_{uuid.uuid4().hex[:6]}"
    # 1) 显式弱密码 → 拒绝
    bad = client.post("/api/admin/users", headers=auth_headers, json={"username": uname, "password": "abc123", "role": "user"})
    assert bad.status_code == 400
    # 2) 显式强密码 + 邮箱 → 成功
    r = client.post("/api/admin/users", headers=auth_headers,
                    json={"username": uname, "password": "Strong@2026", "role": "user", "email": "u@feihe.com"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "u@feihe.com"
    # 3) 不传密码 → 后端随机生成强密码返回
    uname2 = f"test_user_{uuid.uuid4().hex[:6]}"
    r2 = client.post("/api/admin/users", headers=auth_headers,
                     json={"username": uname2, "role": "user"})
    assert r2.status_code == 200
    assert r2.json().get("one_time_password")
    assert r2.json()["must_change_password"] is True
    # list should include both
    r3 = client.get("/api/admin/users", headers=auth_headers)
    names = [u["username"] for u in r3.json()["items"]]
    assert uname in names and uname2 in names
    # delete both
    assert client.delete(f"/api/admin/users/{uname}",  headers=auth_headers).status_code == 200
    assert client.delete(f"/api/admin/users/{uname2}", headers=auth_headers).status_code == 200


def test_password_strength_enforced(client, auth_headers):
    """改密接口必须拒绝弱密码。"""
    # admin first changes own password to a known value
    # ... actually test using a freshly created user
    uname = f"test_pw_{uuid.uuid4().hex[:6]}"
    create = client.post("/api/admin/users", headers=auth_headers, json={"username": uname, "role": "user"})
    pwd0 = create.json()["one_time_password"]
    login = client.post("/api/login", json={"username": uname, "password": pwd0})
    assert login.status_code == 200
    tok = login.json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}
    # 弱密码改密 → 400
    bad = client.post("/api/me/password", headers=headers, json={"old_password": pwd0, "new_password": "12345678"})
    assert bad.status_code == 400
    # 强密码 → 200
    ok = client.post("/api/me/password", headers=headers, json={"old_password": pwd0, "new_password": "Strong@2026"})
    assert ok.status_code == 200
    # cleanup
    client.delete(f"/api/admin/users/{uname}", headers=auth_headers)


def test_report_template_user_isolation(client, auth_headers):
    """普通用户不能改/删别人的模板，只能改自己的；admin 都能改。"""
    from app.core.auth import generate_initial_password
    # 1) 创建 alice
    uname = f"iso_{uuid.uuid4().hex[:6]}"
    create = client.post("/api/admin/users", headers=auth_headers, json={"username": uname, "role": "user"})
    pwd0 = create.json()["one_time_password"]
    tok = client.post("/api/login", json={"username": uname, "password": pwd0}).json()["token"]
    # 一次性密码用户必须先改密（后端强制：未改密不能访问核心接口）
    client.post("/api/me/password", headers={"Authorization": f"Bearer {tok}"},
                json={"old_password": pwd0, "new_password": "Strong@2026"})
    tok = client.post("/api/login", json={"username": uname, "password": "Strong@2026"}).json()["token"]
    alice = {"Authorization": f"Bearer {tok}"}
    # 2) alice 看到的模板：至少有系统默认
    listing = client.get("/api/report/templates", headers=alice).json()["items"]
    assert any(t["is_system"] for t in listing)
    # 3) alice 创建自己的模板
    r = client.post("/api/report/templates", headers=alice,
                    json={"name": "alice_tpl", "prompt": "alice's prompt"})
    assert r.status_code == 200
    tid = r.json()["id"]
    # 4) admin 也能看到 alice 的模板
    admin_listing = client.get("/api/report/templates", headers=auth_headers).json()["items"]
    assert any(t["id"] == tid for t in admin_listing)
    # 5) 第二个用户 bob 看不到 alice 的模板（用户隔离）
    bob_name = f"iso_b_{uuid.uuid4().hex[:6]}"
    bob_create = client.post("/api/admin/users", headers=auth_headers, json={"username": bob_name, "role": "user"})
    bob_pwd = bob_create.json()["one_time_password"]
    bob_tok = client.post("/api/login", json={"username": bob_name, "password": bob_pwd}).json()["token"]
    client.post("/api/me/password", headers={"Authorization": f"Bearer {bob_tok}"},
                json={"old_password": bob_pwd, "new_password": "Strong@2026"})
    bob_tok = client.post("/api/login", json={"username": bob_name, "password": "Strong@2026"}).json()["token"]
    bob = {"Authorization": f"Bearer {bob_tok}"}
    bob_listing = client.get("/api/report/templates", headers=bob).json()["items"]
    assert not any(t["id"] == tid for t in bob_listing), "bob 不应看到 alice 的私有模板"
    # 6) bob 改 alice 的模板 → 403
    forbidden = client.patch(f"/api/report/templates/{tid}", headers=bob, json={"name": "hacked"})
    assert forbidden.status_code == 403
    # 7) admin 能改任何模板
    ok = client.patch(f"/api/report/templates/{tid}", headers=auth_headers, json={"name": "admin_renamed"})
    assert ok.status_code == 200
    # cleanup
    client.delete(f"/api/report/templates/{tid}", headers=auth_headers)
    client.delete(f"/api/admin/users/{uname}", headers=auth_headers)
    client.delete(f"/api/admin/users/{bob_name}", headers=auth_headers)


def test_long_question_does_not_500(client, auth_headers):
    """审计 P0 #10 回归：长问题不再 500，返回 ok=true（direct-sql）或 ok=false（friendly）。"""
    long_q = "请直接返回可执行 MySQL 5.6 SQL，统计本月各大区销售额。不要 CTE，不要窗口函数。"
    r = client.post("/api/chat", headers=auth_headers, json={"question": long_q, "skip_llm_narrative": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ok" in body
    # 不暴露内部异常
    if body.get("ok") is False:
        assert "user_message" in body
        assert "trace_id" in body
        # user_message 不应该包含 Python traceback / 'str' object 等内部字眼
        msg = body["user_message"]
        assert "Traceback" not in msg
        assert "object has no attribute" not in msg


def test_cannot_delete_default_admin(client, auth_headers):
    r = client.delete("/api/admin/users/admin", headers=auth_headers)
    assert r.status_code == 400


def test_conversation_lifecycle(client, auth_headers):
    # create
    r = client.post("/api/conversations", headers=auth_headers, json={"title": "测试会话"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    # list
    r2 = client.get("/api/conversations", headers=auth_headers)
    ids = [c["id"] for c in r2.json()["items"]]
    assert cid in ids
    # rename
    r3 = client.patch(f"/api/conversations/{cid}", headers=auth_headers, json={"title": "改名后"})
    assert r3.status_code == 200
    # get
    r4 = client.get(f"/api/conversations/{cid}", headers=auth_headers)
    assert r4.json()["title"] == "改名后"
    # delete
    r5 = client.delete(f"/api/conversations/{cid}", headers=auth_headers)
    assert r5.status_code == 200


def test_chat_requires_auth(client):
    r = client.post("/api/chat", json={"question": "test"})
    assert r.status_code == 401


def test_feishu_push_failure_returns_ok_false(client, auth_headers, monkeypatch):
    """P1-2：飞书推送失败时后端返回 HTTP 200 + ok:false + user_message，
    不回传底层异常文本（前端据此不得显示“已推送”）。"""
    import app.main as main_mod

    def boom(*a, **k):
        raise main_mod.FeishuError("internal webhook 10.0.0.1 connection refused")

    monkeypatch.setattr(main_mod, "feishu_push", boom)
    r = client.post("/api/feishu/push", headers=auth_headers,
                     json={"title": "t", "narrative": "n", "highlights": [], "rows_preview": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "FEISHU_PUSH_FAILED"
    assert body["user_message"] and "connection refused" not in body["user_message"]
    assert "10.0.0.1" not in str(body)


def test_chat_pipeline_failure_returns_ok_false(client, auth_headers, monkeypatch):
    """P1-4：SQL 编译/Guard/权限/执行失败时 /api/chat 必须 ok:false，
    不得把失败 narrative 当正常答案展示，且 user_message 不含内部异常文本。"""
    from app.core.orchestrator import Pipeline, PipelineResult

    def fake_run(self, question, **kwargs):
        return PipelineResult(
            trace_id="deadbeef", question=question,
            answer={"narrative": "SQL 编译失败：boom internal detail"},
            plan={}, sql="", rows=0, elapsed_ms=1, cached=False, events=[],
            ok=False, error_code="CHAT_FAILED",
        )

    monkeypatch.setattr(Pipeline, "run", fake_run)
    r = client.post("/api/chat", headers=auth_headers,
                     json={"question": "本月各大区销售额排名", "skip_llm_narrative": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "CHAT_FAILED"
    assert body.get("trace_id")
    blob = str(body)
    assert "SQL 编译失败" not in blob and "boom internal detail" not in blob
    assert "answer" not in body  # 失败不返回 answer，不被前端当正常结果渲染


@pytest.mark.e2e
def test_chat_full(client, auth_headers):
    """Full chat — requires LLM + DB."""
    if not os.environ.get("DASHSCOPE_API_KEY"):
        pytest.skip("no API key")
    r = client.post(
        "/api/chat",
        headers=auth_headers,
        json={"question": "本月各大区销售额排名", "skip_llm_narrative": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["plan"]["metric"] == "terminal_sale_amount_total"
    assert "region" in (body["plan"].get("group_by") or [])
    assert body["rows"] >= 6
