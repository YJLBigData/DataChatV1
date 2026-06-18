"""HTTP-level tests for the data export queue (XLSX). Offline/deterministic:
no MySQL → the export falls back to the trusted stored result table.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_exp.db"
os.environ["DATACHAT_EXPORT_DB"] = "/tmp/datachat_test_exports.db"
os.environ["DATACHAT_EXPORT_DIR"] = tempfile.mkdtemp(prefix="dc_exports_")
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_conv_exp.db", "/tmp/datachat_test_exports.db"):
    Path(_p).unlink(missing_ok=True)

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


def _seed(client, headers, rows):
    me = client.get("/api/me", headers=headers).json()
    from app.core.conversation import get_conversation_store
    store = get_conversation_store()
    sess = store.create_session(me["id"], title="t")
    trace_id = "tr_" + uuid.uuid4().hex[:8]
    store.append_message(sess.id, "user", "各大区销售额", payload={})
    store.append_message(sess.id, "assistant", "结论", payload={
        "answer": {"narrative": "结论", "table": {
            "display_columns": [{"label": "大区"}, {"label": "销售额"}],
            "display_rows": rows,
        }},
        "plan": {"metric": "sales"}, "sql": "", "trace_id": trace_id,
    })
    return sess.id, trace_id


def _wait_ready(client, headers, jid, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/exports/{jid}", headers=headers).json()
        if j["status"] in ("ready", "error", "expired"):
            return j
        time.sleep(0.2)
    return client.get(f"/api/exports/{jid}", headers=headers).json()


def test_export_submit_ready_download(client, auth_headers):
    cid, trace = _seed(client, auth_headers, [["华东", "120"], ["华北", "90"]])
    sub = client.post("/api/exports", headers=auth_headers,
                      json={"conversation_id": cid, "trace_id": trace})
    assert sub.status_code == 200, sub.text
    jid = sub.json()["job"]["id"]
    final = _wait_ready(client, auth_headers, jid)
    assert final["status"] == "ready", final
    assert final["row_count"] == 2
    # 出现在我的列表
    items = client.get("/api/exports", headers=auth_headers).json()["items"]
    assert any(it["id"] == jid for it in items)
    # 下载得到 XLSX
    dl = client.get(f"/api/exports/{jid}/download", headers=auth_headers)
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert dl.content[:2] == b"PK"  # xlsx = zip
    # 删除
    assert client.delete(f"/api/exports/{jid}", headers=auth_headers).json()["ok"] is True
    assert client.get(f"/api/exports/{jid}", headers=auth_headers).status_code == 404


def test_export_rejects_foreign_trace(client, auth_headers):
    # 不存在的 trace → 404（不泄露存在性）
    cid, _ = _seed(client, auth_headers, [["x", "1"]])
    r = client.post("/api/exports", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": "does-not-exist"})
    assert r.status_code == 404


def test_export_same_second_no_collision(client, auth_headers):
    """审计 P0-1：同一秒提交的两个导出绝不互相覆盖 —— 物理文件按 job id 唯一命名。
    用不同行数的两份数据各提交一次，下载校验各自对应自己的内容。"""
    cid_a, trace_a = _seed(client, auth_headers, [["华东", "1"], ["华北", "2"], ["华南", "3"]])  # 3 行
    cid_b, trace_b = _seed(client, auth_headers, [["华中", "9"]])                                # 1 行
    ja = client.post("/api/exports", headers=auth_headers,
                     json={"conversation_id": cid_a, "trace_id": trace_a}).json()["job"]["id"]
    jb = client.post("/api/exports", headers=auth_headers,
                     json={"conversation_id": cid_b, "trace_id": trace_b}).json()["job"]["id"]
    assert ja != jb
    fa = _wait_ready(client, auth_headers, ja)
    fb = _wait_ready(client, auth_headers, jb)
    assert fa["status"] == "ready" and fb["status"] == "ready"
    # 行数各自正确（若文件互相覆盖，两者会相等/串味）
    assert fa["row_count"] == 3 and fb["row_count"] == 1
    # 两个文件都能下载且都是有效 xlsx
    da = client.get(f"/api/exports/{ja}/download", headers=auth_headers)
    db = client.get(f"/api/exports/{jb}/download", headers=auth_headers)
    assert da.status_code == 200 and db.status_code == 200
    assert da.content[:2] == b"PK" and db.content[:2] == b"PK"
    assert da.content != db.content  # 内容不同 → 没有互相覆盖


def test_export_per_user_backpressure(client, auth_headers, monkeypatch):
    """每用户在途上限：超限的提交业务拒绝（ok:false），不是 500。"""
    monkeypatch.setenv("EXPORT_PER_USER_MAX", "1")
    me = client.get("/api/me", headers=auth_headers).json()
    # 预置一个该用户的 queued job 占满名额（不跑，纯占位）
    from app.exports.store import get_export_store
    import time as _t
    get_export_store().create(
        user_id=me["id"], conversation_id="c", trace_id="t", question="占位",
        filename="x.xlsx", expires_at=_t.time() + 3600,
    )
    cid, trace = _seed(client, auth_headers, [["x", "1"]])
    r = client.post("/api/exports", headers=auth_headers,
                    json={"conversation_id": cid, "trace_id": trace})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and ("较多" in body["error"] or "排队" in body["error"])


def test_export_download_user_isolation(client, auth_headers):
    cid, trace = _seed(client, auth_headers, [["a", "1"]])
    jid = client.post("/api/exports", headers=auth_headers,
                      json={"conversation_id": cid, "trace_id": trace}).json()["job"]["id"]
    _wait_ready(client, auth_headers, jid)
    # 另一个用户拿不到（404）
    uname = "e_" + uuid.uuid4().hex[:8]
    client.post("/api/admin/users", headers=auth_headers,
                json={"username": uname, "password": "Strong@2026x", "role": "user", "must_change_password": False})
    tok = client.post("/api/login", json={"username": uname, "password": "Strong@2026x"}).json()["token"]
    other = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"/api/exports/{jid}", headers=other).status_code == 404
    assert client.get(f"/api/exports/{jid}/download", headers=other).status_code == 404
    client.delete(f"/api/admin/users/{uname}", headers=auth_headers)
