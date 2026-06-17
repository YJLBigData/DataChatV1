"""专家团（expert_team）离线测试 —— 不调真实 LLM / MySQL。

覆盖：定义解析（registry）、自建 skill 持久化（store）、确定性兜底编排（orchestrator）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

os.environ["DATACHAT_EXPERT_DB"] = "/tmp/datachat_test_expert.db"

from app.expert_team.registry import get_registry  # noqa: E402
from app.expert_team.store import ExpertTeamStore  # noqa: E402
from app.expert_team.orchestrator import ExpertTeamOrchestrator, _table_preview  # noqa: E402


# ------------------------------------------------------------ registry
def test_registry_loads_director_and_workers():
    reg = get_registry()
    d = reg.director()
    assert d is not None and d.is_director
    assert d.name == "卓见全" and d.profession == "决策调度总监"
    workers = reg.workers()
    ids = {w.id for w in workers}
    # 五大分析师 + 取数 + 双检核都在
    for must in ("sales-analyst", "channel-analyst", "user-ops-analyst",
                 "market-analyst", "finance-analyst", "data-query-analyst",
                 "data-auditor", "knowledge-auditor"):
        assert must in ids, f"缺少专家 {must}"
    # director 不混进 workers
    assert all(not w.is_director for w in workers)


def test_registry_skill_methodology_and_knowledge():
    reg = get_registry()
    sales = reg.expert("sales-analyst")
    assert sales and "region-sales-diagnosis" in sales.skill_ids
    method = reg.skill_methodology(sales.skill_ids, max_chars=3000)
    assert method and len(method) <= 3000
    kb = reg.knowledge_digest(max_chars=4000)
    assert kb and len(kb) <= 4000
    # 知识库文件齐全（含数据资产/字典/方法论/行业/输出规范）
    files = reg.knowledge_files()
    for k in ("analysis-frameworks", "industry-knowledge", "output-format-spec", "data-dictionary"):
        assert k in files


# ------------------------------------------------------------ store
def test_user_skill_crud():
    store = ExpertTeamStore("/tmp/datachat_test_expert_crud.db")
    sk = store.create_skill("u1", "促销ROI专家", "促销分析", "你专注促销活动的ROI测算。", "🎯")
    assert sk.id.startswith("usk_")
    assert store.get_skill("u1", sk.id) is not None
    # 隔离：别的用户看不到
    assert store.get_skill("u2", sk.id) is None
    assert len(store.list_skills("u1")) == 1
    assert store.update_skill("u1", sk.id, name="促销专家") is True
    assert store.get_skill("u1", sk.id).name == "促销专家"
    assert store.delete_skill("u1", sk.id) is True
    assert store.list_skills("u1") == []


# ------------------------------------------------------------ orchestrator (deterministic)
def test_pool_merges_user_skills():
    o = ExpertTeamOrchestrator()
    pool = o._build_pool([{"id": "usk_x", "name": "定价专家", "profession": "定价", "instructions": "x", "emoji": "💲"}])
    assert any(p.id == "usk_x" and not p.is_builtin for p in pool)
    assert sum(1 for p in pool if p.is_builtin) >= 8


def test_fallback_route_keyword_and_fast():
    o = ExpertTeamOrchestrator()
    pool = o._build_pool(None)
    # 含"报告"+"销售" → slow + want_report + sales
    fr = o._fallback_route("北一区销售额下滑，出个诊断报告", pool, want_report=False)
    assert fr["route"] == "slow" and fr["want_report"] is True
    assert any(e["id"] == "sales-analyst" for e in fr["experts"])
    # 无领域关键词 → 快通道兜底到取数专家
    fr2 = o._fallback_route("帮我看看这个", pool, want_report=False)
    assert fr2["route"] == "fast"
    assert fr2["experts"] and fr2["experts"][0]["id"] == "data-query-analyst"


def test_builtin_override_edit_hide_reset():
    """内置专家也支持增删改查：改→写覆盖，删→隐藏（软删），还原→清覆盖。"""
    from app.expert_team.members import list_members, member_detail
    from app.expert_team.store import ExpertTeamStore
    import app.expert_team.store as store_mod
    st = ExpertTeamStore("/tmp/datachat_test_expert_override.db")
    store_mod._store_singleton = st  # members.py 用 get_expert_store() 取单例
    U = "ov_user"
    n0 = len(list_members(U))
    assert n0 >= 8
    # 改
    st.upsert_override(U, "sales-analyst", name="销冠分析师", instructions="只做量价分解")
    d = member_detail(U, "sales-analyst")
    assert d["name"] == "销冠分析师" and d["has_override"] is True
    assert d["instructions"].startswith("只做量价") and d["default"]["name"] == "齐增辉"
    # 删（隐藏）
    st.upsert_override(U, "channel-analyst", deleted=True)
    assert "channel-analyst" not in {m["id"] for m in list_members(U)}
    assert len(list_members(U)) == n0 - 1
    # 还原
    assert st.clear_override(U, "sales-analyst") is True
    st.clear_override(U, "channel-analyst")
    assert member_detail(U, "sales-analyst")["name"] == "齐增辉"
    assert len(list_members(U)) == n0
    store_mod._store_singleton = None  # 不污染其它测试


def test_pool_respects_overrides():
    """编排池应用覆盖：隐藏的内置专家不进池，改名的按覆盖名进池。"""
    o = ExpertTeamOrchestrator()
    overrides = {
        "channel-analyst": {"deleted": 1},
        "sales-analyst": {"name": "销冠", "deleted": 0},
    }
    pool = o._build_pool(None, overrides)
    ids = {p.id for p in pool}
    assert "channel-analyst" not in ids
    sales = next(p for p in pool if p.id == "sales-analyst")
    assert sales.name == "销冠"


def test_table_preview_compact():
    table = {
        "display_columns": [{"label": "省区"}, {"label": "达成率"}],
        "display_rows": [{"省区": "甘肃", "达成率": "85%"}, {"省区": "新疆", "达成率": "88%"}],
    }
    txt = _table_preview(table)
    assert "省区 | 达成率" in txt and "甘肃" in txt
