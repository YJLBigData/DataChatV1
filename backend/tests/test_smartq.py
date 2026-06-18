"""SmartQ 集成离线测试 —— 归一化字段映射 + 未启用时的优雅降级 + 脱敏诊断。

不触达真实 quickbi.feihe.com（沙箱不可达，且不应在测试里联网）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.integrations.smartq.normalize import (  # noqa: E402
    normalize_smartq_answer,
    smartq_answer_is_substantive,
)
from app.integrations.smartq.config import load_smartq_config, masked_diagnostics  # noqa: E402
from app.integrations.smartq.client import SmartQClient, SmartQError, resolve_smartq_user_id  # noqa: E402


def test_normalize_list_of_dicts():
    raw = {
        "ConclusionText": "本月华东销售额1.2亿，环比+8%",
        "LogicSql": "SELECT region, sales FROM t",
        "ChartType": "column",
        "Columns": [{"Name": "region", "Label": "大区"}, {"Name": "sales", "Label": "销售额"}],
        "Values": [{"region": "华东", "sales": 120000000}, {"region": "华北", "sales": 90000000}],
    }
    ans = normalize_smartq_answer(raw, question="各大区销售额")
    assert ans["narrative"].startswith("本月华东")
    assert ans["table"]["row_count"] == 2
    assert ans["table"]["display_columns"][0]["label"] == "大区"
    assert ans["table"]["display_rows"][0] == ["华东", "120000000"]
    assert ans["chart"]["type"] == "bar"
    assert ans["explainability"]["sql"].startswith("SELECT")


def test_normalize_list_of_lists_and_empty():
    raw = {"Headers": ["a", "b"], "Values": [[1, 2], [3, 4]], "ChartType": "line"}
    ans = normalize_smartq_answer(raw)
    assert ans["table"]["row_count"] == 2 and ans["chart"]["type"] == "line"
    empty = normalize_smartq_answer({})
    assert empty["table"]["row_count"] == 0 and empty["chart"]["type"] == "none"


def test_disabled_degrades_gracefully(monkeypatch):
    for k in ("SMARTQ_ENABLED", "SMARTQ_API_KEY", "SMARTQ_API_SECRET", "SMARTQ_SERVER_DOMAIN"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_smartq_config()
    assert cfg.ready is False
    try:
        SmartQClient(cfg)._request("SmartqQueryAbility", {"UserQuestion": "x"})
        assert False, "should have raised"
    except SmartQError as exc:
        assert "未启用" in exc.message or "未配置" in exc.message


def test_masked_diagnostics_never_leaks(monkeypatch):
    monkeypatch.setenv("SMARTQ_API_KEY", "abcdef0123456789")
    monkeypatch.setenv("SMARTQ_API_SECRET", "supersecretvalue1234")
    diag = masked_diagnostics()
    assert "abcdef0123456789" not in diag["api_key"]
    assert "supersecretvalue1234" not in diag["api_secret"]
    assert "***" in diag["api_key"]


def test_identity_mapping_ignores_frontend(monkeypatch):
    monkeypatch.setenv("SMARTQ_DEFAULT_USER_ID", "server-mapped-id")
    cfg = load_smartq_config()
    # 即便调用方给了 fallback，也优先服务端配置；绝不接受前端 userId
    assert resolve_smartq_user_id(cfg, fallback="attacker-supplied") == "server-mapped-id"


# ===================================================== 假成功防线（审计 P0-2）

def test_substantive_detection():
    """无行 / 无结论 / 无 SQL 的空壳响应不得被当成成功结果。"""
    # 实质结果：有行
    rich = normalize_smartq_answer({"Headers": ["a"], "Values": [[1]]})
    assert smartq_answer_is_substantive(rich) is True
    # 仅有结论文本
    only_text = normalize_smartq_answer({"ConclusionText": "华东第一"})
    assert smartq_answer_is_substantive(only_text) is True
    # 仅有 SQL
    only_sql = normalize_smartq_answer({"LogicSql": "SELECT 1"})
    assert smartq_answer_is_substantive(only_sql) is True
    # 空壳（典型越权 cube / 无数据）：兜底文案不算结论 → 非实质
    empty = normalize_smartq_answer({}, question="本月销售额")
    assert smartq_answer_is_substantive(empty) is False


def test_request_rejects_business_error(monkeypatch):
    """官方 Success=False / 异常返回形态一律抛 SmartQError（不把空壳当成功）。"""
    monkeypatch.setenv("SMARTQ_ENABLED", "1")
    monkeypatch.setenv("SMARTQ_API_KEY", "k")
    monkeypatch.setenv("SMARTQ_API_SECRET", "s")
    monkeypatch.setenv("SMARTQ_SERVER_DOMAIN", "https://example.invalid")
    cfg = load_smartq_config()
    client = SmartQClient(cfg)

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp(_Client.payload)

    import app.integrations.smartq.client as mod

    # Success=False → 业务失败
    _Client.payload = {"Success": False, "Code": "Forbidden", "Message": "no auth"}
    monkeypatch.setattr(mod.httpx, "Client", _Client)
    try:
        client._request("SmartqQueryAbility", {"UserQuestion": "x"})
        assert False, "should raise on Success=False"
    except SmartQError:
        pass

    # 既无 Success 又无 Result → 异常返回形态
    _Client.payload = {"RequestId": "abc"}
    try:
        client._request("SmartqQueryAbility", {"UserQuestion": "x"})
        assert False, "should raise on empty shell response"
    except SmartQError:
        pass

    # 正常：Success=True + Result → 透传
    _Client.payload = {"Success": True, "Result": {"ConclusionText": "ok"}}
    out = client._request("SmartqQueryAbility", {"UserQuestion": "x"})
    assert out["Result"]["ConclusionText"] == "ok"
