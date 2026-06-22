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
from app.api.support import trusted_result_for_trace as _trusted_result_for_trace, user_dict as _user_dict
from app.core.conversation import get_conversation_store
from app.core.feishu import push as feishu_push, FeishuError
from app.core.nl2sql.plan import QueryPlan
from app.core.orchestrator import Pipeline, get_pipeline, to_sse_done, to_sse_error, to_sse_event, TraceEvent
from app.core.folders import get_folders_store, FolderNotFound
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
# 抽到 app/api/schemas.py（#16）；此处 re-export，保持 `from app.main import XxxReq` 兼容。
from app.api.schemas import (  # noqa: E402
    LoginReq, ChatRequest, ConversationCreateReq, ConversationRenameReq,
    FeishuPushReq, ReportRequest, ReportTemplateReq, ReportTemplatePatchReq,
    LLMSettingsPutReq, LLMPresetCreateReq, LLMPresetPatchReq, LLMPresetTestReq,
    FolderCreateReq, FolderRenameReq, CollectionReq, FolderMembershipReq, CreateUserReq,
    ResetPasswordReq, UserActiveReq, MyPasswordReq, MyProfileReq,
    SemanticPutReq, SemanticEntityReq, SemanticAnalyzeReq, SemanticStatusReq,
    ChatFeedbackReq, PermissionsPutReq,
)


# ----------------------------------------------------------- auth dependencies
# 抽到 app/api/deps.py（#16）；此处 re-export，保持 `from app.main import require_user` 等兼容。
from app.api.deps import (  # noqa: E402
    _bearer_token, _PW_CHANGE_EXEMPT_PATHS, _authenticate_or_403,
    require_user, require_admin,
)


# 关键特性路由挂载错误（mount_at -> 错误摘要）。本地继续启动但记录于此，管理员诊断可见。
_ROUTER_MOUNT_ERRORS: dict[str, str] = {}


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
        # —— 容量调优（P0-1/P0-2）——
        # SSE 问数经 run_in_executor(None) 落到事件循环**默认线程池**，其默认大小约
        # min(32, CPU+4)（4 核机仅 ~8），高峰会排队。这里显式放大，并把 anyio 线程限额
        # （同步端点 /api/chat 走的池）对齐，让两条问数路径并发能力一致。
        _pool_size = max(8, int(_os.environ.get("CHAT_THREAD_POOL_SIZE", "32") or "32"))
        _anyio_limit = max(8, int(_os.environ.get("ANYIO_THREAD_LIMIT", "32") or "32"))
        try:
            from concurrent.futures import ThreadPoolExecutor
            asyncio.get_running_loop().set_default_executor(
                ThreadPoolExecutor(max_workers=_pool_size, thread_name_prefix="chat")
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("set default executor failed: %s", exc)
        try:
            import anyio.to_thread
            anyio.to_thread.current_default_thread_limiter().total_tokens = _anyio_limit
        except Exception as exc:  # noqa: BLE001
            logger.warning("set anyio thread limit failed: %s", exc)
        try:
            get_pipe()
            get_auth_store()
            get_query_log_store()
            get_permissions_store()
            from app.core.concurrency import get_chat_guard
            _guard = get_chat_guard()  # 预热并发闸 + 注册自定义 Prometheus 指标
            logger.info(
                "DataChat startup ok (chat_pool=%s, anyio=%s, chat_max_inflight=%s)",
                _pool_size, _anyio_limit, _guard.max_inflight,
            )
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

    # 关键特性路由（专家团 / SmartQ / 导出队列）：各自自包含模块，这里一行挂载。
    # 审计 P1：生产环境若导入失败必须**快速失败**（启动即报错），绝不让"健康但缺特性"
    # 的半残应用悄悄上线；本地/开发则记录诊断、继续启动便于排查。
    def _mount(import_path: str, attr: str, mount_at: str) -> None:
        try:
            import importlib
            mod = importlib.import_module(import_path)
            app.include_router(getattr(mod, attr))
            logger.info("router mounted at %s", mount_at)
        except Exception as exc:  # noqa: BLE001
            _ROUTER_MOUNT_ERRORS[mount_at] = f"{type(exc).__name__}: {exc}"
            if not _is_local:
                logger.error("FATAL: critical router %s failed to mount: %s", mount_at, exc)
                raise RuntimeError(f"critical router {mount_at} failed to mount: {exc}") from exc
            logger.warning("router not mounted (local, continuing): %s -> %s", mount_at, exc)

    _ROUTER_MOUNT_ERRORS.clear()
    _mount("app.expert_team.api", "router", "/api/expert-team")
    _mount("app.integrations.smartq.api", "router", "/api/smartq")
    _mount("app.exports.api", "router", "/api/exports")

    # 核心路由（按域拆分到 app/api/routes/*，与上面可选特性路由同构挂载）。
    # 这些是主链路必备路由，导入失败应直接让应用启动失败（不吞异常）。
    from app.api.routes.admin import router as _admin_router
    from app.api.routes.conversations import router as _conversations_router
    from app.api.routes.feishu import router as _feishu_router
    from app.api.routes.folders import router as _folders_router
    from app.api.routes.llm import router as _llm_router
    from app.api.routes.reports import router as _reports_router
    app.include_router(_conversations_router)
    app.include_router(_folders_router)
    app.include_router(_reports_router)
    app.include_router(_feishu_router)
    app.include_router(_admin_router)
    app.include_router(_llm_router)

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

    # 阶段 P1：/metrics 访问控制 —— 生产仅 localhost/内网/带 METRICS_TOKEN 可访问，
    # 杜绝公网无鉴权拉取运行指标（端点 QPS/延迟/路由分布等属内部信息）。
    @app.middleware("http")
    async def _guard_metrics(request: Request, call_next):
        if request.url.path == "/metrics":
            if not _metrics_access_allowed(
                is_local=_is_local,
                client_ip=(request.client.host if request.client else ""),
                auth_header=request.headers.get("authorization", ""),
                token=(_os.environ.get("METRICS_TOKEN") or "").strip(),
            ):
                return JSONResponse(status_code=403, content={"detail": "metrics access restricted"})
        return await call_next(request)

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
            "routers": {
                "mounted": [p for p in ("/api/expert-team", "/api/smartq", "/api/exports")
                            if p not in _ROUTER_MOUNT_ERRORS],
                "errors": dict(_ROUTER_MOUNT_ERRORS),
            },
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

    # ---- #15：语义层版本快照 / 回滚（同样必须注册在 {kind} 之前）----
    @app.get("/api/admin/semantic/versions")
    def api_semantic_versions(_: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.semantic_versions import list_versions
        return {"items": list_versions(Path(cfg.app.semantic_path))}

    @app.get("/api/admin/semantic/versions/{vid}")
    def api_semantic_version_content(vid: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        from app.core.semantic_versions import read_version
        try:
            return {"id": vid, "content": read_version(Path(cfg.app.semantic_path), vid)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

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

    def _apply_semantic_content(content: str) -> dict[str, Any]:
        """校验 → 快照当前版本 → 写入 → 热重载。供 PUT 全文保存与版本回滚共用（#15）。"""
        from app.core.semantic_versions import validate_semantic, snapshot
        path = Path(cfg.app.semantic_path)
        check = validate_semantic(content)
        if not check["ok"]:
            raise HTTPException(status_code=400, detail="YAML 校验失败: " + "；".join(check["errors"]))
        # 写入前快照当前版本（可回滚）+ 兼容旧的单份 .bak
        try:
            snapshot(path)
            if path.exists():
                path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            logger.exception("write semantic file failed: %s", exc)
            raise HTTPException(status_code=500, detail="保存语义层文件失败，请稍后重试或联系管理员。")
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

    @app.post("/api/admin/semantic/validate")
    def api_admin_validate_semantic(req: SemanticPutReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        """保存前 dry-run 校验（不落盘）：返回 ok / 错误列表 / 计数摘要（#15）。"""
        from app.core.semantic_versions import validate_semantic
        return validate_semantic(req.content)

    @app.put("/api/admin/semantic")
    def api_admin_put_semantic(req: SemanticPutReq = Body(...), _: User = Depends(require_admin)) -> dict[str, Any]:
        return _apply_semantic_content(req.content)

    @app.post("/api/admin/semantic/rollback/{vid}")
    def api_admin_rollback_semantic(vid: str, _: User = Depends(require_admin)) -> dict[str, Any]:
        """回滚到历史版本：读取该版本 → 走与保存一致的校验/快照/写入/热重载（#15）。"""
        from app.core.semantic_versions import read_version
        try:
            content = read_version(Path(cfg.app.semantic_path), vid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        result = _apply_semantic_content(content)
        result["rolled_back_to"] = vid
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
        from app.core.concurrency import get_chat_guard
        # 全局在途并发闸（P0-3）：满了短暂等待后 429 泄洪，避免高峰堆到 LLM 超时。
        guard = get_chat_guard()
        if not guard.try_acquire():
            raise HTTPException(status_code=429, detail="当前问数服务繁忙，请稍后重试。")
        try:
            set_request_provider(req.llm_provider)
            return _do_chat(get_pipe(), get_conversation_store(), user, req, on_event=None)
        finally:
            guard.release()

    @app.post("/api/chat/stream")
    @_chat_limit("30/minute")
    async def api_chat_stream(
        request: Request,
        req: ChatRequest = Body(...),
        token: Optional[str] = Query(None),
        authorization: Optional[str] = Header(None),
    ) -> StreamingResponse:
        # SSE 与普通接口共用统一鉴权：含 must_change_password 拦截，杜绝绕过改密限制。
        bearer = _bearer_token(authorization) or (token or "")
        user = _authenticate_or_403(bearer, request.url.path)

        pipe = get_pipe()
        store = get_conversation_store()
        session_id = req.conversation_id
        if session_id:
            sess = store.get_session(session_id)
            if not sess or sess.user_id != user.id:
                raise HTTPException(status_code=404, detail="conversation not found")

        # 全局在途并发闸（P0-3）：鉴权/会话校验通过后再占名额（404 不消耗名额）。
        # 异步端点用非阻塞获取（timeout=0），绝不阻塞事件循环；满了立即 429 泄洪。
        from app.core.concurrency import get_chat_guard
        guard = get_chat_guard()
        if not guard.try_acquire(timeout=0.0):
            raise HTTPException(status_code=429, detail="当前问数服务繁忙，请稍后重试。")

        try:
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
        except BaseException:
            # 名额已占但还没把所有权交给 gen()（极少见，如 run_in_executor 失败）→ 立即归还。
            guard.release()
            raise

        async def gen() -> AsyncGenerator[str, None]:
            try:
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
            finally:
                # gen() 被消费完 / 客户端断开 / 异常，都会走到这里 → 归还在途名额。
                guard.release()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # 会话历史路由已拆到 app/api/routes/conversations.py（见上方 include_router）。

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


    # 会话文件夹 + 收藏路由已拆到 app/api/routes/folders.py（见下方 include_router）。

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

def _do_chat(pipe: Pipeline, store, user: User, req: ChatRequest, *, on_event=None) -> dict[str, Any]:
    """实际跑问数 + 落地会话消息 + 落地审计日志，返回响应字典。

    所有内部异常都被吞掉，返回 friendly_error。trace_id 让管理员能在日志中追查。
    永远 200 OK，前端按 ok=true/false 区分。
    """
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    try:
        # 1) 既有会话归属校验（此刻**不创建**任何会话）。
        session_id = req.conversation_id
        existing_session = bool(session_id)
        if session_id:
            sess = store.get_session(session_id)
            if not sess or sess.user_id != user.id:
                # 不暴露 "conversation not found"，给统一友好提示
                return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="会话不存在或无权访问")

        # 2) 输入校验**前置**（审计 P1）：校验未过绝不建会话，杜绝"失败留下无消息空会话"。
        question = (req.question or "").strip()
        if not question:
            return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="问题不能为空")
        if len(question) > 8000:
            return friendly_error("INPUT_INVALID", trace_id=trace_id, extra="问题过长（超过 8000 字符）")

        smartq_cube_ids = [str(c).strip() for c in (getattr(req, "smartq_cube_ids", []) or []) if str(c or "").strip()]
        smartq_cube_ids = list(dict.fromkeys(smartq_cube_ids))
        if smartq_cube_ids:
            # SmartQ 分支（审计 P1）：会话由 execute_smartq_query 在**成功落库**时创建。
            # 这里绝不预建会话 —— SmartQ 未配置 / 越权 / 查询失败都不会留下空会话。
            if on_event:
                on_event(_simple_event("smartq", "start", {"cube_count": len(smartq_cube_ids)}))
            started = datetime.utcnow()
            try:
                from app.integrations.smartq.api import execute_smartq_query
                payload = execute_smartq_query(
                    user=user,
                    question=question,
                    cube_ids=smartq_cube_ids,
                    conversation_id=session_id or None,
                    persist=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("[trace=%s user=%s] smartq branch crashed: %s", trace_id, user.username, exc)
                return friendly_error("CHAT_FAILED", trace_id=trace_id, extra="智能小Q查询失败")
            if not payload.get("ok"):
                err = str(payload.get("error") or "智能小Q查询失败")
                if on_event:
                    on_event(_simple_event("smartq", "error", {"error": err}))
                try:
                    get_query_log_store().record(
                        trace_id=trace_id, user_id=user.id, username=user.username,
                        conversation_id=session_id or "", question=question,
                        plan={"source": "smartq", "cube_ids": smartq_cube_ids},
                        sql="", rows=0, elapsed_ms=0, cached=False,
                        needs_clarify=False, error=err,
                    )
                except Exception:
                    pass
                return friendly_error("CHAT_FAILED", trace_id=trace_id, extra=err[:80])
            # 成功：execute_smartq_query 已落库，拿到真实 conversation_id。
            real_cid = str(payload.get("conversation_id") or session_id or "")
            # 新会话场景补发 session 事件，让前端把 draft 迁移到真实会话（与普通问数对齐）。
            if on_event and not existing_session and real_cid:
                on_event(_simple_event("session", "ok", {"conversation_id": real_cid}))
            answer = normalize_chat_result(payload.get("answer"))
            sql_str = str(payload.get("sql") or "")
            rows = int(payload.get("rows") or 0)
            elapsed_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
            if on_event:
                on_event(_simple_event("smartq", "ok", {
                    "mode": (payload.get("smartq") or {}).get("mode"),
                    "cube_count": len(smartq_cube_ids),
                    "rows": rows,
                }))
            try:
                get_query_log_store().record(
                    trace_id=str(payload.get("trace_id") or trace_id), user_id=user.id, username=user.username,
                    conversation_id=real_cid, question=question,
                    plan={"source": "smartq", "mode": (payload.get("smartq") or {}).get("mode"), "cube_ids": smartq_cube_ids},
                    sql=sql_str, rows=rows, elapsed_ms=elapsed_ms, cached=False,
                    needs_clarify=False, error="",
                )
            except Exception as exc:
                logger.warning("query_log record failed: %s", exc)
            return {
                "ok": True,
                "trace_id": payload.get("trace_id") or trace_id,
                "conversation_id": real_cid,
                "question": question,
                "answer": answer,
                "plan": {},
                "sql": sql_str,
                "rows": rows,
                "cached": False,
                "elapsed_ms": elapsed_ms,
                "smartq": payload.get("smartq") or {},
            }

        # 3) 普通问数：到这里（输入校验全过）才创建会话，并补发 session 事件。
        if not session_id:
            session = store.create_session(user.id, title=_short_title(question))
            session_id = session.id
        if on_event:
            on_event(_simple_event("session", "ok", {"conversation_id": session_id}))

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


def _ip_is_local_or_private(ip: str) -> bool:
    """回环 / 内网 IP 判定（nginx 同机反代时 client 即 127.0.0.1）。"""
    if not ip:
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def _metrics_access_allowed(*, is_local: bool, client_ip: str, auth_header: str, token: str) -> bool:
    """/metrics 访问控制（P1）：
      · 本地/开发：放开，方便调试；
      · 生产：仅允许 localhost / 内网 IP，或携带正确的 METRICS_TOKEN（Bearer）。
    Prometheus 抓取一般来自同机 nginx（127.0.0.1）或内网，天然满足；公网直连被拒。"""
    if is_local:
        return True
    if token and auth_header.strip() == f"Bearer {token}":
        return True
    return _ip_is_local_or_private(client_ip)


app = create_app()
