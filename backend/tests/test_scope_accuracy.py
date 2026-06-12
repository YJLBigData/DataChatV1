"""P0-P2 准确率改造的回归测试：检索分域 / L2 串数据修复 / 超范围拒答 /
全量表卡片 / 歧义澄清 / 语义认证状态 / few-shot 飞轮。

全部不调 LLM、不连 MySQL/Redis，快速且确定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import load_config, reset_for_tests  # noqa: E402
from app.core.semantic import SemanticLayer  # noqa: E402
from app.core.guard import SQLGuard, GuardError  # noqa: E402
from app.core.nl2sql.plan import PlanFilter, QueryPlan, TimeKind, TimeRange  # noqa: E402
from app.core.nl2sql.planner import Planner  # noqa: E402
from app.core.retrieval.hybrid import RetrievalBundle, RetrievalCandidate  # noqa: E402

SUMMARY_T = "ads_bi_month_shop_item_dan_summary_df"
HS_T = "ads_bi_hs_sale_info_df"
NEWCUST_T = "ads_member_first_purchase_new_customer_total_df"


@pytest.fixture(scope="module")
def cfg():
    reset_for_tests()
    return load_config(reload=True)


@pytest.fixture(scope="module")
def semantic(cfg):
    return SemanticLayer(cfg.app.semantic_path)


@pytest.fixture()
def perm_store(tmp_path, monkeypatch):
    """指向临时权限库的干净 PermissionsStore（并重置单例）。"""
    import app.core.permissions as perms
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(tmp_path / "perm.db"))
    monkeypatch.setattr(perms, "_store_singleton", None)
    return perms.get_permissions_store()


def _mk_planner(semantic) -> Planner:
    """不触发 LLM 路由初始化的 Planner（被测方法均不调用 llm/retriever/cache 网络）。"""
    class _NoopCache:
        def get_plan(self, *a, **kw): return None
        def set_plan(self, *a, **kw): pass
    p = Planner.__new__(Planner)
    p.semantic = semantic
    p.retriever = None
    p.llm = object()
    p.cache = _NoopCache()
    return p


def _bundle(metrics=(), tables=(), dimensions=(), few_shots=()) -> RetrievalBundle:
    return RetrievalBundle(
        metrics=list(metrics), dimensions=list(dimensions),
        tables=list(tables), few_shots=list(few_shots), elapsed_ms=0,
    )


def _cand(kind, name, label, score, **payload) -> RetrievalCandidate:
    return RetrievalCandidate(kind=kind, name=name, label=label, score=score, text=label, payload=payload)


# ===================================================== UserScope / 指纹

def test_permission_fingerprint_changes_with_rules(perm_store):
    perm_store.set_for_user("alice", row_rules={"region": ["北一区"]}, allowed_tables=[HS_T])
    fp1 = perm_store.get_for_user("alice").fingerprint()
    perm_store.set_for_user("alice", row_rules={"region": ["南一区"]})
    fp2 = perm_store.get_for_user("alice").fingerprint()
    assert fp1 != fp2, "行级规则变更必须改变权限指纹（否则 L1/q2p 缓存不失效）"
    perm_store.set_for_user("alice", row_rules={"region": ["北一区"]})
    assert perm_store.get_for_user("alice").fingerprint() == fp1, "指纹必须是确定性的"


def test_get_user_scope_intersects_semantic(perm_store, semantic):
    from app.core.permissions import get_user_scope
    perm_store.set_for_user("bob", allowed_tables=[HS_T, "not_a_real_table"])
    scope = get_user_scope("bob", is_admin=False, semantic_layer=semantic)
    assert scope.restricted
    assert scope.allowed_tables == frozenset({HS_T}), "配置里的未知表必须被语义层交集剔除"

    admin_scope = get_user_scope("root", is_admin=True, semantic_layer=semantic)
    assert not admin_scope.restricted

    perm_store.set_for_user("carol", allowed_tables=["ghost_table_only"])
    ghost = get_user_scope("carol", is_admin=False, semantic_layer=semantic)
    assert ghost.restricted and ghost.allowed_tables == frozenset(), \
        "全部表都不在语义层 → 空集（检索零召回 → 超范围拒答），而不是放开全部"


# ===================================================== 检索分域

def test_retrieval_scope_filters_all_kinds(cfg, tmp_path, monkeypatch):
    """BM25-only 检索（embedding 故意失败）下，分域过滤必须对四类候选全部生效。"""
    from app.core.retrieval import hybrid as hybrid_mod
    from app.core.retrieval.hybrid import HybridRetriever

    monkeypatch.setattr(hybrid_mod, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(hybrid_mod, "INDEX_MATRIX", tmp_path / "m.npy")
    monkeypatch.setattr(hybrid_mod, "INDEX_DOCS", tmp_path / "d.json")
    monkeypatch.setattr(hybrid_mod, "INDEX_META", tmp_path / "meta.json")

    class _FailEmbedLLM:
        class _Inner:
            bailian_embed_model = "stub"
        llm = _Inner()
        embedding_dim = 8
        def embed(self, texts, *, model=None):
            raise RuntimeError("offline")

    class _StubCache:
        def get_embedding(self, *a, **kw): return None
        def set_embedding(self, *a, **kw): pass

    sem = SemanticLayer(cfg.app.semantic_path)
    r = HybridRetriever(sem)
    r.llm = _FailEmbedLLM()  # type: ignore[assignment]
    r.cache = _StubCache()   # type: ignore[assignment]
    r.build()

    allowed = {HS_T}
    bundle = r.search("门店销售金额 销量", allowed_tables=allowed)
    assert bundle.metrics, "范围内应有指标召回"
    for c in bundle.metrics:
        md = sem.metric(c.name)
        assert md and md.table in allowed, f"召回了域外指标 {c.name}"
    for c in bundle.tables:
        assert c.name in allowed, f"召回了域外表 {c.name}"
    for c in bundle.dimensions:
        dd = sem.dimension(c.name)
        assert dd and any(t in allowed for t in dd.table_columns), f"召回了域外维度 {c.name}"
    for c in bundle.few_shots:
        intent = (c.payload or {}).get("intent") or {}
        t = intent.get("table") or (sem.metric(str(intent.get("metric") or "")).table
                                    if sem.metric(str(intent.get("metric") or "")) else "")
        if t:
            assert t in allowed, f"召回了域外 few-shot（table={t}）"

    # 不分域（None）→ 跨表候选仍然可见
    full = r.search("终端销售额", allowed_tables=None)
    assert any(sem.metric(c.name) and sem.metric(c.name).table == SUMMARY_T for c in full.metrics)

    # 空集 → 全类目零召回
    empty = r.search("终端销售额", allowed_tables=frozenset())
    assert not empty.metrics and not empty.tables and not empty.few_shots


# ===================================================== L2 串数据修复

def test_plan_signature_separates_users_after_perm_injection(perm_store):
    """复现已修复的 P0 bug：注入前两个用户的 plan 签名相同（会互相命中 L2），
    注入行级权限后签名必须分开；权限相同的用户仍然共享。"""
    from app.core.permissions import apply_to_plan

    perm_store.set_for_user("user_a", row_rules={"region": ["北一区"]}, allowed_tables=[SUMMARY_T])
    perm_store.set_for_user("user_b", row_rules={"region": ["南一区"]}, allowed_tables=[SUMMARY_T])
    perm_store.set_for_user("user_c", row_rules={"region": ["北一区"]}, allowed_tables=[SUMMARY_T])

    def fresh_plan() -> QueryPlan:
        return QueryPlan(
            metric="terminal_sale_amount_total", table=SUMMARY_T,
            time_range=TimeRange(kind=TimeKind.RELATIVE, period="last_month"),
        )

    pa, pb, pc = fresh_plan(), fresh_plan(), fresh_plan()
    assert pa.signature() == pb.signature(), "注入前签名相同 —— 这正是旧实现的串数据通道"

    pa = apply_to_plan(pa, user_id="user_a", is_admin=False)
    pb = apply_to_plan(pb, user_id="user_b", is_admin=False)
    pc = apply_to_plan(pc, user_id="user_c", is_admin=False)
    assert pa.signature() != pb.signature(), "行级权限不同的用户注入后签名必须分开"
    assert pa.signature() == pc.signature(), "权限完全相同的用户应继续共享 L2 缓存"


def test_out_of_scope_excluded_from_signature():
    p1 = QueryPlan(metric="m", table="t")
    p2 = QueryPlan(metric="m", table="t", out_of_scope=True, out_of_scope_reason="x")
    assert p1.signature() == p2.signature()
    rt = QueryPlan.from_dict(p2.to_dict())
    assert rt.out_of_scope and rt.out_of_scope_reason == "x"


# ===================================================== guard 按用户白名单

def test_guard_per_user_whitelist(cfg, semantic):
    guard = SQLGuard(allowed_tables=semantic.tables.keys(), cfg=cfg.guard, semantic_layer=semantic)
    sql = f"SELECT region_name, SUM(terminal_sale_amount) FROM {SUMMARY_T} GROUP BY region_name"
    assert guard.validate(sql).tables == [SUMMARY_T]          # 默认全语义层白名单：通过
    assert guard.validate(sql, allowed_tables={SUMMARY_T}).tables == [SUMMARY_T]
    with pytest.raises(GuardError):
        guard.validate(sql, allowed_tables={HS_T})            # 域外表：拒绝


# ===================================================== 超范围拒答

def test_out_of_scope_explicit_metric_mention(semantic):
    p = _mk_planner(semantic)
    allowed = frozenset({NEWCUST_T})
    reason = p._out_of_scope_reason("上月终端销售额多少", _bundle(), allowed, followup=False)
    assert "终端销售额" in reason, "显式点名域外指标必须给出含指标名的精确拒答"
    # 点名了域内指标 → 放行
    assert p._out_of_scope_reason("本月首购人数", _bundle(), allowed, followup=False) == ""
    # 追问 → 永不拒答（上一轮已证明在域内）
    assert p._out_of_scope_reason("上月终端销售额多少", _bundle(), allowed, followup=True) == ""
    # 不分域 → 永不拒答
    assert p._out_of_scope_reason("上月终端销售额多少", _bundle(), None, followup=False) == ""


def test_out_of_scope_low_retrieval_score(semantic, monkeypatch):
    p = _mk_planner(semantic)
    allowed = frozenset({NEWCUST_T})
    weak = _bundle(metrics=[_cand("metric", "first_purchase_num_total", "首购人数", 0.12, table=NEWCUST_T)])
    monkeypatch.setenv("DATACHAT_SCOPE_REJECT_THRESHOLD", "0.35")
    reason = p._out_of_scope_reason("库存周转天数怎么样", weak, allowed, followup=False)
    assert reason, "范围内全员低分必须拒答"
    monkeypatch.setenv("DATACHAT_SCOPE_REJECT_THRESHOLD", "0")
    assert p._out_of_scope_reason("库存周转天数怎么样", weak, allowed, followup=False) == "", \
        "阈值=0 必须关闭低分拒答"


# ===================================================== P0.5 全量表卡片

def test_table_cards_full_presentation(semantic, monkeypatch):
    from app.core.permissions import UserScope
    p = _mk_planner(semantic)
    monkeypatch.delenv("DATACHAT_FULL_TABLE_CARDS_MAX", raising=False)

    scope = UserScope(user_id="u", allowed_tables=frozenset({HS_T, NEWCUST_T}), fingerprint="x")
    block, full = p._table_cards(_bundle(), scope)
    assert full
    assert HS_T in block and NEWCUST_T in block
    assert SUMMARY_T not in block, "域外表绝不能出现在表卡片里"
    assert "状态=" in block and "粒度：" in block

    # 不分域：6 张表 ≤ 默认阈值 20 → 也走全量卡片
    block_all, full_all = p._table_cards(_bundle(), None)
    assert full_all and SUMMARY_T in block_all

    # 阈值压到 1 → 回退召回 top-k 行
    monkeypatch.setenv("DATACHAT_FULL_TABLE_CARDS_MAX", "1")
    fallback, full2 = p._table_cards(_bundle(tables=[_cand("table", HS_T, "火山POC明细销售", 0.5, grain="月-门店")]), scope)
    assert not full2 and HS_T in fallback


# ===================================================== P1.5 歧义澄清

def _close_bundle(semantic):
    return _bundle(metrics=[
        _cand("metric", "terminal_sale_amount_total", "终端销售额", 0.52, table=SUMMARY_T),
        _cand("metric", "shop_sale_amount_total", "门店销售金额", 0.47, table=HS_T),
    ])


def test_ambiguity_forces_clarify(semantic, monkeypatch):
    monkeypatch.delenv("DATACHAT_AMBIGUITY_GAP", raising=False)
    p = _mk_planner(semantic)
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY_T, confidence=0.9)
    out = p._maybe_ambiguity_clarify(plan, _close_bundle(semantic), "上个月卖得怎么样", inherit=False)
    assert out.needs_clarify, "top-2 分差 0.05 < 0.10 且未点名 → 必须澄清"
    assert len(out.clarify_options) == 2
    keys = {o["key"] for o in out.clarify_options}
    assert keys == {"terminal_sale_amount_total", "shop_sale_amount_total"}


def test_ambiguity_skips_when_user_named_metric(semantic, monkeypatch):
    monkeypatch.delenv("DATACHAT_AMBIGUITY_GAP", raising=False)
    p = _mk_planner(semantic)
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY_T)
    out = p._maybe_ambiguity_clarify(plan, _close_bundle(semantic), "上月终端销售额多少", inherit=False)
    assert not out.needs_clarify, "用户显式点名指标别名 → 不打扰"


def test_ambiguity_skips_on_inherit_and_big_gap(semantic, monkeypatch):
    monkeypatch.delenv("DATACHAT_AMBIGUITY_GAP", raising=False)
    p = _mk_planner(semantic)
    plan = QueryPlan(metric="terminal_sale_amount_total", table=SUMMARY_T)
    assert not p._maybe_ambiguity_clarify(plan, _close_bundle(semantic), "卖得怎么样", inherit=True).needs_clarify
    far = _bundle(metrics=[
        _cand("metric", "terminal_sale_amount_total", "终端销售额", 0.80, table=SUMMARY_T),
        _cand("metric", "shop_sale_amount_total", "门店销售金额", 0.30, table=HS_T),
    ])
    assert not p._maybe_ambiguity_clarify(plan, far, "卖得怎么样", inherit=False).needs_clarify
    monkeypatch.setenv("DATACHAT_AMBIGUITY_GAP", "0")
    assert not p._maybe_ambiguity_clarify(plan, _close_bundle(semantic), "卖得怎么样", inherit=False).needs_clarify


# ===================================================== P1 认证状态

def test_semantic_status_loading_defaults_draft(semantic):
    assert all(t.status in ("draft", "verified") for t in semantic.list_tables())
    assert all(m.status in ("draft", "verified") for m in semantic.list_metrics())


def test_semantic_editor_status_workflow(cfg, tmp_path):
    import shutil
    from app.core.semantic_editor import set_status, certification_overview, upsert_entity, list_entities
    work = tmp_path / "semantic.yaml"
    shutil.copyfile(cfg.app.semantic_path, work)

    ov = certification_overview(work)
    assert ov["stats"]["verified"] == 0 and ov["stats"]["draft"] > 0, "存量条目默认全是草稿"

    set_status(work, "metrics", "terminal_sale_amount_total", "verified")
    sem2 = SemanticLayer(work)
    assert sem2.metric("terminal_sale_amount_total").status == "verified"
    assert certification_overview(work)["stats"]["verified"] == 1

    # upsert 不带 status → 沿用已有状态；全新条目默认 draft
    body = dict(list_entities(work, "metrics")["terminal_sale_amount_total"])
    body.pop("status", None)
    body["description"] = "口径修订"
    upsert_entity(work, "metrics", "terminal_sale_amount_total", body)
    assert SemanticLayer(work).metric("terminal_sale_amount_total").status == "verified"

    with pytest.raises(ValueError):
        set_status(work, "metrics", "terminal_sale_amount_total", "certified!")
    with pytest.raises(ValueError):
        set_status(work, "metrics", "no_such_metric", "verified")


# ===================================================== P2 few-shot 飞轮

def test_fewshot_adopt_strips_permission_filters(tmp_path):
    from app.core.fewshot_store import FewShotStore
    fs = FewShotStore(tmp_path / "fewshots.db")
    plan = QueryPlan(
        metric="terminal_sale_amount_total", table=SUMMARY_T,
        filters=[
            PlanFilter(dimension="product_series", op="eq", values=["星飞帆"], raw="星飞帆"),
            PlanFilter(dimension="region", op="in", values=["北一区"], raw="(数据权限)"),
        ],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="last_month"),
    )
    assert fs.add_adopted("alice", "上月星飞帆终端销售额", plan.to_dict())
    hits = fs.search("星飞帆 上月 终端销售额", allowed_tables={SUMMARY_T})
    assert len(hits) == 1
    dims = [f["dimension"] for f in hits[0]["intent"]["filters"]]
    assert "product_series" in dims
    assert "region" not in dims, "行级权限注入的 filter 绝不能沉淀进 few-shot（会泄露给同表用户）"


def test_fewshot_scope_clarify_and_votes(tmp_path):
    from app.core.fewshot_store import FewShotStore
    fs = FewShotStore(tmp_path / "fewshots.db")
    good = QueryPlan(metric="first_purchase_num_total", table=NEWCUST_T).to_dict()
    assert fs.add_adopted("u1", "本月首购人数", good)
    # 澄清/无指标的 plan 不沉淀
    assert not fs.add_adopted("u1", "问题A", QueryPlan(metric="m", table="t", needs_clarify=True).to_dict())
    assert not fs.add_adopted("u1", "问题B", {"mode": "direct_sql"})
    # 分域检索：域外用户看不到
    assert fs.search("本月首购人数", allowed_tables={SUMMARY_T}) == []
    assert len(fs.search("本月首购人数", allowed_tables={NEWCUST_T})) == 1
    # 重复采纳 → 覆盖不重复
    assert fs.add_adopted("u2", "本月首购人数", good)
    assert len(fs.search("本月首购人数", allowed_tables=None)) == 1
    # 点踩入库但不参与召回
    fs.record_downvote("u1", "错误的回答", good)
    assert all("错误的回答" != h["question"] for h in fs.search("错误的回答", allowed_tables=None))
    st = fs.stats()
    assert st["adopted"] == 1 and st["downvoted"] == 1
