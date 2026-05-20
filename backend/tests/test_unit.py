"""Unit tests for DataChat — semantic layer, planner rules, compiler, guardrails.

These tests do NOT call the LLM or the database, so they are fast and deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import load_config, reset_for_tests  # noqa: E402
from app.core.semantic import SemanticLayer  # noqa: E402
from app.core.guard import SQLGuard, GuardError  # noqa: E402
from app.core.nl2sql.plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange  # noqa: E402
from app.core.nl2sql.compiler import PlanCompiler  # noqa: E402
from app.core.nl2sql.planner import Planner  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    reset_for_tests()
    return load_config(reload=True)


@pytest.fixture(scope="module")
def semantic(cfg):
    return SemanticLayer(cfg.app.semantic_path)


# --------------------------------------------------------- semantic layer

def test_semantic_layer_loads_metrics_and_dims(semantic: SemanticLayer):
    assert len(semantic.metrics) >= 19
    assert len(semantic.dimensions) >= 17
    assert len(semantic.tables) == 5
    assert len(semantic.calculations) >= 7


def test_retrieval_index_load_persist_roundtrip(cfg, tmp_path, monkeypatch):
    """retrieval_index/ 持久化往返：构建 → 落盘 → 重新加载，不联网。

    用 stub LLM 制造确定性向量，避免依赖真实百炼 API。同时验证 fingerprint
    机制：semantic.yaml 不变就吃缓存；改了就强制重建。
    """
    import json as _json
    import numpy as np
    from app.core.retrieval import hybrid as hybrid_mod
    from app.core.retrieval.hybrid import HybridRetriever

    # 把索引目录指到 tmp_path，避免污染仓库里真实的 retrieval_index/
    monkeypatch.setattr(hybrid_mod, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(hybrid_mod, "INDEX_MATRIX", tmp_path / "embed_matrix.npy")
    monkeypatch.setattr(hybrid_mod, "INDEX_DOCS", tmp_path / "docs.json")
    monkeypatch.setattr(hybrid_mod, "INDEX_META", tmp_path / "meta.json")

    class _StubLLM:
        class _Inner:
            bailian_embed_model = "stub-embed-v1"
        llm = _Inner()
        embedding_dim = 8

        def embed(self, texts, *, model=None):
            # 确定性："i 位置 hash(text)→向量"，便于断言
            out = []
            for t in texts:
                h = hash(t) & 0xFFFFFFFF
                vec = np.array([(h >> (i * 4)) & 0xF for i in range(8)], dtype=float)
                if vec.sum() == 0:
                    vec[0] = 1.0
                out.append(list(vec / np.linalg.norm(vec)))
            return out

    class _StubCache:
        def get_embedding(self, *a, **kw): return None
        def set_embedding(self, *a, **kw): pass

    sem = SemanticLayer(cfg.app.semantic_path)
    r = HybridRetriever(sem)
    r.llm = _StubLLM()      # type: ignore[assignment]
    r.cache = _StubCache()  # type: ignore[assignment]

    # 第 1 次构建：磁盘空 → 走 API（_StubLLM）→ 落盘
    r.build()
    assert r._embed_matrix is not None
    assert (tmp_path / "embed_matrix.npy").exists()
    assert (tmp_path / "docs.json").exists()
    assert (tmp_path / "meta.json").exists()
    meta = _json.loads((tmp_path / "meta.json").read_text())
    assert meta["model"] == "stub-embed-v1"
    assert meta["doc_count"] == len(r._docs) > 0
    n_docs = meta["doc_count"]
    fp1 = meta["semantic_hash"]

    # 第 2 次构建：磁盘已有 + fingerprint 匹配 → 走加载路径，不调 stub.embed
    calls = {"embed": 0}
    original = _StubLLM.embed
    def counting_embed(self, texts, *, model=None):
        calls["embed"] += 1
        return original(self, texts, model=model)
    _StubLLM.embed = counting_embed  # type: ignore[assignment]

    r2 = HybridRetriever(sem)
    r2.llm = _StubLLM()      # type: ignore[assignment]
    r2.cache = _StubCache()  # type: ignore[assignment]
    r2.build()
    assert calls["embed"] == 0, "fingerprint 匹配时不应该再调 embedding API"
    assert r2._embed_matrix is not None
    assert r2._embed_matrix.shape[0] == n_docs
    assert len(r2._docs) == n_docs

    # 第 3 次构建：模拟 semantic.yaml 变了 → fingerprint 失效 → 必须重建
    r3 = HybridRetriever(sem)
    r3.llm = _StubLLM()      # type: ignore[assignment]
    r3.cache = _StubCache()  # type: ignore[assignment]
    # 篡改 fingerprint：直接改 meta.json 的 semantic_hash
    bad_meta = dict(meta); bad_meta["semantic_hash"] = "deadbeefdeadbeef"
    (tmp_path / "meta.json").write_text(_json.dumps(bad_meta))
    calls["embed"] = 0
    r3.build()
    assert calls["embed"] >= 1, "fingerprint 不匹配必须重新调 embedding API"
    # 重建后 meta.json 又写回当前 fingerprint
    meta_after = _json.loads((tmp_path / "meta.json").read_text())
    assert meta_after["semantic_hash"] == fp1


def test_semantic_schema_overrides_from_env(cfg, monkeypatch):
    """生产服务器业务库名 (MYSQL_DATABASE / DB_NAME) 必须覆盖 semantic.yaml 里的本地默认值 chatbi，
    否则 compiler 会输出 `FROM chatbi.xxx` 跑到不存在的库——这是上线必踩坑。"""
    # 1) env 设置后，所有表的 schema 都换成 env 值
    monkeypatch.setenv("MYSQL_DATABASE", "hs_poc")
    sl = SemanticLayer(cfg.app.semantic_path)
    assert sl.tables, "semantic 至少要加载到一张表"
    for t in sl.tables.values():
        assert t.schema == "hs_poc", f"{t.name}.schema 未被 env 覆盖: {t.schema!r}"
        assert t.full_name.startswith("hs_poc.")
    # compiler 实际产出的 SQL 必须含 hs_poc.，不能含 chatbi.
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        table="ads_bi_month_shop_item_dan_summary_df",
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]),
        calculation="rank",
        order_by=[OrderBy(field="terminal_sale_amount_total", dir="desc")],
        limit=10,
    )
    sql, _ = PlanCompiler(sl, default_limit=500).compile(plan)
    assert "hs_poc" in sql and "chatbi" not in sql, f"SQL 仍含 chatbi: {sql}"

    # 2) env 缺失则回退 yaml（本地 dev 默认 chatbi）
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.delenv("DATACHAT_BUSINESS_DB", raising=False)
    sl2 = SemanticLayer(cfg.app.semantic_path)
    for t in sl2.tables.values():
        assert t.schema == "chatbi"


def test_metric_alias_resolves(semantic: SemanticLayer):
    assert semantic.find_metric_by_alias("终端销售额").name == "terminal_sale_amount_total"
    assert semantic.find_metric_by_alias("销售额").name == "terminal_sale_amount_total"
    assert semantic.find_metric_by_alias("达成率").name == "shop_sale_achievement_rate"
    assert semantic.find_metric_by_alias("60天复购率").name == "repurchase_rate_60d"
    assert semantic.find_metric_by_alias("潜客转新率").name == "potential_to_new_rate"


def test_dimension_alias_resolves(semantic: SemanticLayer):
    assert semantic.find_dimension_by_alias("大区").name == "region"
    assert semantic.find_dimension_by_alias("省区").name == "sub_region"
    assert semantic.find_dimension_by_alias("段位").name == "item_dan"


# --------------------------------------------------------- compiler

def test_compile_basic_no_group(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500)
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
    )
    sql, meta = pc.compile(plan)
    assert "SUM(terminal_sale_amount)" in sql
    assert "WHERE" in sql
    assert "GROUP BY" not in sql
    assert "LIMIT" not in sql or "LIMIT 500" not in sql  # no group → no default limit
    assert meta["metric"] == "terminal_sale_amount_total"


def test_compile_group_by_region(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=100)
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
        calculation="rank",
        limit=10,
    )
    sql, _ = pc.compile(plan)
    assert "lev2_name" in sql
    assert "GROUP BY" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 10" in sql


def test_compile_filter_in(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500)
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        filters=[PlanFilter(dimension="region", op="in", values=["东一区", "北一区"])],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
    )
    sql, _ = pc.compile(plan)
    assert "lev2_name` IN" in sql
    assert "'东一区'" in sql and "'北一区'" in sql


def test_compile_cross_year_last_n_months(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500)
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        calculation="trend",
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="last_n_months", n=6),
    )
    sql, _ = pc.compile(plan)
    # Cross-year window must include both years 2025 and 2026 when latest is 2026-04
    assert "2025" in sql
    assert "2026" in sql
    # Should be tuple-IN form
    assert "(`year`, `month`) IN" in sql


def test_compile_ratio(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500)
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        calculation="ratio",
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
    )
    sql, meta = pc.compile(plan)
    assert "OVER ()" in sql
    assert any(k.endswith("_ratio") for k in meta["columns"].keys())


def test_compile_yoy_growth(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500, today=date(2026, 4, 1))
    plan = QueryPlan(
        metric="terminal_sale_amount_total",
        calculation="yoy_growth",
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
    )
    sql, meta = pc.compile(plan)
    assert "_growth" in sql
    assert "_current" in sql
    assert "_previous" in sql
    assert "2025" in sql and "2026" in sql


def test_compile_rejects_unknown_metric(semantic: SemanticLayer):
    pc = PlanCompiler(semantic)
    plan = QueryPlan(metric="nonexistent_metric")
    with pytest.raises(Exception):
        pc.compile(plan)


def test_compile_achievement_rate_uses_target_table(semantic: SemanticLayer):
    pc = PlanCompiler(semantic, default_limit=500)
    plan = QueryPlan(
        metric="shop_sale_achievement_rate",
        group_by=["sub_region"],
        time_range=TimeRange(kind=TimeKind.RELATIVE, period="this_month"),
        calculation="rank",
        limit=3,
    )
    sql, meta = pc.compile(plan)
    assert "ads_bi_month_shop_item_dan_target_summary_df" in sql
    assert "NULLIF(SUM(shop_sale_target), 0)" in sql
    assert "lev3_name" in sql
    assert "LIMIT 3" in sql


# --------------------------------------------------------- guard

def test_guard_allows_select(semantic: SemanticLayer):
    guard = SQLGuard(allowed_tables=semantic.tables.keys())
    sql = "SELECT lev2_name, SUM(terminal_sale_amount) FROM ads_bi_month_shop_item_dan_summary_df GROUP BY lev2_name"
    rep = guard.validate(sql)
    assert rep.has_limit  # auto-added
    assert "ads_bi_month_shop_item_dan_summary_df" in rep.tables


@pytest.mark.parametrize("bad", [
    "INSERT INTO ads_bi_hs_sale_info_df VALUES(1)",
    "DELETE FROM ads_bi_hs_sale_info_df",
    "UPDATE ads_bi_hs_sale_info_df SET shop_sale_qty=0",
    "DROP TABLE ads_bi_hs_sale_info_df",
    "SELECT 1; DELETE FROM ads_bi_hs_sale_info_df",
    "SELECT * FROM ads_bi_hs_sale_info_df",  # SELECT *
])
def test_guard_blocks_dangerous(semantic: SemanticLayer, bad: str):
    guard = SQLGuard(allowed_tables=semantic.tables.keys())
    with pytest.raises(GuardError):
        guard.validate(bad)


def test_guard_blocks_unauthorized_table(semantic: SemanticLayer):
    guard = SQLGuard(allowed_tables=["ads_bi_month_shop_item_dan_summary_df"])
    with pytest.raises(GuardError):
        guard.validate("SELECT a FROM unauthorized_table")


# --------------------------------------------------------- planner rules (no LLM)

def test_planner_extract_rule_seed_period_and_calc(semantic: SemanticLayer):
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    seed = p._extract_rule_seed("本月各大区销售额排名")
    assert seed["period"] == "this_month"
    assert seed["calculation"] == "rank"
    assert "region" in seed["group_by_hint"]


def test_planner_extract_rule_seed_top_n(semantic: SemanticLayer):
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    seed = p._extract_rule_seed("销售目标完成率排前三的省区")
    assert seed["calculation"] == "rank"
    assert seed["rank_n"] == 3
    assert "sub_region" in seed["group_by_hint"]


def test_planner_extract_rule_seed_filter_hits(semantic: SemanticLayer):
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    seed = p._extract_rule_seed("北一区60天复购率")
    dims = {h["dimension"] for h in seed["filter_hits"]}
    assert "region" in dims


def test_planner_extract_rule_seed_yoy(semantic: SemanticLayer):
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    seed = p._extract_rule_seed("各大区销售额同比增长")
    assert seed["calculation"] == "yoy_growth"
    assert "region" in seed["group_by_hint"]


def test_planner_no_spurious_single_char_filter(semantic: SemanticLayer):
    """回归：'分别是多少' 里的 '是' 不能命中 is_guide_shop 样例值('是'/'否')，
    否则会生成 `WHERE big_system_channel_name='是'` 这种脏条件。"""
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    q = "2025年1月各大区门店销售金额、门店销售目标和销售达成率分别是多少？按达成率从低到高排序。"
    seed = p._extract_rule_seed(q)
    assert seed["filter_hits"] == [], f"出现误判过滤: {seed['filter_hits']}"
    assert "region" in seed["group_by_hint"]
    assert seed["absolute"] == {"year": "2025", "months": ["01"]}


def test_planner_remap_dim_is_family_safe(semantic: SemanticLayer):
    """回归：_remap_dim 不得把无关维度（is_guide_shop）乱配到目标表的渠道列；
    渠道族内的安全改写要保留。"""
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    tgt = "ads_bi_month_shop_item_dan_target_summary_df"
    assert p._remap_dim("is_guide_shop", tgt) is None
    assert p._remap_dim("channel_type", tgt) == "big_system_channel"
    assert p._remap_dim("region", tgt) == "region"


def test_planner_extract_rule_seed_quarter(semantic: SemanticLayer):
    p = Planner(semantic, retriever=type("R", (), {"search": lambda self, q: None})(), llm=None)  # type: ignore
    seed = p._extract_rule_seed("2026年1季度销售情况")
    assert seed["absolute"]["year"] == "2026"
    assert seed["absolute"]["months"] == ["01", "02", "03"]


# --------------------------------------------------------- plan signature

def test_plan_clarify_options_normalized_no_str_get_crash():
    """回归(P1)：LLM 返回 clarify_options:['xxx'] 等非 dict，必须被规范化为
    [{label,key,...}]，杜绝下游 'str' object has no attribute 'get'。"""
    p = QueryPlan.from_dict({
        "metric": "m",
        "clarify_options": ["只看东一区", 123, {"label": "按省区", "key": "sub_region"}, {"x": 1}, ""],
    })
    assert all(isinstance(o, dict) and "label" in o for o in p.clarify_options)
    assert [o["label"] for o in p.clarify_options] == ["只看东一区", "123", "按省区"]
    # 非 list 时安全降级
    assert QueryPlan.from_dict({"clarify_options": "oops"}).clarify_options == []


def test_plan_signature_stable():
    p1 = QueryPlan(metric="m1", group_by=["a", "b"])
    p2 = QueryPlan(metric="m1", group_by=["a", "b"])
    p3 = QueryPlan(metric="m1", group_by=["b", "a"])
    assert p1.signature() == p2.signature()
    assert p1.signature() != p3.signature()


# --------------------------------------------------------- auth (bcrypt+JWT)

def test_auth_create_authenticate_token_roundtrip(tmp_path, monkeypatch):
    """Construct a private AuthStore in tmp_path; do NOT touch the global singleton
    or os.environ — that would leak into test_api.py."""
    from app.core import auth as auth_mod
    store = auth_mod.AuthStore(path=tmp_path / "auth.db", secret="unit-test-secret")

    admin = store.get_by_username("admin")
    assert admin is not None and admin.role == "admin"
    # admin email default 必须自动填入（不耦合具体地址，避免泄漏真实邮箱到源码）
    assert admin.email and "@" in admin.email
    from app.core.auth import DEFAULT_ADMIN_EMAIL
    assert admin.email == DEFAULT_ADMIN_EMAIL

    # 弱密码必须被拒
    with pytest.raises(auth_mod.AuthError):
        store.create_user("weak", "abc123", role="user")
    # 强密码 + 邮箱
    u = store.create_user("alice", "Strong@2026", role="user", email="alice@feihe.com")
    assert u.username == "alice" and u.role == "user"
    assert u.email == "alice@feihe.com"

    me = store.authenticate("alice", "Strong@2026")
    assert me.username == "alice"

    with pytest.raises(auth_mod.AuthError):
        store.authenticate("alice", "WRONG")

    tok = store.issue_token(me)
    me2 = store.verify_token(tok)
    assert me2.id == me.id and me2.username == "alice"

    with pytest.raises(auth_mod.AuthError):
        store.verify_token(tok + "garbage")

    # 改邮箱
    store.set_email("alice", "alice2@feihe.com")
    assert store.get_by_username("alice").email == "alice2@feihe.com"
    with pytest.raises(auth_mod.AuthError):
        store.set_email("alice", "not-an-email")

    store.delete_user("alice")
    assert store.get_by_username("alice") is None


def test_company_auth_store_email_roundtrip(tmp_path):
    """P1-3：公司业务库 users 表必须能保存并返回真实 email；
    非邮箱 username（如 'alice'）传入 email 后，创建/列表/登录/改邮箱全链路不得清空。"""
    from sqlalchemy import create_engine

    from app.core import auth as auth_mod
    from app.core.user_directory import CompanyAuthStore

    engine = create_engine(f"sqlite:///{tmp_path}/company.db")
    store = CompanyAuthStore(engine=engine, table_name="users", secret="company-test-secret")
    assert store._has_email is True  # sqlite 新建表必带 email 列

    # 非邮箱 username + 显式 email
    created = store.create_user("alice", "Strong@2026", role="user", email="alice@feihe.com")
    assert created.email == "alice@feihe.com", "创建返回值 email 丢失"

    # 列表
    listed = {u.username: u for u in store.list_users()}
    assert listed["alice"].email == "alice@feihe.com", "list_users email 丢失"

    # 登录（authenticate）+ /api/me 等价路径（verify_token）
    me = store.authenticate("alice", "Strong@2026")
    assert me.email == "alice@feihe.com", "登录态 email 丢失"
    me2 = store.verify_token(store.issue_token(me))
    assert me2.email == "alice@feihe.com", "/api/me 等价路径 email 丢失"

    # 改邮箱后仍正确，且非邮箱 username 不被清空
    store.set_email("alice", "alice2@feihe.com")
    assert store.get_by_username("alice").email == "alice2@feihe.com"
    assert store.get_by_username("alice").username == "alice"  # username 未被 email 覆盖

    # 内置 admin（非邮箱 username）回退到默认 admin email，不为空
    admin = store.get_by_username(auth_mod.DEFAULT_ADMIN_USERNAME)
    assert admin is not None and admin.email

    with pytest.raises(auth_mod.AuthError):
        store.delete_user("admin")

    # set_password 强度校验
    with pytest.raises(auth_mod.AuthError):
        store.set_password("admin", "weakpwd")
    store.set_password("admin", "NewStrong@2026")
    assert store.authenticate("admin", "NewStrong@2026").role == "admin"


def test_company_auth_store_readonly_preexisting_table(tmp_path):
    """部署回归：业务库账号只有 SELECT、users 表已由公司预建（且无 email 列）。
    CompanyAuthStore 必须：不跑任何 DDL、能只读认证既有用户、email 优雅降级。"""
    import bcrypt
    from sqlalchemy import create_engine, text

    from app.core.user_directory import CompanyAuthStore

    db = f"{tmp_path}/preexist.db"
    eng = create_engine(f"sqlite:///{db}")
    # 模拟“公司已存在的 users 表”：无 email 列、外部预置一个 bcrypt 用户
    pwd_hash = bcrypt.hashpw(b"Boss@2026", bcrypt.gensalt(rounds=10)).decode()
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "username VARCHAR(255) UNIQUE, display_name VARCHAR(255), "
            "password_hash VARCHAR(255), role VARCHAR(32), "
            "must_change_password INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, "
            "created_at VARCHAR(64), last_login VARCHAR(64), "
            "feishu_user_id VARCHAR(255) DEFAULT '', org_code VARCHAR(255) DEFAULT '', "
            "department VARCHAR(255) DEFAULT '')"
        ))
        c.execute(text(
            "INSERT INTO users(username, display_name, password_hash, role, is_active, created_at) "
            "VALUES ('boss@feihe.com','Boss',:h,'admin',1,'2026-01-01T00:00:00+00:00')"
        ), {"h": pwd_hash})

    store = CompanyAuthStore(engine=eng, table_name="users", secret="ro-test-secret")
    # 表已存在 → 不应新增 email 列（即未跑 ALTER/CREATE）
    assert store._has_email is False
    # 只读认证既有用户成功；email 由邮箱型 username 推导
    u = store.authenticate("boss@feihe.com", "Boss@2026")
    assert u.role == "admin" and u.email == "boss@feihe.com"
    assert store.verify_token(store.issue_token(u)).username == "boss@feihe.com"
    # 列结构未被改动（仍无 email 列）
    from sqlalchemy import inspect as _sa
    assert "email" not in {col["name"] for col in _sa(eng).get_columns("users")}


def test_generate_initial_password_is_strong():
    from app.core.auth import generate_initial_password, is_password_strong
    for _ in range(10):
        pwd = generate_initial_password()
        ok, _ = is_password_strong(pwd)
        assert ok, f"generated pwd not strong: {pwd}"


def test_password_strength_rules():
    from app.core.auth import is_password_strong
    assert is_password_strong("")[0] is False
    assert is_password_strong("short")[0] is False
    assert is_password_strong("12345678")[0] is False
    assert is_password_strong("password")[0] is False
    assert is_password_strong("aaaaaaaa")[0] is False
    assert is_password_strong("Abcd1234")[0] is True
    assert is_password_strong("Strong@2026")[0] is True


def test_direct_sql_trigger_detection():
    from app.core.direct_sql import should_use_direct_sql
    # 显式 SQL 关键词
    assert should_use_direct_sql("请直接返回 SQL")
    assert should_use_direct_sql("请生成可执行的 MySQL")
    # 多表（≥3）
    assert should_use_direct_sql("用 ads_a_df, ads_b_df, ads_c_df 三张表分析")
    # 普通问题
    assert not should_use_direct_sql("本月销售额")


def test_permission_inject_row_filters(tmp_path, monkeypatch):
    """审计 P0 修复：行级权限必须强注入到任何 SQL。"""
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(tmp_path / "perm.db"))
    from app.core import permissions as perm_mod
    perm_mod._store_singleton = None
    store = perm_mod.get_permissions_store()

    # mock semantic layer
    class FakeDim:
        def __init__(self, name, cols):
            self.name = name
            self.table_columns = cols
    class FakeSemantic:
        def dimension(self, name):
            return {"region": FakeDim("region", {"sales_tbl": "lev2_name"})}.get(name)

    uid = "user-1"
    store.set_for_user(uid, row_rules={"region": ["北一区"]})

    sql_in = "SELECT lev2_name, SUM(amount) FROM sales_tbl GROUP BY lev2_name"
    sql_out = perm_mod.inject_row_filters_into_sql(sql_in, user_id=uid, is_admin=False, semantic_layer=FakeSemantic())
    assert "lev2_name" in sql_out and "北一区" in sql_out, f"权限未注入: {sql_out}"

    # admin 跳过
    sql_admin = perm_mod.inject_row_filters_into_sql(sql_in, user_id=uid, is_admin=True, semantic_layer=FakeSemantic())
    assert sql_admin == sql_in
