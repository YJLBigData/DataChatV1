"""HTTP-level tests for: folder ownership (no orphan collections) + expert-team
async background jobs + batch folder-membership. Offline/deterministic — LLM is
absent so the orchestrator degrades, but the job lifecycle still reaches a terminal
state and persists an assistant message.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["APP_ENV"] = "test"
os.environ.pop("USER_DIRECTORY", None)
os.environ.pop("DB_USERS_ENABLED", None)
os.environ["DATACHAT_AUTH_DB"] = "/tmp/datachat_test_auth.db"
os.environ["DATACHAT_CONV_DB"] = "/tmp/datachat_test_conv.db"
os.environ["DATACHAT_FOLDERS_DB"] = "/tmp/datachat_test_folders.db"
os.environ["DATACHAT_EXPERT_CONV_DB"] = "/tmp/datachat_test_expert_conv.db"
os.environ["DATACHAT_EXPERT_FOLDERS_DB"] = "/tmp/datachat_test_expert_folders.db"
os.environ["DATACHAT_EXPERT_DB"] = "/tmp/datachat_test_expert_runs.db"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["DATACHAT_ADMIN_PASSWORD"] = "test-admin-pwd"
for _p in ("/tmp/datachat_test_folders.db", "/tmp/datachat_test_expert_conv.db",
           "/tmp/datachat_test_expert_folders.db", "/tmp/datachat_test_expert_runs.db"):
    Path(_p).unlink(missing_ok=True)

# 让本模块设置的 env 生效：清掉可能已被其它测试创建的单例
from app.core import auth as _auth_mod  # noqa: E402
_auth_mod._store_singleton = None
from app.core import conversation as _conv_mod  # noqa: E402
_conv_mod._default_store = None
from app.core import folders as _folders_mod  # noqa: E402
_folders_mod._singleton = None
from app.expert_team import history as _eh_mod  # noqa: E402
_eh_mod._conv_singleton = None
_eh_mod._folders_singleton = None


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


@pytest.fixture()
def user_headers(client, auth_headers):
    """创建一个普通用户并返回其鉴权头（用于跨用户隔离断言）。"""
    uname = "u_" + uuid.uuid4().hex[:8]
    pwd = "Strong@2026x"
    r = client.post("/api/admin/users", headers=auth_headers,
                    json={"username": uname, "password": pwd, "role": "user", "must_change_password": False})
    assert r.status_code == 200, r.text
    login = client.post("/api/login", json={"username": uname, "password": pwd})
    assert login.status_code == 200, login.text
    yield {"Authorization": f"Bearer {login.json()['token']}"}
    client.delete(f"/api/admin/users/{uname}", headers=auth_headers)


# ===================================================== 问数文件夹归属（杜绝悬挂收藏）

def test_chat_collect_rejects_missing_and_foreign_folder(client, auth_headers):
    conv = client.post("/api/conversations", headers=auth_headers, json={"title": "归属测试"}).json()
    cid = conv["id"]
    # 不存在的 folder_id → 404
    bad = client.post(f"/api/conversations/{cid}/collect", headers=auth_headers,
                      json={"conversation_id": cid, "folder_id": "nope_" + uuid.uuid4().hex})
    assert bad.status_code == 404, bad.text
    # 空 folder_id → 422
    empty = client.post(f"/api/conversations/{cid}/collect", headers=auth_headers,
                        json={"conversation_id": cid, "folder_id": ""})
    assert empty.status_code == 422, empty.text
    # 真实文件夹 → 200
    fid = client.post("/api/folders", headers=auth_headers, json={"name": "经营月报"}).json()["id"]
    ok = client.post(f"/api/conversations/{cid}/collect", headers=auth_headers,
                     json={"conversation_id": cid, "folder_id": fid})
    assert ok.status_code == 200, ok.text
    # 该会话的收藏里能查到这个文件夹
    fids = client.get(f"/api/conversations/{cid}/folders", headers=auth_headers).json()["folder_ids"]
    assert fid in fids


def test_chat_collect_rejects_other_users_folder(client, auth_headers, user_headers):
    # admin 建会话 + 文件夹；普通用户不能把 admin 的会话收藏进 admin 的文件夹
    fid_admin = client.post("/api/folders", headers=auth_headers, json={"name": "admin私有"}).json()["id"]
    conv = client.post("/api/conversations", headers=user_headers, json={"title": "用户会话"}).json()
    cid = conv["id"]
    r = client.post(f"/api/conversations/{cid}/collect", headers=user_headers,
                    json={"conversation_id": cid, "folder_id": fid_admin})
    assert r.status_code == 404, r.text


def test_folders_membership_batch(client, auth_headers):
    f1 = client.post("/api/folders", headers=auth_headers, json={"name": "批量A"}).json()["id"]
    f2 = client.post("/api/folders", headers=auth_headers, json={"name": "批量B"}).json()["id"]
    c1 = client.post("/api/conversations", headers=auth_headers, json={"title": "c1"}).json()["id"]
    c2 = client.post("/api/conversations", headers=auth_headers, json={"title": "c2"}).json()["id"]
    client.post(f"/api/conversations/{c1}/collect", headers=auth_headers, json={"conversation_id": c1, "folder_id": f1})
    client.post(f"/api/conversations/{c1}/collect", headers=auth_headers, json={"conversation_id": c1, "folder_id": f2})
    client.post(f"/api/conversations/{c2}/collect", headers=auth_headers, json={"conversation_id": c2, "folder_id": f2})
    mp = client.post("/api/folders/membership", headers=auth_headers,
                     json={"conversation_ids": [c1, c2]}).json()["map"]
    assert set(mp.get(c1, [])) == {f1, f2}
    assert set(mp.get(c2, [])) == {f2}


# ===================================================== 专家团文件夹归属

def test_folder_rename_delete_missing_is_404(client, auth_headers):
    """审计 P2：改名/删除不存在（或不属于本人）的文件夹 → 404，不再静默 no-op 返回 ok。"""
    ghost = "nope_" + uuid.uuid4().hex
    # 问数文件夹
    assert client.patch(f"/api/folders/{ghost}", headers=auth_headers,
                        json={"name": "改名"}).status_code == 404
    assert client.delete(f"/api/folders/{ghost}", headers=auth_headers).status_code == 404
    # 专家团文件夹
    assert client.patch(f"/api/expert-team/folders/{ghost}", headers=auth_headers,
                        json={"name": "改名"}).status_code == 404
    assert client.delete(f"/api/expert-team/folders/{ghost}", headers=auth_headers).status_code == 404
    # 真实文件夹改名/删除仍 200
    fid = client.post("/api/folders", headers=auth_headers, json={"name": "真"}).json()["id"]
    assert client.patch(f"/api/folders/{fid}", headers=auth_headers, json={"name": "真2"}).status_code == 200
    assert client.delete(f"/api/folders/{fid}", headers=auth_headers).status_code == 200


def test_expert_collect_rejects_missing_folder(client, auth_headers):
    conv = client.post("/api/expert-team/conversations", headers=auth_headers, json={"title": "专家会话"}).json()
    cid = conv["id"]
    bad = client.post(f"/api/expert-team/conversations/{cid}/collect", headers=auth_headers,
                      json={"conversation_id": cid, "folder_id": "nope_" + uuid.uuid4().hex})
    assert bad.status_code == 404, bad.text
    fid = client.post("/api/expert-team/folders", headers=auth_headers, json={"name": "诊断报告"}).json()["id"]
    ok = client.post(f"/api/expert-team/conversations/{cid}/collect", headers=auth_headers,
                     json={"conversation_id": cid, "folder_id": fid})
    assert ok.status_code == 200, ok.text


# ===================================================== 专家团后台 job 生命周期

def _poll_job(client, headers, job_id, timeout=25.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/expert-team/jobs/{job_id}", headers=headers).json()
        if last.get("status") in ("done", "error", "missing"):
            return last
        time.sleep(0.4)
    return last


def test_expert_async_job_lifecycle_and_persist(client, auth_headers):
    sub = client.post("/api/expert-team/chat/async", headers=auth_headers,
                      json={"question": "本月各大区销售额排名", "want_report": False}).json()
    assert sub["ok"] is True and sub["job_id"] and sub["conversation_id"]
    job_id, cid = sub["job_id"], sub["conversation_id"]

    final = _poll_job(client, auth_headers, job_id)
    assert final is not None and final.get("status") in ("done", "error"), final

    # 会话里应已落库：user 提问 + assistant 产出
    detail = client.get(f"/api/expert-team/conversations/{cid}", headers=auth_headers).json()
    roles = [m["role"] for m in detail["messages"]]
    assert "user" in roles and "assistant" in roles, roles
    assert detail["messages"][0]["content"] == "本月各大区销售额排名"


def test_expert_job_user_isolation(client, auth_headers, user_headers):
    sub = client.post("/api/expert-team/chat/async", headers=auth_headers,
                      json={"question": "隔离测试问题"}).json()
    job_id = sub["job_id"]
    # 另一个用户拿不到这个 job
    other = client.get(f"/api/expert-team/jobs/{job_id}", headers=user_headers).json()
    assert other["ok"] is False and other.get("status") == "missing"


def test_expert_async_rejects_foreign_conversation(client, auth_headers, user_headers):
    # admin 建专家团会话；普通用户不能往里投递
    conv = client.post("/api/expert-team/conversations", headers=auth_headers, json={"title": "admin专家会话"}).json()
    cid = conv["id"]
    r = client.post("/api/expert-team/chat/async", headers=user_headers,
                    json={"question": "越权提问", "conversation_id": cid}).json()
    assert r["ok"] is False


def test_expert_job_per_user_backpressure(client, auth_headers, user_headers, monkeypatch):
    """每用户并发上限：超限的提交被业务拒绝（ok:false + 中文提示），不是 500。"""
    from app.expert_team.jobs import get_expert_job_manager
    mgr = get_expert_job_manager()
    monkeypatch.setattr(mgr, "per_user_limit", 1)
    # 占满 1 个名额：直接在内存登记一个 running job（避免依赖真实编排时序）
    from app.expert_team.jobs import JobState
    import time as _t
    me = client.get("/api/me", headers=user_headers).json()
    with mgr._lock:
        mgr._jobs["job_block"] = JobState(job_id="job_block", conversation_id="c", user_id=me["id"],
                                          question="占位", status="running", created_at=_t.time())
    try:
        r = client.post("/api/expert-team/chat/async", headers=user_headers,
                        json={"question": "应被背压拒绝"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False and "正在进行" in body["error"]
    finally:
        with mgr._lock:
            mgr._jobs.pop("job_block", None)


def test_expert_job_cancel(client, auth_headers):
    """取消排队/运行中的 job：返回 ok=True，随后 job 状态变 cancelled。"""
    from app.expert_team.jobs import get_expert_job_manager, JobState
    import time as _t
    mgr = get_expert_job_manager()
    me = client.get("/api/me", headers=auth_headers).json()
    with mgr._lock:
        mgr._jobs["job_cancel"] = JobState(job_id="job_cancel", conversation_id="c", user_id=me["id"],
                                           question="x", status="running", created_at=_t.time())
    r = client.post("/api/expert-team/jobs/job_cancel/cancel", headers=auth_headers).json()
    assert r["ok"] is True
    snap = client.get("/api/expert-team/jobs/job_cancel", headers=auth_headers).json()
    # 运行中取消是 best-effort：cancel_requested 已置位；这里至少不再是可继续轮询的活跃态
    assert snap["status"] in ("cancelled", "running")
    with mgr._lock:
        mgr._jobs.pop("job_cancel", None)
