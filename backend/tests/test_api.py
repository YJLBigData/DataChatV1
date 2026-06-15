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
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
Path("/tmp/datachat_test_auth.db").unlink(missing_ok=True)
Path("/tmp/datachat_test_conv.db").unlink(missing_ok=True)
# Reset any auth singleton possibly created by previous tests so our env wins
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
    """Login as admin and return Authorization header dict."""
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_trusted_answer(client, headers, *, narrative="可信结论", highlights=None,
                         rows=None, trace_id="tracetest123", question="本月各大区销售额"):
    """创建会话并塞入一条带 trace_id 的 assistant 消息，返回 (conversation_id, trace_id)。

    用于验证报告 / 飞书一律以服务端落地结果为准，不信任前端 payload（P0）。
    """
    me = client.get("/api/me", headers=headers).json()
    from app.core.conversation import get_conversation_store
    store = get_conversation_store()
    sess = store.create_session(me["id"], title="t")
    store.append_message(sess.id, "user", question, payload={})
    store.append_message(sess.id, "assistant", narrative, payload={
        "answer": {
            "narrative": narrative,
            "highlights": highlights if highlights is not None else ["要点A"],
            "table": {"display_rows": rows if rows is not None else [["华东", "100"]]},
        },
        "plan": {"metric": "sales"}, "sql": "SELECT 1", "trace_id": trace_id,
    })
    return sess.id, trace_id


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


def test_metrics_access_control_logic():
    """P1：/metrics 本地放开；生产仅 localhost/内网/带 METRICS_TOKEN。"""
    from app.main import _metrics_access_allowed as allow, _ip_is_local_or_private as priv
    # 本地放开
    assert allow(is_local=True, client_ip="8.8.8.8", auth_header="", token="")
    # 生产：公网 IP 拒绝
    assert not allow(is_local=False, client_ip="8.8.8.8", auth_header="", token="t")
    # 生产：localhost / 内网放行
    assert allow(is_local=False, client_ip="127.0.0.1", auth_header="", token="")
    assert allow(is_local=False, client_ip="10.1.2.3", auth_header="", token="")
    # 生产：带正确 token 放行；错误 token 拒绝
    assert allow(is_local=False, client_ip="8.8.8.8", auth_header="Bearer s3cret", token="s3cret")
    assert not allow(is_local=False, client_ip="8.8.8.8", auth_header="Bearer wrong", token="s3cret")
    assert priv("192.168.0.5") and priv("::1")
    assert not priv("8.8.8.8") and not priv("testclient")


def test_feishu_push_failure_returns_ok_false(client, auth_headers, monkeypatch):
    """P1-2：飞书推送失败时后端返回 HTTP 200 + ok:false + user_message，
    不回传底层异常文本（前端据此不得显示“已推送”）。"""
    import app.main as main_mod

    def boom(*a, **k):
        raise main_mod.FeishuError("internal webhook 10.0.0.1 connection refused")

    monkeypatch.setattr(main_mod, "feishu_push", boom)
    cid, tid = _seed_trusted_answer(client, auth_headers)
    r = client.post("/api/feishu/push", headers=auth_headers,
                     json={"conversation_id": cid, "trace_id": tid, "user_email": "ops@feihe.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "FEISHU_PUSH_FAILED"
    assert body["user_message"] and "connection refused" not in body["user_message"]
    assert "10.0.0.1" not in str(body)


def test_feishu_push_uses_server_side_trusted_content(client, auth_headers, monkeypatch):
    """P0：推送内容必须来自服务端会话存储（按 trace 取），不信任前端伪造的
    narrative/highlights/rows_preview，并返回内容指纹 content_sha256。"""
    import app.main as main_mod
    captured: dict = {}

    def capture(title, narrative, highlights, rows_preview, **k):
        captured.update(title=title, narrative=narrative, highlights=highlights, rows_preview=rows_preview)
        return {"ok": True}

    monkeypatch.setattr(main_mod, "feishu_push", capture)
    cid, tid = _seed_trusted_answer(
        client, auth_headers, narrative="真实结论", highlights=["真要点"], rows=[["华东", "999"]],
    )
    r = client.post("/api/feishu/push", headers=auth_headers,
                     json={"conversation_id": cid, "trace_id": tid, "user_email": "ops@feihe.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body.get("content_sha256")
    # 内容取自服务端可信结果（哪怕前端不传也照样填充）
    assert captured["narrative"] == "真实结论"
    assert captured["highlights"] == ["真要点"]
    assert captured["rows_preview"] == ["华东 | 999"]


def test_feishu_push_rejects_unknown_trace(client, auth_headers):
    """P0：trace_id 不存在 / 不属于该用户 → 404，无法伪造推送。"""
    cid, _ = _seed_trusted_answer(client, auth_headers)
    r = client.post("/api/feishu/push", headers=auth_headers,
                     json={"conversation_id": cid, "trace_id": "doesnotexist", "user_email": "ops@feihe.com"})
    assert r.status_code == 404, r.text


def test_report_uses_server_side_trusted_content(client, auth_headers, monkeypatch, tmp_path):
    """P0：报告 question/answer/plan/sql 必须来自服务端会话存储，不信任前端 payload。"""
    import app.main as main_mod
    captured: dict = {}

    def fake_generate(question, answer, plan, sql, **k):
        captured.update(question=question, answer=answer, plan=plan, sql=sql)
        p = tmp_path / "r.docx"
        p.write_bytes(b"PK\x03\x04stub")
        return p

    monkeypatch.setattr(main_mod, "generate_report", fake_generate)
    cid, tid = _seed_trusted_answer(client, auth_headers, narrative="可信报告结论")
    r = client.post("/api/report/generate", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": tid})
    assert r.status_code == 200, r.text
    assert captured["question"] == "本月各大区销售额"
    assert captured["answer"]["narrative"] == "可信报告结论"
    assert captured["sql"] == "SELECT 1"


def test_report_rejects_unknown_trace(client, auth_headers):
    """P0：报告生成同样要求可信 trace，伪造 payload 无法生成报告。"""
    cid, _ = _seed_trusted_answer(client, auth_headers)
    r = client.post("/api/report/generate", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": "nope"})
    assert r.status_code == 404, r.text


def test_stream_blocks_must_change_password(client, auth_headers):
    """P0：未改初始密码的用户不能用 SSE 流式问数（与 /api/chat 一致 403），
    不能绕过 must_change_password 拦截。"""
    uname = "p0streamtest"
    created = client.post("/api/admin/users", headers=auth_headers,
                          json={"username": uname, "must_change_password": True}).json()
    otp = created["one_time_password"]
    tok = client.post("/api/login", json={"username": uname, "password": otp}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    # /api/chat 应 403
    assert client.post("/api/chat", headers=h, json={"question": "hi"}).status_code == 403
    # /api/chat/stream 也必须 403（修复前可绕过），header 与 query token 两种形式都拦
    assert client.post("/api/chat/stream", headers=h, json={"question": "hi"}).status_code == 403
    assert client.post(f"/api/chat/stream?token={tok}", json={"question": "hi"}).status_code == 403


def test_password_change_revokes_old_token(client, auth_headers):
    """P1-7：改密后旧 token 立即失效（401），必须用新密码重新登录。"""
    import time as _t
    uname = "p1revoke"
    pwd1 = "OldPass123"
    client.post("/api/admin/users", headers=auth_headers,
                json={"username": uname, "password": pwd1, "must_change_password": False})
    tok1 = client.post("/api/login", json={"username": uname, "password": pwd1}).json()["token"]
    h1 = {"Authorization": f"Bearer {tok1}"}
    assert client.get("/api/me", headers=h1).status_code == 200      # 旧 token 可用
    # JWT iat 为整秒，sleep 跨秒保证 token.iat < password_changed_at
    _t.sleep(1.1)
    r = client.post("/api/me/password", headers=h1,
                    json={"old_password": pwd1, "new_password": "NewPass456"})
    assert r.status_code == 200, r.text
    assert client.get("/api/me", headers=h1).status_code == 401      # 旧 token 立即失效
    tok2 = client.post("/api/login", json={"username": uname, "password": "NewPass456"}).json()["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {tok2}"}).status_code == 200


def test_user_active_disable_blocks_login_and_token(client, auth_headers):
    """P1-10：停用账号后无法登录、已签发 token 立即失效；重新启用后恢复。"""
    uname = "p1active"
    pwd = "ActivePass1"
    client.post("/api/admin/users", headers=auth_headers,
                json={"username": uname, "password": pwd, "must_change_password": False})
    tok = client.post("/api/login", json={"username": uname, "password": pwd}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/me", headers=h).status_code == 200
    # 停用
    r = client.post(f"/api/admin/users/{uname}/active", headers=auth_headers, json={"is_active": False})
    assert r.status_code == 200, r.text
    assert client.get("/api/me", headers=h).status_code == 401                 # 旧 token 立即失效
    assert client.post("/api/login", json={"username": uname, "password": pwd}).status_code == 401  # 无法登录
    users = client.get("/api/admin/users", headers=auth_headers).json()["items"]
    assert next(u for u in users if u["username"] == uname)["is_active"] is False
    # 重新启用 → 可登录
    client.post(f"/api/admin/users/{uname}/active", headers=auth_headers, json={"is_active": True})
    assert client.post("/api/login", json={"username": uname, "password": pwd}).status_code == 200


def test_cannot_disable_logged_in_admin(client, auth_headers):
    """P1-10：默认 / 当前登录管理员不可被停用（防自锁）。"""
    r = client.post("/api/admin/users/admin/active", headers=auth_headers, json={"is_active": False})
    assert r.status_code == 400


def test_semantic_validate_endpoint(client, auth_headers):
    """#15：semantic 全文保存前 dry-run 校验，不落盘；需要管理员。"""
    bad = client.post("/api/admin/semantic/validate", headers=auth_headers, json={"content": "tables: ["})
    assert bad.status_code == 200 and bad.json()["ok"] is False
    miss = client.post("/api/admin/semantic/validate", headers=auth_headers, json={"content": "tables: {}\nmetrics: {}"})
    assert miss.json()["ok"] is False
    good = "tables:\n  t1: {}\nmetrics:\n  m1: {}\ndimensions:\n  d1: {}\n"
    ok = client.post("/api/admin/semantic/validate", headers=auth_headers, json={"content": good})
    assert ok.json()["ok"] is True and ok.json()["summary"]["tables"] == 1
    # 未登录拒绝
    assert client.post("/api/admin/semantic/validate", json={"content": good}).status_code == 401
    # 版本列表端点可访问（不被 {kind} 通配吞掉）
    assert client.get("/api/admin/semantic/versions", headers=auth_headers).status_code == 200


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


# ===================================================== 反馈闭环 + 认证清单

def test_chat_feedback_adopt_flow(client, auth_headers, tmp_path, monkeypatch):
    """采纳反馈 → few-shot 沉淀（P2 飞轮）。plan 以服务端会话存储为准。"""
    import app.core.fewshot_store as fs_mod
    monkeypatch.setenv("DATACHAT_FEWSHOT_DB", str(tmp_path / "fewshots.db"))
    monkeypatch.setattr(fs_mod, "_store_singleton", None)

    # 建会话 + 手工写入一问一答（绕过 LLM/MySQL）
    r = client.post("/api/conversations", headers=auth_headers, json={"title": "t"})
    assert r.status_code == 200
    cid = r.json()["id"]

    from app.core.conversation import get_conversation_store
    store = get_conversation_store()
    trace_id = "feedbacktrace123"
    store.append_message(cid, "user", "上月各大区终端销售额", payload={})
    store.append_message(cid, "assistant", "答案文本", payload={
        "trace_id": trace_id,
        "plan": {"metric": "terminal_sale_amount_total",
                 "table": "ads_bi_month_shop_item_dan_summary_df",
                 "group_by": ["region"], "filters": [],
                 "time_range": {"kind": "relative", "period": "last_month"}},
    })

    r = client.post("/api/chat/feedback", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": trace_id, "vote": "up"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") and body.get("adopted") is True

    hits = fs_mod.get_fewshot_store().search("上月 各大区 终端销售额", allowed_tables=None)
    assert hits and hits[0]["intent"]["metric"] == "terminal_sale_amount_total"

    # down 票：只记录不沉淀
    r = client.post("/api/chat/feedback", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": trace_id, "vote": "down"})
    assert r.status_code == 200 and r.json().get("ok")
    # trace 不存在 → 友好失败
    r = client.post("/api/chat/feedback", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": "nope", "vote": "up"})
    assert r.status_code == 200 and not r.json().get("ok", True)


def test_semantic_certification_endpoint(client, auth_headers):
    """认证清单（只读）：三类实体 + 草稿/已认证统计。"""
    r = client.get("/api/admin/semantic/certification", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body["kinds"].keys()) == {"tables", "dimensions", "metrics"}
    assert body["stats"]["draft"] + body["stats"]["verified"] > 0
    for item in body["kinds"]["metrics"]:
        assert item["status"] in ("draft", "verified")
