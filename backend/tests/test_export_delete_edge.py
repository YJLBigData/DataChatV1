"""Edge-case repro for 导数(导出)数据删除: cancel running/queued, double-delete, orphan file."""
from __future__ import annotations

import os
import sys
import time
import uuid
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth_del.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv_del.db"
os.environ["DATACHAT_EXPORT_DB"] = "/tmp/datachat_test_exports_del.db"
os.environ["DATACHAT_EXPORT_DIR"] = tempfile.mkdtemp(prefix="dc_exp_del_")
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_conv_del.db", "/tmp/datachat_test_exports_del.db"):
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
def H(client):
    r = client.post("/api/login", json={"username": "admin", "password": "test-admin-pwd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed(client, headers, rows):
    me = client.get("/api/me", headers=headers).json()
    from app.core.conversation import get_conversation_store
    store = get_conversation_store()
    sess = store.create_session(me["id"], title="t")
    trace_id = "tr_" + uuid.uuid4().hex[:8]
    store.append_message(sess.id, "user", "q", payload={})
    store.append_message(sess.id, "assistant", "结论", payload={
        "answer": {"narrative": "结论", "table": {
            "display_columns": [{"label": "大区"}, {"label": "销售额"}],
            "display_rows": rows,
        }},
        "plan": {"metric": "sales"}, "sql": "", "trace_id": trace_id,
    })
    return sess.id, trace_id


def test_delete_queued_job_cancels(client, H):
    """Delete a job that is still queued/running (created directly, not run) -> must succeed."""
    me = client.get("/api/me", headers=H).json()
    from app.exports.store import get_export_store
    job = get_export_store().create(
        user_id=me["id"], conversation_id="c", trace_id="t", question="占位",
        filename="x.xlsx", expires_at=time.time() + 3600,
    )
    assert job.status == "queued"
    res = client.delete(f"/api/exports/{job.id}", headers=H)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["deleted"] is True
    # gone from list + 404 on get
    assert client.get(f"/api/exports/{job.id}", headers=H).status_code == 404
    items = client.get("/api/exports", headers=H).json()["items"]
    assert all(it["id"] != job.id for it in items)


def test_double_delete_is_idempotent(client, H):
    """重复删除（轮询清理/另一个 worker/重复点击）必须幂等成功，绝不报删除失败。"""
    cid, trace = _seed(client, H, [["a", "1"]])
    jid = client.post("/api/exports", headers=H, json={"conversation_id": cid, "trace_id": trace}).json()["job"]["id"]
    # first delete ok
    r1 = client.delete(f"/api/exports/{jid}", headers=H)
    assert r1.status_code == 200 and r1.json()["ok"] is True and r1.json()["deleted"] is True
    # second delete: row already gone -> idempotent 200 ok (NOT a failure)
    r2 = client.delete(f"/api/exports/{jid}", headers=H)
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True and body["deleted"] is False and body.get("already_gone") is True


def test_delete_never_existed_is_idempotent(client, H):
    """删除一个从不存在的 job id 也幂等成功（前端把它从列表抹掉即可）。"""
    r = client.delete("/api/exports/exp_does_not_exist", headers=H)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json().get("already_gone") is True


def test_delete_foreign_job_is_404(client, H):
    """job 存在但属于别人 → 404（既防越权又不泄露存在性）。"""
    # admin 建一个 job
    cid, trace = _seed(client, H, [["x", "1"]])
    jid = client.post("/api/exports", headers=H, json={"conversation_id": cid, "trace_id": trace}).json()["job"]["id"]
    # 另一个用户尝试删除 → 404
    uname = "del_" + uuid.uuid4().hex[:8]
    client.post("/api/admin/users", headers=H,
                json={"username": uname, "password": "Strong@2026x", "role": "user", "must_change_password": False})
    tok = client.post("/api/login", json={"username": uname, "password": "Strong@2026x"}).json()["token"]
    other = {"Authorization": f"Bearer {tok}"}
    assert client.delete(f"/api/exports/{jid}", headers=other).status_code == 404
    # 原主人仍可正常删除
    assert client.delete(f"/api/exports/{jid}", headers=H).status_code == 200
    client.delete(f"/api/admin/users/{uname}", headers=H)


def test_delete_ready_removes_file(client, H):
    cid, trace = _seed(client, H, [["华东", "1"], ["华北", "2"]])
    jid = client.post("/api/exports", headers=H, json={"conversation_id": cid, "trace_id": trace}).json()["job"]["id"]
    # wait ready
    deadline = time.time() + 15
    final = None
    while time.time() < deadline:
        final = client.get(f"/api/exports/{jid}", headers=H).json()
        if final["status"] in ("ready", "error"):
            break
        time.sleep(0.2)
    assert final and final["status"] == "ready", final
    from app.exports.store import get_export_store
    path = get_export_store().get(jid).path
    assert path and Path(path).exists()
    res = client.delete(f"/api/exports/{jid}", headers=H).json()
    assert res["ok"] is True and res["file_deleted"] is True
    assert not Path(path).exists()  # file actually removed -> no orphan
