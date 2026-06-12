"""DataChat — FastAPI 入口。

公开端点（无需鉴权）：
  GET    /health                          服务存活
  GET    /api/health                      语义层 / DB / Redis / LLM 健康
  GET    /api/bootstrap                   SPA 启动元信息
  GET    /api/suggestions                 推荐问句
  POST   /api/login                       用户名密码 → JWT

普通用户（需 Bearer token）：
  GET    /api/me                          当前用户信息
  POST   /api/me/password                 修改自己的密码
  GET    /api/conversations               我的会话列表
  POST   /api/conversations               新建会话
  GET    /api/conversations/{id}          会话详情
  PATCH  /api/conversations/{id}          重命名
  DELETE /api/conversations/{id}          删除
  POST   /api/chat                        同步问数
  POST   /api/chat/stream                 流式问数（SSE）
  POST   /api/feishu/push                 推送到飞书
  POST   /api/report/generate             生成 DOCX 报告
  GET    /api/semantic/overview           查看语义层（只读）

管理员专享：
  GET    /api/admin/users                 列出所有用户
  POST   /api/admin/users                 新建用户
  DELETE /api/admin/users/{username}      删除用户
  POST   /api/admin/users/{username}/password  重置密码
  GET    /api/admin/logs                  审计日志（分页+筛选）
  GET    /api/admin/semantic              获取 semantic.yaml 原文
  PUT    /api/admin/semantic              覆盖 semantic.yaml 并热重载
  GET    /api/admin/permissions           查看所有用户的数据权限
  GET    /api/admin/permissions/{user_id} 查看某用户权限
  PUT    /api/admin/permissions/{user_id} 设置某用户权限
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Query, Body, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# 阶段 1.4 / 2.3 / 2.4 —— 依赖缺失时静默降级（本地未 pip install 也能启动）
try:
    from slowapi import Limiter as _SlowLimiter, _rate_limit_exceeded_handler  # type: ignore
    from slowapi.errors import RateLimitExceeded  # type: ignore
    _SLOWAPI_OK = True
except Exception:  # pragma: no cover
    _SlowLimiter = None
    _rate_limit_exceeded_handler = None
    RateLimitExceeded = Exception  # type: ignore
    _SLOWAPI_OK = False
try:
    from prometheus_fastapi_instrumentator import Instrumentator as _PromInst  # type: ignore
    _PROM_OK = True
except Exception:  # pragma: no cover
    _PromInst = None
    _PROM_OK = False
try:
    from app.logging_setup import configure_logging as _configure_logging
except Exception:  # pragma: no cover
    def _configure_logging(force: bool = False) -> None:  # type: ignore
        return None

from app.core.auth import get_auth_store, AuthError, User
from app.core.config import load_config
from app.core.conversation import get_conversation_store
from app.core.feishu import push as feishu_push, FeishuError
from app.core.nl2sql.plan import QueryPlan
from app.core.orchestrator import Pipeline, get_pipeline, to_sse_done, to_sse_error, to_sse_event, TraceEvent
from app.core.folders import get_folders_store
from app.core.permissions import get_permissions_store
from app.core.query_log import get_query_log_store
from app.core.report import generate_report
from app.core.report_templates import get_report_template_store

logger = logging.getLogger("datachat.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ============================================================== friendly errors
# 用户友好提示 + trace_id；后端日志保留真实异常。
USER_FRIENDLY = {
    "CHAT_FAILED":        "问数失败，请检查输入的问题是否符合规范，或者联系管理员。",
    "REPORT_FAILED":      "报告生成失败，请稍后重试，或联系管理员。",
    "FEISHU_FAILED":      "推送飞书失败，请检查推送配置，或联系管理员。",
    "PERMISSION_DENIED":  "权限不足，请联系管理员开通相关数据权限。",
    "INPUT_INVALID":      "输入内容不符合规范，请调整后重试。",
    "INTERNAL_ERROR":     "系统繁忙，请稍后重试。",
}

def friendly_error(code: str, *, trace_id: str = "", extra: Optional[str] = None) -> dict[str, Any]:
    msg = USER_FRIENDLY.get(code, USER_FRIENDLY["INTERNAL_ERROR"])
    if extra:
        msg = f"{msg}（{extra}）"
    return {"ok": False, "error_code": code, "user_message": msg, "trace_id": trace_id}


def normalize_chat_result(value: Any) -> dict[str, Any]:
    """把 pipeline 任意返回值规范成 dict — LLM 跑飞了也不会让 .get() 崩。"""
    if isinstance(value, dict):
        return value
    if value is None:
        return {"narrative": "未生成结果，请稍后重试。", "highlights": [], "risk_notes": [],
                "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                "chart": {"type": "none"}, "suggestions": [], "explainability": {}}
    if isinstance(value, str):
        return {"narrative": value[:500], "highlights": [], "risk_notes": [],
                "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
                "chart": {"type": "none"}, "suggestions": [], "explainability": {}}
    return {"narrative": "返回数据格式异常。", "highlights": [], "risk_notes": [],
            "table": {"columns": [], "rows": [], "display_columns": [], "display_rows": [], "row_count": 0, "elapsed_ms": 0},
            "chart": {"type": "none"}, "suggestions": [], "explainability": {}}


# -------------------------------------------------------------- request models
# 必须模块级定义，否则 FastAPI 在 `from __future__ import annotations` 下解析不到

class LoginReq(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    force_refresh: bool = False
    skip_llm_narrative: bool = False
    # 右上角下拉框选的模型，None=用 env 默认（线上=feihe）
    llm_provider: Optional[str] = None


class ConversationCreateReq(BaseModel):
    title: str = "新会话"


class ConversationRenameReq(BaseModel):
    title: str = "新会话"


class FeishuPushReq(BaseModel):
    title: str = "飞鹤小Q · 经营分析"
    narrative: str
    highlights: list[str] = Field(default_factory=list)
    rows_preview: list[str] = Field(default_factory=list)
    user_email: Optional[str] = None
    webhook: Optional[str] = None
    url: Optional[str] = None


class ReportRequest(BaseModel):
    question: str
    answer: dict[str, Any]
    plan: dict[str, Any] = Field(default_factory=dict)
    sql: str = ""
    template_id: Optional[str] = None    # 留空 = 用默认模板


class ReportTemplateReq(BaseModel):
    name: str
    prompt: str
    is_default: bool = False
    system: bool = False   # 仅 admin 生效：true 时创建系统级模板(user_id="")，否则私有模板


class ReportTemplatePatchReq(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    is_default: Optional[bool] = None


class LLMSettingsPutReq(BaseModel):
    """[legacy] 管理页旧的"单条配置"接口入参（None=不动；""=清除）。新前端走 preset CRUD。"""
    DASHSCOPE_API_KEY: Optional[str] = Field(default=None, description="百炼 AK，sk-...")
    DASHSCOPE_BASE_URL: Optional[str] = Field(default=None, description="百炼 base URL")
    DASHSCOPE_MODEL: Optional[str] = Field(default=None, description="百炼 chat 模型名 (qwen-plus / qwen-max / qwen3.6-max-preview 等)")
    DASHSCOPE_EMBED_MODEL: Optional[str] = Field(default=None, description="百炼 embedding 模型 (text-embedding-v3 等)")
    LLM_PROVIDER: Optional[str] = Field(default=None, description="默认 provider: bailian / feihe")


class LLMPresetCreateReq(BaseModel):
    name: str = Field(..., description="显示名，唯一")
    provider: str = Field(..., description="'bailian' 或 'feihe'")
    api_key: str = Field("", description="bailian 必填；feihe 留空（AES_KEY 在服务器 .env）")
    base_url: str = Field("", description="bailian: https://dashscope.aliyuncs.com/compatible-mode/v1")
    model: str = Field(..., description="chat 模型名（如 qwen-plus / qwen-max）")
    embed_model: str = Field("", description="bailian 才用，如 text-embedding-v3")


class LLMPresetPatchReq(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None   # None=不动；""=清空；非空=替换
    base_url: Optional[str] = None
    model: Optional[str] = None
    embed_model: Optional[str] = None
    is_active: Optional[bool] = None


class LLMPresetTestReq(BaseModel):
    """保存前测试：用候选配置直发一次 chat，不写库。"""
    provider: str
    api_key: str = ""
    base_url: str = ""
    model: str
    prompt: Optional[str] = None


class FolderCreateReq(BaseModel):
    name: str
    color: str = ""


class FolderRenameReq(BaseModel):
    name: str
    color: Optional[str] = None


class CollectionReq(BaseModel):
    conversation_id: str
    folder_id: str


class CreateUserReq(BaseModel):
    username: str
    password: Optional[str] = None       # 留空则后端随机生成一次性强密码
    role: str = "user"
    email: str = ""                       # 用户的飞书邮箱（飞书推送用）
    must_change_password: bool = True     # 后台创建的用户默认强制改密


class ResetPasswordReq(BaseModel):
    new_password: Optional[str] = None    # 留空 = 随机生成一次性密码并返回
    must_change_password: bool = True


class MyPasswordReq(BaseModel):
    old_password: str
    new_password: str


class MyProfileReq(BaseModel):
    email: Optional[str] = None


class SemanticPutReq(BaseModel):
    content: str           # 完整 YAML 文本


class SemanticEntityReq(BaseModel):
    name: str
    body: dict[str, Any] = Field(default_factory=dict)


class SemanticAnalyzeReq(BaseModel):
    table: str             # 物理表名（chatbi 库中实际存在的表）
    sample_rows: int = 5


class SemanticStatusReq(BaseModel):
    status: str            # draft | verified


class ChatFeedbackReq(BaseModel):
    """问数答案反馈：up=采纳（沉淀为 few-shot），down=点踩（进 bad case 库）。"""
    conversation_id: str
    trace_id: str
    vote: str = "up"       # up | down


class PermissionsPutReq(BaseModel):
    """完整权限配置 — 任一字段省略 = 不变；明确传 {} 或 [] = 清空。"""
    row_rules:        Optional[dict[str, list[str]]] = None
    allowed_tables:   Optional[list[str]] = None
    allowed_columns:  Optional[dict[str, list[str]]] = None
    deny_by_default:  Optional[bool] = None


# ----------------------------------------------------------- auth dependencies

def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


# 未改密用户仅可访问：查看自己 / 改密。其它核心接口一律 403。
_PW_CHANGE_EXEMPT_PATHS = {"/api/me", "/api/me/password"}


def require_user(request: Request, authorization: Optional[str] = Header(None)) -> User:
    token = _bearer_token(authorization)
    try:
        user = get_auth_store().verify_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    # 后端强制：未改初始密码的用户不能访问核心接口（不依赖前端引导）
    if user.must_change_password and request.url.path not in _PW_CHANGE_EXEMPT_PATHS:
        raise HTTPException(
            status_code=403,
            detail="MUST_CHANGE_PASSWORD:请先修改初始密码后再使用系统功能",
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ----------------------------------------------------------------- app factory

def create_app() -> FastAPI:
    # 阶段 2.4：每个 uvicorn worker 启动时统一日志格式（JSON 单行；env DATACHAT_LOG_FORMAT=plain 回退）
    _configure_logging()
    import os as _os
    cfg = load_config()

    _app_env = (_os.environ.get("APP_ENV") or "local").strip().lower()
    _is_local = _app_env in ("local", "dev", "development", "test", "testing")

    pipeline_holder: dict[str, Any] = {}

    def get_pipe() -> Pipeline:
        if "pipe" not in pipeline_holder:
            pipeline_holder["pipe"] = get_pipeline()
        return pipeline_holder["pipe"]

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        # 取代已弃用的 @app.on_event("startup")
        try:
            get_pipe()
            get_auth_store()
            get_query_log_store()
            get_permissions_store()
            logger.info("DataChat startup ok")
        except Exception as exc:
            logger.exception("startup failed: %s", exc)
        yield

    # 调试文档仅在本地/开发暴露；公网生产默认关闭 /api/docs 与 openapi.json
    app = FastAPI(
        title="DataChat",
        version=cfg.app.version,
        docs_url="/api/docs" if _is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if _is_local else None,
        lifespan=_lifespan,
    )

    # CORS：优先用 CORS_ALLOW_ORIGINS（逗号分隔白名单）；本地默认放开；
    # 生产未配置则收敛到本机，杜绝 "*"+credentials 的公网裸放组合。
    _origins_raw = (_os.environ.get("CORS_ALLOW_ORIGINS") or "").strip()
    if _origins_raw:
        _allow_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]
    elif _is_local:
        _allow_origins = ["*"]
    else:
        _allow_origins = ["http://127.0.0.1:8001", "http://localhost:8001"]
    _wildcard = _allow_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allow_origins,
        allow_credentials=not _wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 阶段 1.4 限流：per-token（含未登录退化到 IP）。模块缺失时静默降级（仅警告）。
    if _SLOWAPI_OK and _SlowLimiter is not None:
        def _identify_token(request: Request) -> str:
            auth = request.headers.get("authorization", "") or ""
            if auth.lower().startswith("bearer "):
                return "tok:" + auth.split(" ", 1)[1][:24]
            q = request.query_params.get("token") or ""
            if q:
                return "tok:" + q[:24]
            return "ip:" + (request.client.host if request.client else "anon")
        # 全局上限 + 关键端点单独再细一档（端点处用 @limiter.limit 覆盖）
        _limiter = _SlowLimiter(key_func=_identify_token, default_limits=["120/minute"])
        app.state.limiter = _limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("rate-limit enabled: default=120/min, /api/chat=30/min per token")
    else:
        app.state.limiter = None
        logger.warning("slowapi not installed — running WITHOUT rate limiting")

    # 阶段 2.3 Prometheus /metrics（HTTP 直方图/计数/延迟）。/metrics 不计入文档/不限流。
    if _PROM_OK and _PromInst is not None:
        try:
            _PromInst(
                should_group_status_codes=True,
                should_ignore_untemplated=True,
                excluded_handlers=["/metrics", "/health", "/api/health"],
            ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False, tags=["observability"])
            logger.info("prometheus /metrics exposed")
        except Exception as exc:  # noqa: BLE001
            logger.warning("prometheus instrument failed (skipped): %s", exc)
    else:
        logger.warning("prometheus-fastapi-instrumentator not installed — /metrics disabled")

    # 兜底异常处理：任何未捕获异常都只回友好 JSON + trace_id，
    # 绝不把 traceback / str(exc) / 连接串 暴露给用户；真实异常进日志。
    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(request: Request, exc: Exception) -> JSONResponse:
        import uuid as _u
        tid = _u.uuid4().hex
        logger.exception("[trace=%s] unhandled error on %s %s: %s",
                         tid, request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False, "error_code": "INTERNAL_ERROR",
                "user_message": "系统繁忙，请稍后重试或联系管理员。",
                "trace_id": tid,
            },
        )

    # ============================================================ public

    @app.get("/health")
    def root_health() -> dict[str, Any]:
        return {"status": "ok", "service": "DataChat", "version": cfg.app.version}

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        """公开存活探针 —— 仅最小健康状态，绝不泄露 DB host/库名、Redis URL、
        LLM provider/model、异常文本。详细诊断见管理员接口 /api/admin/diagnostics。"""
        from app.core.exec import get_executor
        from app.core.cache import cache_status
        try:
            db_ok = bool(get_executor().health().get("ok"))
        except Exception:
            db_ok = False
        try:
            cache_ok = bool(cache_status().get("enabled"))
        except Exception:
            cache_ok = False
        return {
            "status": "ok",
            "service": "DataChat",
            "version": cfg.app.version,
            "db": {"ok": db_ok},
            "cache": {"ok": cache_ok},
        }

    @app.get("/api/admin/diagnostics")
    def api_admin_diagnostics(_: User = Depends(require_admin)) -> dict[str, Any]:
        """管理员专属：完整诊断（DB host/库名、Redis、LLM、语义层、飞书）。
        未登录 / 非管理员不可访问（详细信息不对外）。"""
        from app.core.exec import get_executor
        from app.core.cache import cache_status
        pipe = get_pipe()
        feishu_ok = bool(_get_env("FEISHU_WEBHOOK") or (_get_env("FEISHU_APP_ID") and _get_env("FEISHU_APP_SECRET")))
        return {
            "service": "DataChat",
            "version": cfg.app.version,
            "semantic": {
                "metrics": len(pipe.semantic.metrics),
                "dimensions": len(pipe.semantic.dimensions),
                "tables": len(pipe.semantic.tables),
                "data_range": [pipe.semantic.data_range_earliest, pipe.semantic.data_range_latest],
            },
            "db": get_executor().health(),
            "cache": cache_status(),
            "feishu": {"configured": feishu_ok},
            "llm": {"provider": cfg.llm.primary_provider, "model": cfg.llm.bailian_chat_model},
        }

    @app.get("/api/bootstrap")
    def api_bootstrap() -> dict[str, Any]:
        pipe = get_pipe()
        return {
            "service": "DataChat",
            "version": cfg.app.version,
            "data_range": [pipe.semantic.data_range_earliest, pipe.semantic.data_range_latest],
            "metrics_count": len(pipe.semantic.metrics),
            "dimensions_count": len(pipe.semantic.dimensions),
            "tables_count": len(pipe.semantic.tables),
            "suggestions": _default_suggestions(),
            "model": {"provider": cfg.llm.primary_provider, "name": cfg.llm.bailian_chat_model},
        }

    @app.get("/api/suggestions")
    def api_suggestions() -> dict[str, Any]:
        return {"items": _default_suggestions()}

    # ============================================================ auth

    @app.post("/api/login")
    def api_login(req: LoginReq = Body(...)) -> dict[str, Any]:
        store = get_auth_store()
        try:
            user = store.authenticate(req.username, req.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        token = store.issue_token(user)
        return {"token": token, "user": _user_dict(user)}

    @app.get("/api/me")
    def api_me(user: User = Depends(require_user)) -> dict[str, Any]:
        return _user_dict(user)

    @app.post("/api/me/password")
    def api_me_change_password(req: MyPasswordReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        store = get_auth_store()
        try:
            store.authenticate(user.username, req.old_password)
        except AuthError:
            raise HTTPException(status_code=401, detail="原密码不正确")
        try:
            store.set_password(user.username, req.new_password, enforce_strength=True, clear_must_change=True)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.patch("/api/me/profile")
    def api_me_update_profile(req: MyProfileReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        if req.email is not None:
            try:
                get_auth_store().set_email(user.username, req.email)
            except AuthError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        new_user = get_auth_store().get_by_id(user.id)
        return _user_dict(new_user) if new_user else {}

    # ============================================================ admin: users

    @app.get("/api/admin/users")
    def api_admin_list_users(_: User = Depends(require_admin)) -> dict[str, Any]:
        users = get_auth_store().list_users()
        return {"items": [_user_dict(u) for u in users]}

    @app.post("/api/admin/users")
    def api_admin_create_user(req: CreateUserReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.auth import generate_initial_password
        # 没传密码 → 随机生成强密码并返回（一次性，admin 转告新用户）
        initial_pwd = req.password or generate_initial_password()
        enforce = bool(req.password)   # 用户传密码必须强度校验；系统生成跳过（自带强度）
        try:
            user = get_auth_store().create_user(
                req.username, initial_pwd, req.role,
                email=req.email or "",
                must_change_password=bool(req.must_change_password),
                enforce_strength=enforce,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        out = _user_dict(user)
        if not req.password:
            out["one_time_password"] = initial_pwd
        return out

    @app.delete("/api/admin/users/{username}")
    def api_admin_delete_user(username: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        try:
            get_auth_store().delete_user(username)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.post("/api/admin/users/{username}/password")
    def api_admin_reset_password(username: str, req: ResetPasswordReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.auth import generate_initial_password
        new_pwd = req.new_password or generate_initial_password()
        enforce = bool(req.new_password)
        try:
            get_auth_store().set_password(
                username, new_pwd,
                enforce_strength=enforce,
                clear_must_change=not req.must_change_password,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        out: dict[str, Any] = {"ok": True}
        if not req.new_password:
            out["one_time_password"] = new_pwd
        return out

    # ============================================================ admin: query log

    @app.get("/api/admin/logs")
    def api_admin_logs(
        _: User = Depends(require_admin),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        username: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> dict[str, Any]:
        items, total = get_query_log_store().list(
            limit=limit, offset=offset,
            username_like=username, status=status, keyword=keyword,
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ============================================================ admin: semantic

    @app.get("/api/admin/semantic")
    def api_admin_get_semantic(_: User = Depends(require_admin)) -> dict[str, Any]:
        path = Path(cfg.app.semantic_path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.exception("read semantic file failed: %s", exc)
            raise HTTPException(status_code=500, detail="读取语义层文件失败，请稍后重试或联系管理员。")
        return {"path": str(path), "content": text, "bytes": len(text.encode("utf-8"))}

    # ---------- per-entity CRUD ----------

    # 注意：必须注册在 GET /api/admin/semantic/{kind} 之前，否则会被 {kind} 通配吞掉
    @app.get("/api/admin/semantic/certification")
    def api_semantic_certification(_: User = Depends(require_admin)) -> dict[str, Any]:
        """认证清单：草稿排前面，业务负责人按清单走查（表定位/指标口径/维度值字典）。"""
        from app.core.semantic_editor import certification_overview
        return certification_overview(Path(cfg.app.semantic_path))

    @app.get("/api/admin/semantic/{kind}")
    def api_semantic_list_entities(kind: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        if kind not in ("tables", "dimensions", "metrics"):
            raise HTTPException(status_code=404, detail="kind 仅支持 tables/dimensions/metrics")
        from app.core.semantic_editor import list_entities
        return {"items": list_entities(Path(cfg.app.semantic_path), kind)}

    @app.put("/api/admin/semantic/{kind}/{name}")
    def api_semantic_upsert_entity(kind: str, name: str, req: SemanticEntityReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        if kind not in ("tables", "dimensions", "metrics"):
            raise HTTPException(status_code=404, detail="kind 仅支持 tables/dimensions/metrics")
        from app.core.semantic_editor import upsert_entity
        try:
            body = upsert_entity(Path(cfg.app.semantic_path), kind, req.name or name, req.body)
        except Exception as exc:
            logger.warning("semantic upsert failed: %s", exc)
            return friendly_error("INPUT_INVALID", extra=str(exc))
        pipe = get_pipe()
        pipe.semantic.reload()
        try: pipe.retriever.build()
        except Exception: pass
        return {"ok": True, "name": req.name or name, "body": body}

    @app.delete("/api/admin/semantic/{kind}/{name}")
    def api_semantic_delete_entity(kind: str, name: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        if kind not in ("tables", "dimensions", "metrics"):
            raise HTTPException(status_code=404, detail="kind 仅支持 tables/dimensions/metrics")
        from app.core.semantic_editor import delete_entity
        ok = delete_entity(Path(cfg.app.semantic_path), kind, name)
        if ok:
            pipe = get_pipe()
            pipe.semantic.reload()
            try: pipe.retriever.build()
            except Exception: pass
        return {"ok": ok}

    # ---------- 认证工作流（机器起草 → 人工认证） ----------

    @app.post("/api/admin/semantic/{kind}/{name}/status")
    def api_semantic_set_status(kind: str, name: str, req: SemanticStatusReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        if kind not in ("tables", "dimensions", "metrics"):
            raise HTTPException(status_code=404, detail="kind 仅支持 tables/dimensions/metrics")
        from app.core.semantic_editor import set_status
        try:
            result = set_status(Path(cfg.app.semantic_path), kind, name, req.status)
        except ValueError as exc:
            return friendly_error("INPUT_INVALID", extra=str(exc))
        # 状态在检索/拼 prompt 时实时读取，reload 即生效（无需重建向量索引）
        get_pipe().semantic.reload()
        return {"ok": True, **result}

    @app.post("/api/admin/semantic/analyze")
    def api_semantic_analyze(req: SemanticAnalyzeReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.exec import get_executor
        from app.core.llm import get_llm_router
        from app.core.semantic_editor import analyze_table
        try:
            proposal = analyze_table(
                req.table, schema=cfg.mysql.database,
                executor=get_executor(), llm=get_llm_router(),
                sample_rows=int(req.sample_rows or 5),
            )
            return {"ok": True, "proposal": proposal}
        except ValueError as exc:
            # 可预期的非法输入（非法表名 / 无权限 / LLM 非 JSON）→ 业务告警，不刷 traceback
            logger.warning("analyze_table rejected: %s", exc)
            return friendly_error("INPUT_INVALID", extra=str(exc)[:200])
        except Exception as exc:
            # 真正系统异常 → 记录完整 exception，但用户侧只给统一友好提示
            logger.exception("analyze_table failed: %s", exc)
            return friendly_error("INTERNAL_ERROR")

    @app.put("/api/admin/semantic")
    def api_admin_put_semantic(req: SemanticPutReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        import yaml
        path = Path(cfg.app.semantic_path)
        try:
            parsed = yaml.safe_load(req.content)
            if not isinstance(parsed, dict):
                raise ValueError("根节点必须是 YAML mapping")
            for must in ("tables", "metrics", "dimensions"):
                if must not in parsed:
                    raise ValueError(f"缺少必填字段: {must}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"YAML 校验失败: {exc}")
        # backup + write atomically
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            if path.exists():
                backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(req.content, encoding="utf-8")
        except OSError as exc:
            logger.exception("write semantic file failed: %s", exc)
            raise HTTPException(status_code=500, detail="保存语义层文件失败，请稍后重试或联系管理员。")
        # hot reload
        pipe = get_pipe()
        pipe.semantic.reload()
        try:
            pipe.retriever.build()  # rebuild retrieval index
        except Exception as exc:
            logger.warning("retriever rebuild failed: %s", exc)
        return {
            "ok": True,
            "metrics": len(pipe.semantic.metrics),
            "dimensions": len(pipe.semantic.dimensions),
            "tables": len(pipe.semantic.tables),
        }

    # ============================================================ admin: permissions

    @app.get("/api/admin/permissions")
    def api_admin_list_perms(_: User = Depends(require_admin)) -> dict[str, Any]:
        store = get_permissions_store()
        users = get_auth_store().list_users()
        all_perms = store.list_all()
        return {
            "items": [
                {
                    "user_id": u.id, "username": u.username, "role": u.role,
                    "row_rules":       all_perms.get(u.id, {}).get("row_rules") or {},
                    "allowed_tables":  all_perms.get(u.id, {}).get("allowed_tables") or [],
                    "allowed_columns": all_perms.get(u.id, {}).get("allowed_columns") or {},
                    "deny_by_default": bool(all_perms.get(u.id, {}).get("deny_by_default")),
                }
                for u in users
            ]
        }

    @app.get("/api/admin/permissions/{user_id}")
    def api_admin_get_perms(user_id: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        b = get_permissions_store().get_for_user(user_id)
        return {
            "user_id": user_id,
            "row_rules": b.row_rules,
            "allowed_tables": b.allowed_tables,
            "allowed_columns": b.allowed_columns,
            "deny_by_default": b.deny_by_default,
        }

    @app.put("/api/admin/permissions/{user_id}")
    def api_admin_put_perms(user_id: str, req: PermissionsPutReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        pipe = get_pipe()
        valid_dims = set(pipe.semantic.dimensions.keys())
        valid_tables = set(pipe.semantic.tables.keys())
        if req.row_rules:
            unknown = [d for d in req.row_rules.keys() if d not in valid_dims]
            if unknown:
                raise HTTPException(status_code=400, detail=f"未知维度: {unknown}")
        if req.allowed_tables:
            unknown = [t for t in req.allowed_tables if t not in valid_tables]
            if unknown:
                raise HTTPException(status_code=400, detail=f"未知数据表: {unknown}")
        if req.allowed_columns:
            for tbl in req.allowed_columns.keys():
                if tbl not in valid_tables:
                    raise HTTPException(status_code=400, detail=f"未知数据表: {tbl}")
        get_permissions_store().set_for_user(
            user_id,
            row_rules=req.row_rules,
            allowed_tables=req.allowed_tables,
            allowed_columns=req.allowed_columns,
            deny_by_default=req.deny_by_default,
        )
        return {"ok": True}

    # ============================================================ llm providers
    @app.get("/api/llm/providers")
    def api_llm_providers(_user: User = Depends(require_user)) -> dict[str, Any]:
        """右上角下拉框用：列出本环境**已配置**的 LLM provider + 默认值。
        不返回任何 key/secret 明文。"""
        from app.core.llm.router import available_providers, default_provider
        return {
            "available": available_providers(),
            "default": default_provider(),
        }

    # ============================================================ admin: llm settings
    @app.get("/api/admin/llm-settings")
    def api_admin_get_llm_settings(_: User = Depends(require_admin)) -> dict[str, Any]:
        """读当前生效的 LLM 配置（DB 优先 → env → cfg 默认）。
        secret(DASHSCOPE_API_KEY) 一律脱敏成 'sk-***1234'，**绝不**回完整密文。"""
        from app.core.llm_settings import get_llm_settings_store
        return {"settings": get_llm_settings_store().get_all_effective()}

    @app.put("/api/admin/llm-settings")
    def api_admin_put_llm_settings(
        req: LLMSettingsPutReq = Body(...),
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        """写 LLM 配置到 SQLite，**写完即生效**（下一次 LLM 调用自动用新值，无需重启）。
        空字符串/None 视为"清除该键"，下次回退到 env 或代码默认。
        允许键白名单：DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL /
                   DASHSCOPE_EMBED_MODEL / LLM_PROVIDER。"""
        from app.core.llm_settings import get_llm_settings_store
        store = get_llm_settings_store()
        # req.dict(exclude_unset=False) 取所有字段；空串=清除，None=不动
        payload = req.dict()
        # None 字段视作"未传/不动"，过滤掉
        updates = {k: v for k, v in payload.items() if v is not None}
        changed = store.set_many(updates)
        logger.info("admin llm-settings update keys=%s", changed)
        return {"ok": True, "updated": changed, "version": store.version}

    # ============================================================ admin: llm presets (multi-LLM)
    @app.get("/api/admin/llm-presets")
    def api_admin_list_llm_presets(_: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.llm_presets import get_llm_presets_store
        return {"items": [p.to_dict_masked() for p in get_llm_presets_store().list_all(include_inactive=True)]}

    @app.post("/api/admin/llm-presets/test")
    def api_admin_test_llm_preset_candidate(
        req: LLMPresetTestReq = Body(...),
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        """保存前测试：用候选配置直接发一句问题，必须收到非空回复才返回 ok=True；不写库。"""
        from app.core.llm.test_runner import test_preset_config, DEFAULT_TEST_PROMPT
        result = test_preset_config(
            req.provider,
            api_key=req.api_key, base_url=req.base_url,
            model=req.model,
        )
        result["prompt"] = req.prompt or DEFAULT_TEST_PROMPT
        return result

    @app.post("/api/admin/llm-presets")
    def api_admin_create_llm_preset(
        req: LLMPresetCreateReq = Body(...),
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        from app.core.llm_presets import get_llm_presets_store
        try:
            p = get_llm_presets_store().create(
                name=req.name, provider=req.provider, api_key=req.api_key,
                base_url=req.base_url, model=req.model, embed_model=req.embed_model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "preset": p.to_dict_masked()}

    @app.put("/api/admin/llm-presets/{preset_id}")
    def api_admin_update_llm_preset(
        preset_id: str,
        req: LLMPresetPatchReq = Body(...),
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        from app.core.llm_presets import get_llm_presets_store
        try:
            p = get_llm_presets_store().update(
                preset_id,
                name=req.name, provider=req.provider, api_key=req.api_key,
                base_url=req.base_url, model=req.model, embed_model=req.embed_model,
                is_active=req.is_active,
            )
        except ValueError as exc:
            code = 404 if "不存在" in str(exc) else 400
            raise HTTPException(status_code=code, detail=str(exc))
        return {"ok": True, "preset": p.to_dict_masked()}

    @app.delete("/api/admin/llm-presets/{preset_id}")
    def api_admin_delete_llm_preset(
        preset_id: str,
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        from app.core.llm_presets import get_llm_presets_store
        get_llm_presets_store().delete(preset_id)
        return {"ok": True}

    @app.post("/api/admin/llm-presets/{preset_id}/set-default")
    def api_admin_set_default_llm_preset(
        preset_id: str,
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        from app.core.llm_presets import get_llm_presets_store
        try:
            get_llm_presets_store().set_default(preset_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"ok": True}

    @app.post("/api/admin/llm-presets/{preset_id}/test")
    def api_admin_test_existing_llm_preset(
        preset_id: str,
        _: User = Depends(require_admin),
    ) -> dict[str, Any]:
        """对已存的 preset 跑一发测试，把结果写回 last_test_*"""
        from app.core.llm_presets import get_llm_presets_store
        from app.core.llm.test_runner import test_preset_config
        store = get_llm_presets_store()
        p = store.get(preset_id)
        if not p:
            raise HTTPException(status_code=404, detail="preset 不存在")
        result = test_preset_config(p.provider, api_key=p.api_key, base_url=p.base_url, model=p.model)
        store.record_test(preset_id, bool(result.get("ok")), str(result.get("text") or result.get("error") or ""))
        return result

    # ============================================================ chat

    # 阶段 1.4：单接口限流。没装 slowapi 时为 no-op，不影响功能。
    def _chat_limit(spec: str):
        lim = getattr(app.state, "limiter", None)
        if lim is not None:
            return lim.limit(spec)
        def _noop(func):
            return func
        return _noop

    @app.post("/api/chat")
    @_chat_limit("30/minute")
    def api_chat(request: Request, req: ChatRequest = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        from app.core.llm.router import set_request_provider
        set_request_provider(req.llm_provider)
        return _do_chat(get_pipe(), get_conversation_store(), user, req, on_event=None)

    @app.post("/api/chat/stream")
    @_chat_limit("30/minute")
    async def api_chat_stream(
        request: Request,
        req: ChatRequest = Body(...),
        token: Optional[str] = Query(None),
        authorization: Optional[str] = Header(None),
    ) -> StreamingResponse:
        bearer = _bearer_token(authorization) or (token or "")
        try:
            user = get_auth_store().verify_token(bearer)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))

        pipe = get_pipe()
        store = get_conversation_store()
        session_id = req.conversation_id
        if session_id:
            sess = store.get_session(session_id)
            if not sess or sess.user_id != user.id:
                raise HTTPException(status_code=404, detail="conversation not found")

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_event(evt) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ("event", evt))
            except Exception:
                pass

        # ContextVar 默认在主线程设置后**不会**穿透到 run_in_executor 的工作线程；
        # 这里改成在 worker 内显式 set，保证 pipeline → planner → answerer → llm.chat
        # 整条调用栈都能看到本次请求选的 provider。
        from app.core.llm.router import set_request_provider as _set_provider
        chosen_provider = req.llm_provider  # capture before worker runs (avoid req lifetime issues)

        def worker() -> None:
            try:
                _set_provider(chosen_provider)
                payload = _do_chat(pipe, store, user, req, on_event=on_event)
                loop.call_soon_threadsafe(queue.put_nowait, ("done", payload))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))

        loop.run_in_executor(None, worker)

        async def gen() -> AsyncGenerator[str, None]:
            if session_id:
                yield to_sse_event(_simple_event("session", "ok", {"conversation_id": session_id}))
            while True:
                kind, payload = await queue.get()
                if kind == "event":
                    yield to_sse_event(payload)
                elif kind == "done":
                    yield to_sse_done(payload)
                    break
                elif kind == "error":
                    yield to_sse_error(str(payload))
                    break

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ====================================================== conversations

    @app.post("/api/conversations")
    def conversations_create(req: ConversationCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        s = get_conversation_store().create_session(user.id, title=req.title)
        return {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at}

    @app.get("/api/conversations")
    def conversations_list(user: User = Depends(require_user)) -> dict[str, Any]:
        items = get_conversation_store().list_sessions(user.id)
        return {"items": [{"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at} for s in items]}

    @app.get("/api/conversations/{cid}")
    def conversations_get(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
        store = get_conversation_store()
        s = store.get_session(cid)
        if not s or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        msgs = store.list_messages(cid)
        return {
            "id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at,
            "messages": [
                {"id": m.id, "role": m.role, "content": m.content, "payload": m.payload, "created_at": m.created_at}
                for m in msgs
            ],
        }

    @app.patch("/api/conversations/{cid}")
    def conversations_rename(cid: str, body: ConversationRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        store = get_conversation_store()
        s = store.get_session(cid)
        if not s or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        store.rename_session(cid, body.title or "新会话")
        return {"ok": True}

    @app.delete("/api/conversations/{cid}")
    def conversations_delete(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
        store = get_conversation_store()
        s = store.get_session(cid)
        if not s or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        store.delete_session(cid)
        return {"ok": True}

    # ============================================================ feishu

    @app.post("/api/chat/feedback")
    def api_chat_feedback(req: ChatFeedbackReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        """问数反馈闭环（P2 飞轮）：
        · vote=up   → (问题, plan) 沉淀为同域 few-shot，后续同类问题作为范例注入 planner；
        · vote=down → 记入 bad case 库（评测集挖掘素材），不参与召回。
        plan 一律以服务端会话存储为准，不信任客户端传参。"""
        vote = (req.vote or "up").strip().lower()
        if vote not in ("up", "down"):
            return friendly_error("INPUT_INVALID", extra="vote 仅支持 up/down")
        store = get_conversation_store()
        sess = store.get_session(req.conversation_id)
        if not sess or sess.user_id != user.id:
            return friendly_error("INPUT_INVALID", extra="会话不存在或无权访问")
        msgs = store.list_messages(req.conversation_id, limit=500)
        question, plan_dict = "", {}
        for i, m in enumerate(msgs):
            if m.role == "assistant" and str((m.payload or {}).get("trace_id") or "") == req.trace_id:
                plan_dict = dict((m.payload or {}).get("plan") or {})
                for prev in reversed(msgs[:i]):
                    if prev.role == "user":
                        question = prev.content
                        break
                break
        if not question:
            return friendly_error("INPUT_INVALID", extra="未找到该回答对应的提问")
        from app.core.fewshot_store import get_fewshot_store
        fs = get_fewshot_store()
        if vote == "down":
            fs.record_downvote(user.id, question, plan_dict)
            return {"ok": True, "vote": "down"}
        adopted = fs.add_adopted(user.id, question, plan_dict)
        return {"ok": True, "vote": "up", "adopted": adopted}

    @app.get("/api/admin/fewshots/stats")
    def api_admin_fewshot_stats(_: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.fewshot_store import get_fewshot_store
        return get_fewshot_store().stats()

    @app.post("/api/feishu/push")
    def api_feishu_push(req: FeishuPushReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        import uuid as _uuid
        trace_id = _uuid.uuid4().hex
        # 安全（P1）：禁止请求体指定任意 webhook/url（SSRF / 内网探测）。
        # 推送目标只允许：服务端配置的 webhook，或按"用户邮箱→open_id"个人推送。
        #
        # 收件人解析规则：
        #   · 管理员：必须显式传 user_email（admin@feihe.com 这种系统账号在飞书查不到
        #     open_id，绝不应该 fallback 到 user.email 去试，会必失败）；
        #     若没传，target_email=None → 落到 env 里 FEISHU_WEBHOOK 兜底（管理群）。
        #   · 普通用户：用自己绑定的飞书邮箱 user.email。请求体里 user_email 一概忽略，
        #     防止越权推给别人。
        if user.role == "admin":
            target_email = (req.user_email or "").strip() or None
        else:
            target_email = (user.email or "").strip() or None
        try:
            res = feishu_push(
                req.title, req.narrative, req.highlights, req.rows_preview,
                user_email=target_email, webhook=None, url=None,
            )
            return {"ok": True, "trace_id": trace_id}
        except FeishuError as exc:
            # 真实异常（含底层网络错误）只进日志，绝不回传用户侧
            logger.warning("[trace=%s user=%s] feishu push failed: %s", trace_id, user.username, exc)
            return {"ok": False, "error_code": "FEISHU_PUSH_FAILED",
                    "user_message": "飞书推送失败，请确认已配置推送或联系管理员。",
                    "trace_id": trace_id}
        except Exception as exc:
            logger.exception("[trace=%s user=%s] feishu push crashed: %s", trace_id, user.username, exc)
            return {"ok": False, "error_code": "FEISHU_PUSH_ERROR",
                    "user_message": "飞书推送失败，请稍后重试或联系管理员。",
                    "trace_id": trace_id}

    # ============================================================ report

    @app.post("/api/report/generate")
    def api_report(req: ReportRequest = Body(...), user: User = Depends(require_user)):
        backend_root = Path(__file__).resolve().parent.parent
        out_dir = backend_root / "reports" / "generated"
        store = get_report_template_store()
        # 模板归属校验：admin 通用；普通用户只能用系统模板或自己的
        tpl = store.get(req.template_id) if req.template_id else None
        if tpl and user.role != "admin" and tpl.user_id and tpl.user_id != user.id:
            raise HTTPException(status_code=403, detail="无权使用该模板")
        if not tpl:
            tpl = store.get_default_for_user(user.id)
        prompt = tpl.prompt if tpl else None
        name = tpl.name if tpl else "标准商业分析报告"
        try:
            path = generate_report(
                req.question, req.answer, req.plan, req.sql,
                output_dir=out_dir, template_prompt=prompt, template_name=name,
            )
        except Exception as exc:
            logger.exception("[user=%s] report failed: %s", user.username, exc)
            raise HTTPException(status_code=500, detail="报告生成失败，请稍后重试，或联系管理员。")
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # ====================================== 报告模板（user 隔离）
    # 普通用户：看「系统默认」+「自己创建的」；只能改自己的
    # admin：看全部，按用户筛选；可以改任何

    @app.get("/api/report/templates")
    def api_list_report_templates(user: User = Depends(require_user), owner: Optional[str] = Query(None)) -> dict[str, Any]:
        store = get_report_template_store()
        if user.role == "admin":
            if owner:
                items = [t for t in store.list_all() if t.user_id == owner or (owner == "system" and not t.user_id)]
            else:
                items = store.list_all()
        else:
            items = store.list_for_user(user.id)
        return {"items": [{"id": t.id, "name": t.name, "prompt": t.prompt,
                           "is_default": t.is_default, "user_id": t.user_id,
                           "is_system": not t.user_id,
                           "is_mine": t.user_id == user.id,
                           "created_at": t.created_at, "updated_at": t.updated_at} for t in items]}

    @app.post("/api/report/templates")
    def api_create_template(req: ReportTemplateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        """普通用户创建私有模板（user_id=自己）；admin 可选 user_id="" 创建系统模板。"""
        target_user_id = user.id if user.role != "admin" else (user.id if not getattr(req, "system", False) else "")
        try:
            t = get_report_template_store().create(name=req.name, prompt=req.prompt,
                                                   is_default=req.is_default, user_id=target_user_id)
        except ValueError as exc:
            # 业务校验信息（如名称为空）对管理员可见即可，不含内部细节
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("[user=%s] create report template failed: %s", user.username, exc)
            raise HTTPException(status_code=500, detail="模板保存失败，请稍后重试或联系管理员。")
        return {"id": t.id, "name": t.name, "is_default": t.is_default, "user_id": t.user_id}

    @app.patch("/api/report/templates/{tid}")
    def api_update_template(tid: str, req: ReportTemplatePatchReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        try:
            get_report_template_store().update(
                tid, name=req.name, prompt=req.prompt, is_default=req.is_default,
                requester_user_id=user.id, requester_is_admin=(user.role == "admin"),
            )
        except Exception as exc:
            raise HTTPException(status_code=403 if "无权" in str(exc) else 400, detail=str(exc))
        return {"ok": True}

    @app.delete("/api/report/templates/{tid}")
    def api_delete_template(tid: str, user: User = Depends(require_user)) -> dict[str, Any]:
        try:
            get_report_template_store().delete(
                tid,
                requester_user_id=user.id, requester_is_admin=(user.role == "admin"),
            )
        except Exception as exc:
            raise HTTPException(status_code=403 if "无权" in str(exc) else 400, detail=str(exc))
        return {"ok": True}

    # ============================================================ folders + favorites

    @app.get("/api/folders")
    def api_folders_list(user: User = Depends(require_user)) -> dict[str, Any]:
        items = get_folders_store().list_folders(user.id)
        return {"items": [{"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at} for f in items]}

    @app.post("/api/folders")
    def api_folders_create(req: FolderCreateReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        f = get_folders_store().create_folder(user.id, req.name, req.color)
        return {"id": f.id, "name": f.name, "color": f.color, "created_at": f.created_at}

    @app.patch("/api/folders/{folder_id}")
    def api_folders_rename(folder_id: str, req: FolderRenameReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        get_folders_store().rename_folder(user.id, folder_id, req.name, req.color)
        return {"ok": True}

    @app.delete("/api/folders/{folder_id}")
    def api_folders_delete(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
        get_folders_store().delete_folder(user.id, folder_id)
        return {"ok": True}

    @app.get("/api/folders/{folder_id}/conversations")
    def api_folders_conversations(folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
        store = get_folders_store()
        items = store.list_collections(user.id, folder_id=folder_id)
        # 加上会话元信息
        conv_store = get_conversation_store()
        out: list[dict[str, Any]] = []
        for it in items:
            s = conv_store.get_session(it.conversation_id)
            if not s:
                continue
            out.append({
                "id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at,
                "collected_at": it.created_at,
            })
        return {"items": out}

    @app.post("/api/conversations/{cid}/collect")
    def api_conversation_collect(cid: str, req: CollectionReq = Body(...), user: User = Depends(require_user)) -> dict[str, Any]:
        # cid 必须等于 body.conversation_id，且会话必须属于该用户
        if cid != req.conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id 不一致")
        s = get_conversation_store().get_session(cid)
        if not s or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在或无权限")
        c = get_folders_store().add(user.id, cid, req.folder_id)
        return {"ok": True, "id": c.id}

    @app.delete("/api/conversations/{cid}/collect/{folder_id}")
    def api_conversation_uncollect(cid: str, folder_id: str, user: User = Depends(require_user)) -> dict[str, Any]:
        get_folders_store().remove(user.id, cid, folder_id)
        return {"ok": True}

    @app.get("/api/conversations/{cid}/folders")
    def api_conversation_folders(cid: str, user: User = Depends(require_user)) -> dict[str, Any]:
        fids = get_folders_store().folder_ids_for_conversation(user.id, cid)
        return {"folder_ids": fids}

    # ============================================================ semantic (read-only)

    @app.get("/api/semantic/overview")
    def api_semantic_overview(_: User = Depends(require_user)) -> dict[str, Any]:
        pipe = get_pipe()
        return {
            "data_range": [pipe.semantic.data_range_earliest, pipe.semantic.data_range_latest],
            "metrics": [
                {"name": m.name, "label": m.label, "table": m.table, "domain": m.domain, "unit": m.unit, "description": m.description}
                for m in pipe.semantic.list_metrics()
            ],
            "dimensions": [
                {"name": d.name, "label": d.label, "tables": list(d.table_columns.keys()), "samples": d.sample_values[:6]}
                for d in pipe.semantic.list_dimensions()
            ],
            "tables": [
                {"name": t.name, "label": t.label, "schema": t.schema, "grain": t.grain, "description": t.description}
                for t in pipe.semantic.list_tables()
            ],
            "calculations": [
                {"name": c.name, "label": c.label, "aliases": c.aliases, "formula": c.formula}
                for c in pipe.semantic.calculations.values()
            ],
        }

    # ============================================================ static frontend

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.exists():
        app.mount("/web", StaticFiles(directory=web_dir, html=True), name="web")

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse(url="/web/")

    return app


# =============================================================================
# helpers
# =============================================================================

def _user_dict(u: User) -> dict[str, Any]:
    return {
        "id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at,
        "email": u.email or "",
        "must_change_password": bool(u.must_change_password),
    }


def _do_chat(pipe: Pipeline, store, user: User, req: ChatRequest, *, on_event=None) -> dict[str, Any]:
    """实际跑问数 + 落地会话消息 + 落地审计日志，返回响应字典。

    所有内部异常都被吞掉，返回 friendly_error。trace_id 让管理员能在日志中追查。
    永远 200 OK，前端按 ok=true/false 区分。
    """
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    try:
        session_id = req.conversation_id
        if not session_id:
            session = store.create_session(user.id, title=_short_title(req.question))
            session_id = session.id
        else:
            sess = store.get_session(session_id)
            if not sess or sess.user_id != user.id:
                # 不暴露 "conversation not found"，给统一友好提示
                return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="会话不存在或无权访问")
        if on_event:
            on_event(_simple_event("session", "ok", {"conversation_id": session_id}))

        # 校验输入
        question = (req.question or "").strip()
        if not question:
            return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="问题不能为空")
        if len(question) > 8000:
            return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="问题过长（超过 8000 字符）")

        history = store.history_for_llm(session_id, limit=4)
        prev_plan: Optional[QueryPlan] = None
        sig = store.latest_assistant_plan_signature(session_id)
        if sig:
            for msg in store.list_messages(session_id, limit=20):
                if msg.role == "assistant" and msg.plan_signature == sig:
                    try:
                        prev_plan = QueryPlan.from_dict((msg.payload or {}).get("plan") or {})
                    except Exception:
                        prev_plan = None
                    break

        store.append_message(session_id, "user", question, payload={})
        try:
            result = pipe.run(
                question,
                user_id=user.id,
                is_admin=(user.role == "admin"),
                history=history,
                previous_plan=prev_plan,
                on_event=on_event,
                force_refresh=req.force_refresh,
                skip_llm_narrative=req.skip_llm_narrative,
            )
        except Exception as exc:
            logger.exception("[trace=%s user=%s] pipeline crashed: %s", trace_id, user.username, exc)
            try:
                get_query_log_store().record(
                    trace_id=trace_id, user_id=user.id, username=user.username,
                    conversation_id=session_id, question=question,
                    plan={}, sql="", rows=0, elapsed_ms=0, cached=False,
                    needs_clarify=False, error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            return friendly_error("CHAT_FAILED", trace_id=trace_id)

        # 审计 P1-4：pipeline 在 SQL 编译 / Guard / 权限 / 执行失败时 ok=False，
        # 必须返回 friendly_error，绝不把失败 narrative 当正常答案展示。
        # 内部失败原因只进日志 + 审计，用户侧只给统一友好提示。
        if not getattr(result, "ok", True):
            internal = str((result.answer or {}).get("narrative") or "")[:500]
            logger.warning("[trace=%s user=%s] pipeline failed ok=false code=%s: %s",
                            result.trace_id, user.username, result.error_code, internal)
            try:
                get_query_log_store().record(
                    trace_id=result.trace_id, user_id=user.id, username=user.username,
                    conversation_id=session_id, question=question,
                    plan=result.plan if isinstance(result.plan, dict) else {},
                    sql=str(result.sql or ""), rows=0,
                    elapsed_ms=int(result.elapsed_ms or 0), cached=False,
                    needs_clarify=False, error=f"{result.error_code}: {internal}",
                )
            except Exception as exc:
                logger.warning("query_log record failed: %s", exc)
            return friendly_error(result.error_code or "CHAT_FAILED", trace_id=result.trace_id)

        # 规范化所有可能被 LLM 弄飞的字段
        answer = normalize_chat_result(result.answer)
        plan_dict = result.plan if isinstance(result.plan, dict) else {}
        sql_str = str(result.sql or "")
        narrative = str(answer.get("narrative") or "")

        try:
            plan_sig = QueryPlan.from_dict(plan_dict).signature() if plan_dict else ""
        except Exception:
            plan_sig = ""

        store.append_message(
            session_id, "assistant", narrative,
            payload={
                "answer": answer, "plan": plan_dict, "sql": sql_str,
                "rows": int(result.rows or 0), "cached": bool(result.cached),
                "trace_id": result.trace_id,
            },
            plan_signature=plan_sig,
        )
        # 审计日志
        try:
            get_query_log_store().record(
                trace_id=result.trace_id, user_id=user.id, username=user.username,
                conversation_id=session_id, question=question,
                plan=plan_dict, sql=sql_str,
                rows=int(result.rows or 0), elapsed_ms=int(result.elapsed_ms or 0),
                cached=bool(result.cached),
                needs_clarify=bool(plan_dict.get("needs_clarify")), error="",
            )
        except Exception as exc:
            logger.warning("query_log record failed: %s", exc)

        return {
            "ok": True,
            "trace_id": result.trace_id,
            "conversation_id": session_id,
            "question": question,
            "answer": answer,
            "plan": plan_dict,
            "sql": sql_str,
            "rows": int(result.rows or 0),
            "cached": bool(result.cached),
            "elapsed_ms": int(result.elapsed_ms or 0),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[trace=%s] _do_chat outer crash: %s", trace_id, exc)
        return friendly_error("CHAT_FAILED", trace_id=trace_id)


def _default_suggestions() -> list[str]:
    return [
        "本月各大区销售额排名",
        "卓睿系列最近 6 个月销售趋势",
        "1 段产品在各大区的销售情况",
        "销售目标完成率排前三的省区",
        "60 天复购率最高的省区",
        "潜客转新率排名前 5 的省区",
        "终端销售额同比增长情况",
        "东一区核心终端销售情况",
    ]


def _short_title(question: str) -> str:
    s = (question or "").strip().replace("\n", " ")
    return (s[:18] + "…") if len(s) > 18 else (s or "新会话")


def _simple_event(stage: str, status: str, payload: dict[str, Any]):
    return TraceEvent(stage=stage, status=status, payload=payload, elapsed_ms=0, timestamp=datetime.utcnow().isoformat() + "Z")


def _get_env(name: str) -> str:
    import os
    return (os.environ.get(name) or "").strip()


app = create_app()
