"""阶段 1/2/3 新增能力的单元测试 —— 全部纯 Python，无网络/无 DB/无 LLM 依赖。

覆盖：
  · 1.4 slowapi 限流装饰器：模块可导入
  · 2.3 prometheus instrumentator：模块可导入
  · 2.4 logging_setup：JSON 行格式 + trace_id 注入
  · 2.2 celery tasks：模块可导入（celery 可缺）
  · 3.1 SemanticLayer.join_path() / can_join() / joins_between()
  · 3.1 SQLGuard 多表 JOIN 的 feature-flag 行为
  · 3.3 EXPLAIN gate feature flag 开关
"""
from __future__ import annotations

import importlib
import io
import json
import logging
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import load_config, reset_for_tests  # noqa: E402


@pytest.fixture(scope="module")
def semantic():
    reset_for_tests()
    cfg = load_config(reload=True)
    from app.core.semantic import SemanticLayer
    return SemanticLayer(cfg.app.semantic_path)


# ----------------------------------------------------------------------- 2.4 logging

def test_logging_setup_json_format(monkeypatch):
    from app.logging_setup import configure_logging, set_trace_id
    monkeypatch.setenv("DATACHAT_LOG_FORMAT", "json")
    # 强制重新配置，让 monkeypatch 生效
    configure_logging(force=True)
    set_trace_id("trace-xyz")

    buf = io.StringIO()
    root = logging.getLogger()
    h = logging.StreamHandler(buf)
    h.setFormatter(root.handlers[0].formatter)  # 拿到我们装的 JSON formatter
    root.addHandler(h)
    try:
        logging.getLogger("test").info("hello %s", "world")
        line = [l for l in buf.getvalue().splitlines() if l.strip()][-1]
        obj = json.loads(line)
        assert obj["level"] == "INFO"
        assert obj["msg"] == "hello world"
        assert obj["trace_id"] == "trace-xyz"
        assert "pid" in obj
    finally:
        root.removeHandler(h)


# ----------------------------------------------------------------------- 1.4 / 2.3 imports

def test_slowapi_optional_import():
    """slowapi 不存在不应阻塞主模块；存在则提供 Limiter。"""
    spec = importlib.util.find_spec("slowapi")
    if spec is None:
        pytest.skip("slowapi not installed in dev env (CI 必装)")
    from slowapi import Limiter
    assert Limiter is not None


def test_prometheus_instrumentator_optional_import():
    spec = importlib.util.find_spec("prometheus_fastapi_instrumentator")
    if spec is None:
        pytest.skip("prometheus_fastapi_instrumentator not installed")
    from prometheus_fastapi_instrumentator import Instrumentator
    assert Instrumentator is not None


def test_celery_tasks_module_imports():
    """celery 缺失时 tasks 模块仍可导入（celery_app=None），不破坏 worker 启动。"""
    from app.core import tasks
    assert hasattr(tasks, "celery_app")
    # 装了 celery 时 celery_app 不为 None；没装也不报错
    if tasks.celery_app is not None:
        # 标准任务名应可注册
        names = [n for n in tasks.celery_app.tasks if "datachatv1" in n]
        assert "datachatv1.demo.ping" in names


# ----------------------------------------------------------------------- 3.1 joins

def test_join_path_direct(semantic):
    """summary↔target 是直连 join，应返回 [JoinDef]。"""
    path = semantic.join_path(
        "ads_bi_month_shop_item_dan_summary_df",
        "ads_bi_month_shop_item_dan_target_summary_df",
    )
    assert path is not None and len(path) == 1
    j = path[0]
    cols = {a for pair in j.on for a in pair}
    assert "year" in cols and "month" in cols and "lev2_name" in cols


def test_join_path_two_hop_through_summary(semantic):
    """target → summary → member 应该 2 跳可达（summary 是事实表中心）。"""
    path = semantic.join_path(
        "ads_bi_month_shop_item_dan_target_summary_df",
        "ads_member_first_purchase_new_customer_total_df",
    )
    assert path is not None
    assert len(path) == 2


def test_join_path_same_table(semantic):
    p = semantic.join_path("ads_bi_month_shop_item_dan_summary_df",
                           "ads_bi_month_shop_item_dan_summary_df")
    assert p == []


def test_can_join_two_tables(semantic):
    assert semantic.can_join([
        "ads_bi_month_shop_item_dan_summary_df",
        "ads_bi_month_shop_item_dan_target_summary_df",
    ]) is True


def test_cannot_join_unrelated_table(semantic):
    # detail 表暂未在 joins 声明 → 应当返回 False（保持保守安全）
    assert semantic.can_join([
        "ads_bi_month_shop_item_dan_summary_df",
        "ads_bi_month_shop_item_dan_detail_df",
    ]) is False


# ----------------------------------------------------------------------- 3.1 SQL Guard 多表

def test_guard_blocks_multi_table_when_flag_off(monkeypatch, semantic):
    monkeypatch.delenv("DATACHAT_ALLOW_MULTI_TABLE", raising=False)
    from app.core.guard import SQLGuard, GuardError
    guard = SQLGuard(
        allowed_tables=[t.name for t in semantic.list_tables()],
        semantic_layer=semantic,
    )
    sql = (
        "SELECT s.year, s.month, SUM(s.terminal_sale_amount) AS amt "
        "FROM ads_bi_month_shop_item_dan_summary_df s "
        "JOIN ads_bi_month_shop_item_dan_target_summary_df t "
        "ON s.year=t.year AND s.month=t.month "
        "GROUP BY s.year, s.month"
    )
    with pytest.raises(GuardError) as ei:
        guard.validate(sql)
    assert "DATACHAT_ALLOW_MULTI_TABLE" in str(ei.value)


def test_guard_allows_multi_table_when_flag_on_and_join_declared(monkeypatch, semantic):
    monkeypatch.setenv("DATACHAT_ALLOW_MULTI_TABLE", "1")
    from app.core.guard import SQLGuard
    guard = SQLGuard(
        allowed_tables=[t.name for t in semantic.list_tables()],
        semantic_layer=semantic,
    )
    sql = (
        "SELECT s.year, s.month, SUM(s.terminal_sale_amount) AS amt "
        "FROM ads_bi_month_shop_item_dan_summary_df s "
        "JOIN ads_bi_month_shop_item_dan_target_summary_df t "
        "ON s.year=t.year AND s.month=t.month "
        "GROUP BY s.year, s.month"
    )
    rep = guard.validate(sql)
    assert len(rep.tables) == 2


def test_guard_rejects_multi_table_when_no_join_declared(monkeypatch, semantic):
    monkeypatch.setenv("DATACHAT_ALLOW_MULTI_TABLE", "1")
    from app.core.guard import SQLGuard, GuardError
    guard = SQLGuard(
        allowed_tables=[t.name for t in semantic.list_tables()],
        semantic_layer=semantic,
    )
    sql = (
        "SELECT s.year FROM ads_bi_month_shop_item_dan_summary_df s "
        "JOIN ads_bi_month_shop_item_dan_detail_df d ON s.year=d.year"
    )
    with pytest.raises(GuardError) as ei:
        guard.validate(sql)
    assert "join" in str(ei.value).lower() or "未在" in str(ei.value)


# ----------------------------------------------------------------------- 3.3 EXPLAIN 闸门

def test_explain_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DATACHAT_EXPLAIN_GATE", raising=False)
    # 不真连 DB，只测开关静默 no-op
    from app.core.exec.mysql_exec import MySQLExecutor
    e = MySQLExecutor.__new__(MySQLExecutor)
    # 用一个假 conn 验证：不调 execute 即说明闸门关闭
    class FakeConn:
        called = False
        def execute(self, *a, **kw):
            FakeConn.called = True
    e._maybe_explain_gate("SELECT 1", FakeConn())
    assert FakeConn.called is False


def test_explain_gate_enabled_calls_explain(monkeypatch):
    monkeypatch.setenv("DATACHAT_EXPLAIN_GATE", "1")
    from app.core.exec.mysql_exec import MySQLExecutor
    e = MySQLExecutor.__new__(MySQLExecutor)
    # FakeConn 返回 EXPLAIN 结果 rows=999（远低于默认 1_000_000 阈值），应放行
    class FakeResult:
        def __init__(self): self._rows = [{"rows": 999}]
        def keys(self): return ["rows"]
        def __iter__(self): return iter([[999]])
    class FakeConn:
        called = False
        def execute(self, *a, **kw):
            FakeConn.called = True
            return FakeResult()
    e._maybe_explain_gate("SELECT 1 FROM t", FakeConn())
    assert FakeConn.called is True


def test_llm_settings_store_set_get_mask(tmp_path, monkeypatch):
    """LLMSettingsStore：白名单 / 脱敏 / DB 优先 / 空串清除 / version 自增。"""
    # 清掉本机 .env 灌进来的 env 变量，确认 DB 与 default 行为
    for k in ["DASHSCOPE_API_KEY", "DASHSCOPE_MODEL", "DASHSCOPE_BASE_URL", "DASHSCOPE_EMBED_MODEL", "LLM_PROVIDER"]:
        monkeypatch.setenv(k, "")
    from app.core.llm_settings import LLMSettingsStore, ALLOWED_KEYS, SECRET_KEYS, _mask
    s = LLMSettingsStore(tmp_path / "llm.db")
    v0 = s.version
    # 1) 写两个键 + 一个未授权键（应被静默丢弃）
    changed = s.set_many({
        "DASHSCOPE_API_KEY": "FAKE_TEST_KEY_ZZZZ1234",
        "DASHSCOPE_MODEL": "qwen-max",
        "FOO_HACK": "x",   # 不在白名单
    })
    assert set(changed) == {"DASHSCOPE_API_KEY", "DASHSCOPE_MODEL"}
    assert s.version > v0
    # 2) 读：DB 优先于 env
    assert s.get("DASHSCOPE_API_KEY") == "FAKE_TEST_KEY_ZZZZ1234"
    assert s.get("DASHSCOPE_MODEL") == "qwen-max"
    # 3) get_all_effective：secret 必须脱敏
    eff = s.get_all_effective()
    assert eff["DASHSCOPE_API_KEY"]["is_secret"] is True
    assert eff["DASHSCOPE_API_KEY"]["is_set"] is True
    # 脱敏：前3 + **** + 后4。新 fixture 是 "FAKE_TEST_KEY_ZZZZ1234"（22 字符）
    assert eff["DASHSCOPE_API_KEY"]["value"].startswith("FAK")
    assert eff["DASHSCOPE_API_KEY"]["value"] != "FAKE_TEST_KEY_ZZZZ1234"
    assert eff["DASHSCOPE_API_KEY"]["value"].endswith("1234")
    assert "****" in eff["DASHSCOPE_API_KEY"]["value"]
    # 非 secret 直接回显
    assert eff["DASHSCOPE_MODEL"]["value"] == "qwen-max"
    # 4) 空串=清除，回退到 env（这里 env 也空 → ""）
    s.set_many({"DASHSCOPE_MODEL": ""})
    assert s.get("DASHSCOPE_MODEL", default="") == ""
    # 5) 白名单：所有键必须在白名单
    for k in eff.keys():
        assert k in ALLOWED_KEYS
    # 6) mask 短串测试
    assert _mask("") == ""
    assert _mask("abc") == "****"
    assert _mask("sk-abcdef1234") == "sk-****1234"


def test_llm_router_picks_db_overrides(monkeypatch, tmp_path):
    """LLMRouter 的 _api_key/_chat_model 等热改方法：DB 优先于 env/cfg。"""
    monkeypatch.setenv("DATACHAT_LLM_SETTINGS_DB", str(tmp_path / "llm.db"))
    # 重置 settings store singleton 以使用新路径
    from app.core import llm_settings as ls_mod
    ls_mod._store_singleton = None
    from app.core.llm_settings import get_llm_settings_store
    store = get_llm_settings_store()
    store.set_many({
        "DASHSCOPE_API_KEY": "FAKE_DB_KEY_ZZZ1234567890",
        "DASHSCOPE_MODEL": "qwen-from-db",
    })
    # 不调真 LLM，只用 router 实例方法读
    from app.core.llm.router import LLMRouter
    r = LLMRouter()
    assert r._api_key() == "FAKE_DB_KEY_ZZZ1234567890"
    assert r._chat_model() == "qwen-from-db"
    # 清空 → 回到 env（这里 env 没设）→ 回到 cfg 默认
    store.set_many({"DASHSCOPE_MODEL": ""})
    assert r._chat_model() == r.cfg.llm.bailian_chat_model


def test_explain_gate_blocks_high_cost(monkeypatch):
    monkeypatch.setenv("DATACHAT_EXPLAIN_GATE", "1")
    monkeypatch.setenv("DATACHAT_EXPLAIN_MAX_ROWS", "100")
    from app.core.exec.mysql_exec import MySQLExecutor, ExecError
    e = MySQLExecutor.__new__(MySQLExecutor)
    class FakeResult:
        def keys(self): return ["rows"]
        def __iter__(self): return iter([[1_000_000]])
    class FakeConn:
        def execute(self, *a, **kw): return FakeResult()
    with pytest.raises(ExecError) as ei:
        e._maybe_explain_gate("SELECT * FROM t", FakeConn())
    assert "成本闸门" in str(ei.value)
