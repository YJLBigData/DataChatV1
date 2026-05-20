"""DataChat 修复验证测试 —— 安全 + 多轮上下文继承。

全部离线确定性：不调用真实 LLM、不连真实 MySQL。
"""
from __future__ import annotations

import base64
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.config import load_config, reset_for_tests  # noqa: E402
from app.core.semantic import SemanticLayer  # noqa: E402
from app.core.nl2sql.plan import OrderBy, PlanFilter, QueryPlan, TimeKind, TimeRange  # noqa: E402
from app.core.nl2sql.compiler import PlanCompiler  # noqa: E402
from app.core.nl2sql.planner import Planner  # noqa: E402
from app.core.retrieval import RetrievalBundle  # noqa: E402


# ============================================================ deps
def test_security_deps_importable():
    import redis  # noqa: F401
    import sqlglot  # noqa: F401
    assert True


# ============================================================ semantic / planner fixtures
@pytest.fixture(scope="module")
def semantic():
    reset_for_tests()
    cfg = load_config(reload=True)
    return SemanticLayer(cfg.app.semantic_path)


@pytest.fixture(scope="module")
def planner(semantic):
    class _StubRetriever:
        def search(self, q, **kw):
            return RetrievalBundle(metrics=[], dimensions=[], tables=[], few_shots=[], elapsed_ms=0)

        def build(self):
            return None

    return Planner(semantic, retriever=_StubRetriever(), llm=object())


def _inherit(planner, semantic, prev: QueryPlan, followup_q: str) -> QueryPlan:
    """模拟"LLM 丢了上下文（返回空 plan）"后，确定性继承层应纠回上文。"""
    assert planner._looks_like_followup(followup_q, prev) is True
    rule_seed = planner._extract_rule_seed(followup_q, today=date(2026, 4, 1))
    empty_bundle = RetrievalBundle(metrics=[], dimensions=[], tables=[], few_shots=[], elapsed_ms=0)
    return planner._validate_and_repair(
        QueryPlan(), empty_bundle, rule_seed, today=date(2026, 4, 1),
        previous_plan=prev, followup=True, question=followup_q,
    )


TARGET = "ads_bi_month_shop_item_dan_target_summary_df"
SUMMARY = "ads_bi_month_shop_item_dan_summary_df"
POTENTIAL = "ads_precision_nutrition_potential_total_df"


def _prev_guodan(semantic) -> QueryPlan:
    return QueryPlan(
        metric="gd_achievement_rate",
        extra_metrics=["gd_amount_actual_total", "gd_target_total"],
        table=TARGET,
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]),
        order_by=[OrderBy(field="gd_achievement_rate", dir="desc")],
    )


# ============================================================ 上下文：测试 2（核心回归）
def test_ctx2_followup_keeps_table_metric_time(planner, semantic):
    prev = _prev_guodan(semantic)
    plan = _inherit(planner, semantic, prev, "把东一区按渠道拆开看。")
    # 表/指标/时间必须继承
    assert plan.table == TARGET, f"表被错误切换: {plan.table}"
    assert plan.metric == "gd_achievement_rate"
    assert set(plan.extra_metrics) == {"gd_amount_actual_total", "gd_target_total"}
    assert plan.time_range.kind == TimeKind.ABSOLUTE
    assert plan.time_range.year == "2025" and plan.time_range.months == ["01"]
    # 维度切到（大系统）渠道，过滤东一区
    assert "big_system_channel" in plan.group_by
    fdims = {f.dimension: f for f in plan.filters}
    assert "region" in fdims and fdims["region"].values == ["东一区"]

    sql, _ = PlanCompiler(semantic, default_limit=500).compile(plan)
    assert TARGET in sql
    assert "terminal_sale_amount" not in sql, "禁止串到终端销售口径"
    assert "2026" not in sql and "'2025'" in sql and "'01'" in sql, "时间必须仍是 2025-01"
    assert "big_system_channel_name" in sql
    assert "lev2_name` = '东一区'" in sql


# ============================================================ 上下文：测试 1（升维到省区）
def test_ctx1_drill_to_subregion(planner, semantic):
    prev = QueryPlan(
        metric="shop_sale_achievement_rate",
        extra_metrics=["shop_sale_amount_actual_total", "shop_sale_target_total"],
        table=TARGET, group_by=["region"],
        time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]),
        order_by=[OrderBy(field="shop_sale_achievement_rate", dir="asc")],
    )
    plan = _inherit(planner, semantic, prev, "只看东一区，拆到省区层级。")
    assert plan.table == TARGET
    assert plan.metric == "shop_sale_achievement_rate"
    assert plan.time_range.year == "2025" and plan.time_range.months == ["01"]
    assert "sub_region" in plan.group_by and "region" not in plan.group_by
    assert any(f.dimension == "region" and f.values == ["东一区"] for f in plan.filters)
    sql, _ = PlanCompiler(semantic, default_limit=500).compile(plan)
    assert TARGET in sql and "lev3_name" in sql and "terminal_sale_amount" not in sql


# ============================================================ 上下文：测试 8（潜客口径）
def test_ctx8_potential_drilldown(planner, semantic):
    prev = QueryPlan(
        metric="potential_to_new_rate",
        extra_metrics=["potential_num_total", "potential_to_new_num_total"],
        table=POTENTIAL, group_by=["region"],
        time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]),
        calculation="rank", limit=5,
        order_by=[OrderBy(field="potential_to_new_rate", dir="asc")],
    )
    plan = _inherit(planner, semantic, prev, "下钻到省区，并只看东一区。")
    assert plan.table == POTENTIAL
    assert plan.metric == "potential_to_new_rate"
    assert plan.time_range.year == "2025"
    assert "sub_region" in plan.group_by
    assert any(f.dimension == "region" and f.values == ["东一区"] for f in plan.filters)
    sql, _ = PlanCompiler(semantic, default_limit=500).compile(plan)
    assert POTENTIAL in sql and "terminal_sale_amount" not in sql


# ============================================================ 追问识别：独立问句不继承
def test_followup_discrimination(planner, semantic):
    prev = _prev_guodan(semantic)
    # 带明确时间口径的标准问句 → 独立新问题
    assert planner._looks_like_followup(
        "2025年1月各大区门店销售金额、门店销售目标和销售达成率分别是多少？按达成率从低到高排序。",
        prev,
    ) is False
    # 没有上一轮 → 不是追问
    assert planner._looks_like_followup("把东一区按渠道拆开看。", None) is False
    # 典型追问
    for q in ["只看东一区，拆到省区层级。", "继续下钻到省区。", "按大区拆开看。",
              "东一区表现怎么样？", "只看转新率最低的3个大区。", "把这3个大区下钻到省区。"]:
        assert planner._looks_like_followup(q, prev) is True, q


# ============================================================ 字段级权限 fail closed
def _fake_semantic_for_perm():
    class FakeDim:
        def __init__(self, name, cols):
            self.name = name
            self.table_columns = cols

    class FakeSem:
        def dimension(self, n):
            return {"region": FakeDim("region", {"sales_tbl": "lev2_name"})}.get(n)

    return FakeSem()


def test_field_permission_blocks_unauthorized_and_allows_authorized(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(tmp_path / "p.db"))
    from app.core import permissions as perm
    perm._store_singleton = None
    store = perm.get_permissions_store()
    store.set_for_user("u1", allowed_columns={"sales_tbl": ["lev2_name", "amount"]})
    # 授权列通过
    perm.validate_sql_columns(
        "SELECT lev2_name, amount FROM sales_tbl", user_id="u1", is_admin=False,
        semantic_layer=None,
    )
    # 未授权列拒绝
    with pytest.raises(perm.PermissionDenied):
        perm.validate_sql_columns(
            "SELECT lev2_name, secret_cost FROM sales_tbl", user_id="u1", is_admin=False,
            semantic_layer=None,
        )
    # admin 跳过
    perm.validate_sql_columns(
        "SELECT secret_cost FROM sales_tbl", user_id="u1", is_admin=True, semantic_layer=None,
    )


def test_field_permission_fail_closed_on_parse_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(tmp_path / "p2.db"))
    from app.core import permissions as perm
    perm._store_singleton = None
    perm.get_permissions_store().set_for_user("u1", allowed_columns={"sales_tbl": ["lev2_name"]})
    import sqlglot
    monkeypatch.setattr(sqlglot, "parse_one", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(perm.PermissionDenied):
        perm.validate_sql_columns(
            "SELECT lev2_name FROM sales_tbl", user_id="u1", is_admin=False, semantic_layer=None,
        )


# ============================================================ 行级权限注入（可执行 + 不漏）
@pytest.mark.parametrize("sql_in", [
    "SELECT lev2_name, SUM(amount) AS a FROM sales_tbl",
    "SELECT lev2_name, SUM(amount) AS a FROM sales_tbl WHERE amount > 0",
    "SELECT lev2_name, SUM(amount) AS a FROM sales_tbl GROUP BY lev2_name",
    "SELECT lev2_name, SUM(amount) AS a FROM sales_tbl GROUP BY lev2_name ORDER BY a DESC LIMIT 10",
])
def test_row_permission_injection_executable(tmp_path, monkeypatch, sql_in):
    import sqlglot
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(tmp_path / "r.db"))
    from app.core import permissions as perm
    perm._store_singleton = None
    perm.get_permissions_store().set_for_user("u1", row_rules={"region": ["北一区"]})
    out = perm.inject_row_filters_into_sql(
        sql_in, user_id="u1", is_admin=False, semantic_layer=_fake_semantic_for_perm(),
    )
    # 仍是单条可解析 SELECT，权限条件已 AND 进 WHERE，原结构保留
    tree = sqlglot.parse_one(out, dialect="mysql")
    assert tree is not None
    assert "北一区" in out and "lev2_name" in out
    if "GROUP BY" in sql_in:
        assert "GROUP BY" in out
    if "ORDER BY" in sql_in:
        assert "ORDER BY" in out and "LIMIT" in out
    # admin 不改写
    assert perm.inject_row_filters_into_sql(
        sql_in, user_id="u1", is_admin=True, semantic_layer=_fake_semantic_for_perm()
    ) == sql_in


# ============================================================ query_log 旧库幂等迁移
def test_query_log_legacy_migration(tmp_path, monkeypatch):
    db = tmp_path / "query_log.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE query_log (id TEXT PRIMARY KEY, trace_id TEXT, question TEXT, created_at REAL);"
        "INSERT INTO query_log VALUES ('x','t','old-q', 1.0);"
    )
    con.close()
    monkeypatch.setenv("DATACHAT_QUERY_LOG_DB", str(db))
    from app.core import query_log as ql
    ql._store_singleton = None
    store = ql.get_query_log_store()  # 触发迁移
    store.record(trace_id="t2", user_id="u", username="alice", conversation_id="c",
                 question="新问题", plan={"metric": "m", "table": "t"}, sql="SELECT 1",
                 rows=1, elapsed_ms=5, cached=False, needs_clarify=False, error="")
    items, total = store.list(limit=10)
    assert total >= 2
    assert any(it["username"] == "alice" for it in items)
    # 迁移幂等：再次初始化不报错、不清空
    ql._store_singleton = None
    store2 = ql.get_query_log_store()
    _, total2 = store2.list(limit=10)
    assert total2 == total


def test_query_log_integer_id_legacy_rebuild(tmp_path, monkeypatch):
    db = tmp_path / "query_log_old_integer.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE query_log (
            id INTEGER PRIMARY KEY,
            trace_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            raw_input TEXT,
            metric TEXT,
            status TEXT NOT NULL,
            elapsed_ms FLOAT,
            created_at TEXT NOT NULL,
            result_row_count INTEGER,
            query_plan_json TEXT
        );
        INSERT INTO query_log(
            id, trace_id, user_id, raw_input, metric, status, elapsed_ms,
            created_at, result_row_count, query_plan_json
        ) VALUES (
            1, 'legacy-trace', 'default', '旧问题', 'terminal_sale_amount_total',
            'ok', 12.5, '2026-05-07T02:06:20.201704+00:00', 8, '{"metric":"m"}'
        );
        """
    )
    con.close()
    monkeypatch.setenv("DATACHAT_QUERY_LOG_DB", str(db))
    from app.core import query_log as ql
    ql._store_singleton = None
    store = ql.get_query_log_store()
    store.record(trace_id="new-trace", user_id="u", username="alice", conversation_id="c",
                 question="新问题", plan={"metric": "m", "table": "t"}, sql="SELECT 1",
                 rows=1, elapsed_ms=5, cached=False, needs_clarify=False, error="")
    items, total = store.list(limit=10)
    assert total == 2
    assert any(it["trace_id"] == "legacy-trace" and it["question"] == "旧问题" for it in items)
    assert any(it["trace_id"] == "new-trace" and it["username"] == "alice" for it in items)


# ============================================================ JWT_SECRET 策略
def test_jwt_secret_policy(monkeypatch):
    from app.core.auth import resolve_jwt_secret
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        resolve_jwt_secret()
    monkeypatch.setenv("JWT_SECRET", "datachat-local-dev-secret")  # 弱默认
    with pytest.raises(RuntimeError):
        resolve_jwt_secret()
    monkeypatch.setenv("JWT_SECRET", "x" * 40)  # 强随机长度
    assert resolve_jwt_secret() == "x" * 40
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert resolve_jwt_secret() == "datachat-local-dev-secret"  # 本地放行


# ============================================================ must_change_password 强制
def test_must_change_password_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATACHAT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.delenv("USER_DIRECTORY", raising=False)
    monkeypatch.delenv("DB_USERS_ENABLED", raising=False)
    import app.core.auth as auth
    auth._store_singleton = None
    store = auth.get_auth_store()
    store.create_user("temp1", "Init@2026ok", role="user", must_change_password=True)

    # 登录返回真实 must_change_password=True
    u = store.authenticate("temp1", "Init@2026ok")
    assert u.must_change_password is True

    from starlette.requests import Request
    from fastapi import HTTPException
    from app.main import require_user

    def _req(path):
        return Request({"type": "http", "path": path, "headers": [], "query_string": b"", "method": "GET"})

    token = store.issue_token(u)
    # 未改密访问核心接口 → 403
    with pytest.raises(HTTPException) as ei:
        require_user(_req("/api/chat"), authorization=f"Bearer {token}")
    assert ei.value.status_code == 403
    # /api/me 与改密接口放行
    assert require_user(_req("/api/me"), authorization=f"Bearer {token}").username == "temp1"
    # 改密后可访问核心接口
    store.set_password("temp1", "NewStrong@2026", clear_must_change=True)
    token2 = store.issue_token(store.get_by_username("temp1"))
    assert require_user(_req("/api/chat"), authorization=f"Bearer {token2}").username == "temp1"
    auth._store_singleton = None


def test_production_local_user_store_does_not_create_default_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("USER_DIRECTORY", "local")
    monkeypatch.setenv("DB_USERS_ENABLED", "1")
    user_db = tmp_path / "user_store.db"
    monkeypatch.setenv("DATACHAT_AUTH_DB", str(user_db))
    monkeypatch.setenv("DATACHAT_PERMISSIONS_DB", str(user_db))

    import app.core.auth as auth
    import app.core.permissions as perm
    from app.core.user_directory import company_directory_enabled

    auth._store_singleton = None
    perm._store_singleton = None
    assert company_directory_enabled() is False

    store = auth.get_auth_store()
    assert store.list_users() == []
    store.create_user("admin", "Strong@2026", role="admin")
    auth._store_singleton = None
    store = auth.get_auth_store()
    assert store.get_by_username("admin") is None
    admin = store.create_user("admin@feihe.com", "Strong@2026", role="admin")
    pstore = perm.get_permissions_store()
    pstore.set_for_user(admin.id, allowed_tables=["sales_tbl"], deny_by_default=True)
    assert Path(store.path) == Path(pstore.path) == user_db
    assert pstore.get_for_user(admin.id).allowed_tables == ["sales_tbl"]

    auth._store_singleton = None
    perm._store_singleton = None


# ============================================================ 公司 users 表适配
def _company_engine(tmp_path):
    from sqlalchemy import create_engine
    eng = create_engine(f"sqlite:///{tmp_path/'company.db'}")
    with eng.begin() as c:
        from sqlalchemy import text
        c.execute(text(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
            "display_name TEXT, password_hash TEXT, role TEXT, must_change_password INTEGER, "
            "is_active INTEGER, created_at TEXT, last_login TEXT, feishu_user_id TEXT, "
            "org_code TEXT, department TEXT)"
        ))
    return eng


def test_company_user_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    import bcrypt
    from sqlalchemy import text
    from app.core.user_directory import CompanyAuthStore
    from app.core.auth import AuthError

    eng = _company_engine(tmp_path)
    h_admin = bcrypt.hashpw(b"Test@Admin2026", bcrypt.gensalt(rounds=10)).decode()
    h_user = bcrypt.hashpw(b"Test@User2026", bcrypt.gensalt(rounds=10)).decode()
    with eng.begin() as c:
        c.execute(text("INSERT INTO users VALUES (1,'admin@feihe.com','系统管理员',:h,'super_admin',1,1,'t',NULL,'ou_x','o','d')"), {"h": h_admin})
        c.execute(text("INSERT INTO users VALUES (2,'u@feihe.com','张三',:h,'user',1,1,'t',NULL,'','','')"), {"h": h_user})
        c.execute(text("INSERT INTO users VALUES (3,'off@feihe.com','停用',:h,'user',0,0,'t',NULL,'','','')"), {"h": h_user})

    store = CompanyAuthStore(engine=eng, table_name="users", secret="x" * 40)

    su = store.authenticate("admin@feihe.com", "Test@Admin2026")
    assert su.role == "admin" and getattr(su, "raw_role") == "super_admin"
    assert not hasattr(su, "password_hash")
    nu = store.authenticate("u@feihe.com", "Test@User2026")
    assert nu.role == "user"
    with pytest.raises(AuthError):
        store.authenticate("u@feihe.com", "wrong")
    with pytest.raises(AuthError):  # is_active=0
        store.authenticate("off@feihe.com", "Test@User2026")
    # last_login 已更新
    with eng.connect() as c:
        ll = c.execute(text("SELECT last_login FROM users WHERE user_id=2")).scalar()
    assert ll
    # JWT 不含 password_hash；verify 往返
    tok = store.issue_token(nu)
    import jwt as _jwt
    payload = _jwt.decode(tok, "x" * 40, algorithms=["HS256"])
    assert "password_hash" not in payload and payload["username"] == "u@feihe.com"
    assert store.verify_token(tok).id == nu.id
    # 改密清除 must_change
    store.set_password("u@feihe.com", "Newp@ss2026", clear_must_change=True)
    assert store.authenticate("u@feihe.com", "Newp@ss2026").username == "u@feihe.com"


# ============================================================ planner 抗"LLM 过度澄清"
def test_planner_overrides_llm_clarify_when_structurally_sufficient(planner, semantic):
    """同一道'差异/比较类'问题，百炼 LLM 不会 needs_clarify，飞鹤 kaier_znws 会。
    planner 在 metric + (group_by|filter|calculation) 任一信号充分 + confidence ≥ 0.3 时，
    必须信结构、忽略 LLM 的 needs_clarify，让 compiler/answerer 兜底处理衍生表达式。
    """
    # 模拟飞鹤 LLM 给出的"结构完整但又自我澄清"的 plan
    llm_plan = QueryPlan(
        metric="terminal_sale_amount_total",
        extra_metrics=["reduction_gd_sale_amount_total"],
        table=SUMMARY,
        group_by=["region"],
        time_range=TimeRange(kind=TimeKind.ABSOLUTE, year="2025", months=["01"]),
        calculation="rank",
        order_by=[OrderBy(field="terminal_sale_amount_total", dir="desc")],
        limit=10,
        confidence=0.6,
        needs_clarify=True,
        clarify_reason="系统无法在查询层按两个指标的差值排序",
        clarify_options=[],
    )
    bundle = RetrievalBundle(metrics=[], dimensions=[], tables=[], few_shots=[], elapsed_ms=0)
    out = planner._validate_and_repair(
        llm_plan, bundle, rule_seed={}, today=date(2026, 4, 1),
        previous_plan=None, followup=False, question="2025年1月各大区终端销售金额和还原过单金额差异是多少？差异最大的前10个大区列出来。",
    )
    assert out.needs_clarify is False, f"结构充分时应被强制执行: {out.clarify_reason!r}"
    assert out.clarify_reason == ""
    assert out.clarify_options == []
    # 结构关键字段必须保留，等下游 compiler 把两个指标都 SELECT 出来
    assert out.metric == "terminal_sale_amount_total"
    assert "reduction_gd_sale_amount_total" in out.extra_metrics
    assert "region" in out.group_by
    assert out.calculation == "rank"

    # compiler 实际产出的 SQL 必须同时包含两个指标列，answerer 才有数据兜底算差异
    sql, _ = PlanCompiler(semantic, default_limit=500).compile(out)
    assert "terminal_sale_amount" in sql
    assert "reduction_gd_sale_amount" in sql
    assert "lev2_name" in sql  # region → lev2_name
    assert "'2025'" in sql and "'01'" in sql


def test_planner_keeps_clarify_when_structurally_insufficient(planner, semantic):
    """信号不足时仍然必须澄清——不能把一切 LLM 澄清都覆盖。"""
    weak_plan = QueryPlan(
        metric="",                # 没识别出指标
        table="",
        group_by=[],
        filters=[],
        calculation="",
        confidence=0.2,           # 低置信
        needs_clarify=True,
        clarify_reason="无法确定要查询的业务指标，请补充关键词",
    )
    bundle = RetrievalBundle(metrics=[], dimensions=[], tables=[], few_shots=[], elapsed_ms=0)
    out = planner._validate_and_repair(
        weak_plan, bundle, rule_seed={}, today=date(2026, 4, 1),
        previous_plan=None, followup=False, question="看一下情况",
    )
    assert out.needs_clarify is True


# ============================================================ 飞鹤网关签名（确定性）
def test_feihe_gateway_sign_deterministic():
    from app.core.llm.feihe_gateway import build_sign, FeiheGatewayError
    aes_key = base64.b64encode(b"0123456789abcdef").decode()  # 16B → 合法 AES key
    s1 = build_sign("data_middle_platform", "AES", 1730000000000, aes_key)
    s2 = build_sign("data_middle_platform", "AES", 1730000000000, aes_key)
    assert s1 == s2  # 确定性
    base64.b64decode(s1)  # 合法 base64
    assert build_sign("data_middle_platform", "AES", 1730000000001, aes_key) != s1
    with pytest.raises(FeiheGatewayError):
        build_sign("x", "AES", 1, "")  # 空密钥必须报错
