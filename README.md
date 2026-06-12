# DataChat · 飞鹤小Q 智能问数平台

面向飞鹤高管与数据团队的一体化数据智能平台。设计原则：**准确优先 / 稳定优先 / 可解释优先 / 宁可澄清不答错**。

仓库由三个相互独立、又通过 **统一登录（SSO）** 串联的子系统组成：

| 子系统 | 目录 | 技术栈 | 端口 | 职责 |
|--------|------|--------|------|------|
| **DataChat 问数后端** | `backend/` | Python 3.11 · FastAPI | `8001` | 自然语言问数（NL2SQL）、鉴权、报告、飞书推送，并托管前端静态资源 |
| **DataChat 前端 SPA** | `frontend/` | React 18 · Vite · TS · Tailwind | （构建进 `backend/web/`） | 问数对话界面、图表、管理后台 |
| **DataCode 写代码平台** | `datacode/` | Java 8 · Spring Boot | `18082` | 上传需求自动生成 Dataphin/MaxCompute SQL，并内置 CDP 数据上报 CLI 工具，复用 DataChat 登录态 |

---

## 一、整体技术架构

> 📐 **完整架构流程图**（Mermaid，含系统总览 / 七阶段管线 / 请求时序 / SSO 鉴权 / 配置分层 / 飞书路由 共 6 张）见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

### 1.1 系统拓扑

```
                         浏览器（高管 / 数据工程师）
                                    │
                 ┌──────────────────┴───────────────────┐
                 │ JWT（同一套登录态，SSO）              │
                 ▼                                       ▼
   ┌─────────────────────────────┐        ┌────────────────────────────────┐
   │  DataChat 后端  :8001         │        │  DataCode 平台  :18082          │
   │  FastAPI + Uvicorn(多 worker) │        │  Spring Boot                   │
   │                              │        │                                │
   │  /web/   ← 托管前端 SPA       │  /api/me│  DataChatSsoFilter             │
   │  /api/*  ← 问数 / 鉴权 / 报告 │◀───────│  （拦截 Bearer，回源校验后放行）│
   │  /api/chat/stream (SSE)      │  校验   │  → 生成本地影子用户            │
   └───────┬─────────┬───────────┘        └───────┬───────────────┬────────┘
           │         │                            │               │
     ┌─────▼───┐ ┌───▼────┐                 ┌─────▼─────┐   ┌──────▼───────┐
     │ MySQL    │ │ Redis  │                 │ DashScope │   │ MaxCompute / │
     │ chatbi   │ │ 三层缓存│                 │ qwen3.6   │   │ Dataphin     │
     │ + users  │ │ db=2   │                 │ 生成 SQL  │   │ 查询 / 血缘  │
     └──────────┘ └────────┘                 └───────────┘   └──────────────┘
           │
     ┌─────▼──────────────────────────────────┐
     │ 本地 SQLite（用户/权限/会话/日志/模板/预设）│
     └─────────────────────────────────────────┘
           │
           ▼ （可选）富文本卡片
     ┌──────────────┐
     │ 飞书 open API │
     └──────────────┘
```

### 1.2 核心设计思想

- **语义层驱动**：所有指标、维度、表、计算口径集中在 `backend/config/semantic.yaml`，新增表/指标只改 YAML、热重载即可，**不写代码**。
- **Plan-First NL2SQL**：LLM 只负责把自然语言翻译成受控的中间表示（QueryPlan IR），真正的 SQL 由**确定性编译器**生成，杜绝 LLM 直接吐 SQL 带来的不可控风险。
- **多层防护**：SQL 必经 `sqlglot` AST 校验 + 危险词黑名单 + 表白名单 + 自动 LIMIT；仅允许 `SELECT`。
- **可解释**：每次问数都返回口径、SQL、图表与高管级文案；失败给出澄清选项而非硬答。
- **统一登录**：DataChat 是唯一鉴权中心，DataCode 不再维护独立账号，凭 DataChat 的 JWT 即可访问。

---

## 二、DataChat 问数后端（`backend/`）

FastAPI 应用，入口 `backend/app/main.py`，对外既托管前端 SPA（`/web/`）也提供 REST + SSE 接口（`/api/*`）。

### 2.1 问数管线（Orchestrator）

一次问数是一条**七阶段 DAG**，通过 SSE 把每个阶段的状态实时推给前端（`backend/app/core/orchestrator.py`）：

```
session → cache → retrieval → plan → compile → guard → execute → answer
```

| 阶段 | 模块 | 做什么 |
|------|------|--------|
| **session** | `core/conversation.py` | 载入多轮上下文，自动继承上一轮的指标/维度/筛选 |
| **cache** | `core/cache/redis_cache.py` | L1 问题缓存 → L2 plan 缓存 → L3 SQL 结果缓存，逐层命中即短路返回 |
| **retrieval** | `core/retrieval/hybrid.py` | 千问 embedding 向量召回 + BM25 + alias 加权，定位相关指标/维度 |
| **plan** | `core/nl2sql/planner.py` · `plan.py` | LLM 生成受控 `QueryPlan` IR；模糊则 `needs_clarify` + `clarify_options` |
| **compile** | `core/nl2sql/compiler.py` | 把 QueryPlan **确定性**编译成 SQL（同比/环比/占比/TopN/趋势/累计/差值开箱即用） |
| **guard** | `core/guard/sql_guard.py` | sqlglot AST 解析 + 危险词 + 表白名单 + 强制 LIMIT，越权直接拦截 |
| **execute** | `core/exec/mysql_exec.py` | 连接池执行 + 语句超时（默认 15s） |
| **answer** | `core/answerer.py` | 生成高管级 narrative + 图表配置 + 口径解释 |

### 2.2 模块清单（`backend/app/core/`）

| 模块 | 说明 |
|------|------|
| `auth.py` / `user_directory.py` | bcrypt 口令 + JWT 签发/校验；用户目录支持本地 SQLite 或业务库两种后端 |
| `permissions.py` | 按 `user_id` 的行/列级数据权限 |
| `config.py` | 集中配置 + 分层 `.env` 加载（详见 [第六节](#六配置与环境变量)） |
| `semantic/layer.py` · `semantic_editor.py` | 业务语义层加载/热重载/在线编辑 |
| `nl2sql/` | Plan-First IR（`plan.py`）+ 规划器（`planner.py`）+ 确定性 SQL 编译器（`compiler.py`） |
| `retrieval/hybrid.py` | 混合检索（向量 + BM25 + alias） |
| `guard/sql_guard.py` | SQL 安全护栏 |
| `exec/mysql_exec.py` | MySQL 执行器（连接池 + 超时 + 健康检查） |
| `cache/redis_cache.py` | 三层 Redis 缓存 |
| `llm/router.py` | LLM 路由：统一走百炼（DashScope）OpenAI 兼容接口 |
| `llm/feihe_gateway.py` | 飞鹤统一模型网关（ADP），AES 签名，密钥仅从环境变量读取 |
| `llm_presets.py` / `llm_settings.py` | 多套 LLM 预设（不同 AK + 不同模型并存切换），SQLite 持久化、热生效 |
| `answerer.py` | 高管级文案 + 图表 + 解释 |
| `feishu.py` | 飞书富文本卡片推送（webhook 或应用 token + open_id，详见 [第七节](#七飞书推送与排查)） |
| `report.py` / `report_templates.py` | DOCX 报告生成 + 模板管理 |
| `conversation.py` / `folders.py` / `query_log.py` | 会话、收藏夹、审计日志（均为本地 SQLite） |
| `orchestrator.py` | 问数 DAG 编排 + SSE 事件流 |
| `direct_sql.py` | 管理员直查 SQL 通道 |
| `tasks.py` | Celery 异步任务骨架 |

### 2.3 接口总览

公开（无需鉴权）：`GET /health`、`GET /api/health`、`GET /api/bootstrap`、`GET /api/suggestions`、`POST /api/login`

普通用户（需 Bearer JWT）：`/api/me`、`/api/me/password`、`/api/conversations*`、`POST /api/chat`、`POST /api/chat/stream`（SSE）、`POST /api/feishu/push`、`POST /api/report/generate`、`GET /api/semantic/overview`

管理员专享：`/api/admin/users*`、`/api/admin/logs`、`/api/admin/semantic`（GET/PUT 热重载）、`/api/admin/permissions*`、`GET /api/admin/diagnostics`（DB/Redis/LLM/语义层/飞书一站式体检）

---

## 三、DataChat 前端 SPA（`frontend/`）

React 18 + Vite + TypeScript + Tailwind，构建产物直接输出到 `backend/web/`，由后端统一托管（生产无需独立前端服务）。

```
frontend/src/
├── App.tsx                     ← 路由与全局状态
├── api.ts                      ← JWT / SSE 客户端，统一错误文案
├── types.ts
├── components/
│   ├── LoginScreen / Hero / Composer / AnswerCard        ← 问数主流程
│   ├── EChartView / ChartSwitcher / KpiCards / TableView ← 可视化（ECharts 6）
│   ├── StagePill / Sidebar / ConversationList / UserMenu ← 框架与会话
│   ├── ReportDownloadModal / PasswordModal / ErrorBoundary
│   └── pages/  ← 管理后台：Users / Logs / Permissions / Semantic / LLMSettings / ReportTemplates
└── utils/chartDetect.ts        ← 自动图表类型推断
```

技术要点：SSE 流式渲染各问数阶段、ECharts 动态选图、framer-motion 动效、按角色（admin/user）裁剪后台菜单。

---

## 四、DataCode 写代码平台（`datacode/`）

Spring Boot 应用（`com.feihe.datacode`），默认端口 `18082`，不占用 `8000/8001`。同一 Maven 模块内还保留了 CDP 数据上报 CLI 工具（`com.feihe`）。

### 4.1 智能写代码平台

- 上传 Excel/CSV 需求文件，Excel 多 Sheet 自动拆分为多个需求（`ExcelStandardizationService` / `RequirementParserService`）。
- 填写模型提示词、源表结构、样例数据、备注，调用 `qwen3.6-max-preview` 生成 Dataphin/MaxCompute 建表与写入 SQL（`CodeGenerationService` / `ModelClientService`）。
- 自动校验建表、写入、默认 `ds` 分区、`${bizdate}`、高风险语句与基础语法（`SqlValidatorService`）。
- 管理员可直查 MaxCompute/Dataphin 数据、任务代码、表级/任务级血缘（`DataphinService` + `tools/*LineageExcelExporter`）。
- 保留用户管理、生成日志、模型调用日志（`SqliteStore` / `CodeLogService`）。

### 4.2 与 DataChat 的 SSO 集成

DataCode 默认 **不再维护独立账号密码**，统一复用 DataChat 登录态：

1. 前端带 `Authorization: Bearer <DataChat JWT>` 访问 DataCode `/api/*`。
2. `DataChatSsoFilter` 拦截请求，取出 Bearer。
3. `DataChatSsoClient` 回源调用 DataChat `GET /api/me` 校验（按 token 内存缓存 60s，命中 401 立即失效）。
4. 校验通过 → `AuthService` 建立/更新**本地影子用户**并把 token 透传成既有控制器期望的 Cookie（`SsoCookieRequestWrapper`），老控制器零改动；校验失败 → 直接 401。

相关配置见 `datacode/config/datacode.env.example`：`DATACODE_SSO_ENABLED`、`DATACODE_SSO_DATACHAT_BASE_URL`（同机部署填回环地址）、`DATACODE_SSO_CACHE_SECONDS`。

### 4.3 CDP 数据上报 CLI

`com.feihe.FeiHeCdpApplication` 是一个独立的命令行 JAR，把 MaxCompute（ODPS）数据多线程批量上报到 CDP（客户/实体/事件三类）。详细参数、请求结构与 Dataphin 取数示例见 `datacode/README.md`。

---

## 五、一键启动与默认账号

### 5.1 启动

```bash
./start_local.sh   # 本地启动：Redis + MySQL(chatbi/users) + 后端 + 编译前端，访问 http://127.0.0.1:8001/web/
./start.sh         # 同上的底层启动脚本（[1/7]~[7/7] 依次：工具链→.env→Redis→MySQL→Python→前端→Uvicorn）
./start.sh --rebuild   # 强制重新构建前端
./stop.sh          # 停止后端（保留 Redis）
./stop.sh --redis  # 停止后端 + Redis
```

- 首次启动若 `backend/.env` 不存在，脚本会从 `backend/.env.example` 复制并退出，填好 `DASHSCOPE_API_KEY` 后重跑即可。
- 本机没有 MySQL 时，`start.sh` 会先尝试项目私有 MySQL（`.mysql/`），再用 Docker 兜底拉起 `datachat-mysql`，自动建 `chatbi` 库与样例表。
- DataCode 单独启动：`cd datacode && cp config/datacode.env.example config/datacode.env && ./start_datacode_java.sh`（停止 `./stop_datacode_java.sh`）。

### 5.2 默认账号与重置

- 用户名：`admin`
- 密码：在 `backend/.env` 的 `DATACHAT_ADMIN_PASSWORD` 中自行设置；脚本不内置任何默认明文密码。

```bash
./scripts/reset_admin.sh                # 交互式重置
./scripts/reset_admin.sh 新的强密码      # 直接传参
```

---

## 六、配置与环境变量

后端采用**分层加载**（高优先级在前，已存在的键不被覆盖，见 `core/config.py`）：

```
真实 os.environ  >  backend/.env  >  backend/config/runtime.local.env
                 >  <project>/.env  >  backend/config/env/<APP_ENV>.env
```

`APP_ENV=local|production` 决定加载哪套环境专属默认（本地用本地 MySQL，服务器用服务器 MySQL）。

| 类别 | 关键变量 |
|------|----------|
| 应用 | `APP_ENV` `APP_HOST` `APP_PORT`（8001）`LOG_LEVEL` |
| 业务库 | `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`（兼容 `DB_*` 别名） |
| LLM | `DASHSCOPE_API_KEY` `DASHSCOPE_BASE_URL` `DASHSCOPE_MODEL`（qwen3.6-max-preview）`DASHSCOPE_EMBED_MODEL` |
| 缓存 | `DATACHAT_REDIS_URL`（redis://127.0.0.1:6379/2）`DATACHAT_CACHE_ENABLED` |
| 鉴权 | `JWT_SECRET` `DATACHAT_ADMIN_PASSWORD` `USER_DIRECTORY` `DATACHAT_AUTH_DB` |
| 飞书 | `FEISHU_WEBHOOK` 或 `FEISHU_APP_ID`+`FEISHU_APP_SECRET`+`FEISHU_DEFAULT_USER_EMAIL` |
| DataCode | `DATACODE_LLM_API_KEY` `ALIYUN_DATA_PLATFORM_AK/SK` `DATACODE_SSO_*` |

### 6.1 配置分层与密钥管理（生产红线）

配置分三层，**Git 只承载前两层（代码 + 零密钥默认值），所有密钥永远只在服务器本地第三层**，不入库、不被部署覆盖：

| 层 | 文件 | 入库 | `git pull` 部署是否覆盖 | 放什么 |
|----|------|:----:|:----:|--------|
| ① 代码 | `backend/` `frontend/` `datacode/` … | ✅ | 覆盖（即更新代码） | 纯代码与构建产物 |
| ② 非敏感默认 | `backend/config/env/{local,production}.env` | ✅ | 覆盖 | `APP_ENV`、`DB_NAME`、网关 URL 等**零密钥**默认值 |
| ③ **密钥（服务器本地）** | **`/opt/datachatv1/.env`**（systemd `EnvironmentFile` 指向，`chmod 600`） | ❌ | **不覆盖** | `DB_PASSWORD`、`JWT_SECRET`、`AES_KEY`、`DASHSCOPE_API_KEY`、`FEISHU_APP_ID/SECRET` 等 |

优先级（高 → 低）：systemd 注入的真实环境变量 > `backend/.env` > `backend/config/env/<APP_ENV>.env`。第③层因此始终覆盖第②层。

> 🔴 **红线**：
> 1. 任何密钥**只能**写进第③层服务器本地文件（生产 `/opt/datachatv1/.env`；本地开发 `backend/.env`），二者均已被 `.gitignore` 忽略。
> 2. 第②层 `production.env`/`local.env` 会被 `git pull` 覆盖且**入库**，**严禁**写入任何密钥（否则既泄露又会在下次部署被清空）。
> 3. 切勿提交 `*/.env`、`*.key/*.pem`、`backend/logs/`、`*.db`、私有部署脚本（`deploy/`、`start_server.sh`）。已有 `.gitignore` 覆盖，提交前可用 `git ls-files | grep -iE '\.env$|\.key$'` 复核。

---

## 七、飞书推送与排查

### 7.1 配置

飞书凭证属于密钥（[第③层](#61-配置分层与密钥管理生产红线)），**生产写进 `/opt/datachatv1/.env`（systemd `EnvironmentFile`），本地开发写进 `backend/.env`**，切勿写进 `production.env`。至少配置以下任一组合（`core/feishu.py`）：

- **群机器人 webhook（最简单）**：`FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`
- **企业自建应用（可按邮箱推给个人）**：`FEISHU_APP_ID` + `FEISHU_APP_SECRET`（+ 普通用户的飞书邮箱，由用户资料里的 `email` 决定）

推送路由：普通用户用自己绑定的飞书邮箱走「应用 token → open_id」个人推送；管理员**必须显式选收件人邮箱**，否则只能落到 `FEISHU_WEBHOOK` 群兜底或 `FEISHU_DEFAULT_USER_EMAIL`，两者都没配则报「未配置」。改完密钥文件后需 `systemctl restart datachatv1` 生效。

### 7.2 “飞书推送失败”排查

前端只会看到「飞书推送失败，请确认已配置推送或联系管理员」这类**脱敏**提示，**真正的报错（含飞书返回码）只写在后端日志里**，按 `trace_id` 串联。排查步骤见 [问题排查 → 飞书](#八问题排查)。

---

## 八、问题排查

### 飞书推送失败

后端把真实异常记到日志（默认 JSON 行格式），用户侧只回脱敏文案。日志位置因部署方式而异：本地 `start.sh` 写单文件 `logs/backend.log`；服务器部署按时间轮转为 `logs/backend_YYYYMMDD_HHMMSS.log`（及 logrotate 产生的 `*.log-YYYYMMDD` 旧档）。**先看日志拿到真实返回码**：

```bash
# 1) 查最近的飞书真实报错（用通配同时覆盖单文件与轮转日志，最新在最下面）
grep -iE "feishu push (failed|crashed)" logs/backend*.log* | tail -n 30
# 若无结果，放宽匹配：
grep -hiE "feishu|飞书" logs/backend*.log* | grep -iE "fail|error|exception|失败|未配置|code=|webhook|open_id" | tail -n 30

# 2) 确认服务器 backend/.env 到底配没配飞书（任一组合非空才算配置）
grep -E "^FEISHU_(WEBHOOK|APP_ID|APP_SECRET|DEFAULT_USER_EMAIL)=" backend/.env

# 3) 找出当前运行进程正在写哪个日志文件（轮转后方便定位实时日志去 tail -f）
lsof -p "$(cat uvicorn.pid 2>/dev/null)" 2>/dev/null | grep -iE 'backend_.*\.log'

# 4) 用管理员 token 一站式体检，看 feishu.configured 是否为 true
curl -s http://127.0.0.1:8001/api/admin/diagnostics \
  -H "Authorization: Bearer <管理员JWT>" | python3 -m json.tool

# 5) 校验服务器到飞书开放平台的网络连通性（应返回 JSON，code 非 0 也代表网络通）
curl -s -m 10 -X POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal \
  -H 'Content-Type: application/json' -d '{"app_id":"x","app_secret":"x"}'

# 6) 若用的是群机器人 webhook，直接对 webhook 发一条测试卡片（把 URL 换成 .env 里的真实值）
curl -s -m 10 -X POST "$(grep -E '^FEISHU_WEBHOOK=' backend/.env | cut -d= -f2-)" \
  -H 'Content-Type: application/json' \
  -d '{"msg_type":"text","content":{"text":"DataChat 飞书连通性测试"}}'
```

按日志里的真实报错对症处理：

| 日志中的关键信息 | 含义 | 处理 |
|------------------|------|------|
| `飞书未配置` | 服务器 `backend/.env` 没有任何 `FEISHU_*` | 补 `FEISHU_WEBHOOK` 或 APP_ID/SECRET 后重启后端 |
| `webhook 推送失败 [code=...]` | 飞书拒绝（签名/IP 白名单/机器人被移除/卡片格式） | 按返回 code 核对：机器人是否仍在群内、是否开了 IP 白名单/签名校验 |
| `tenant_access_token 失败` | APP_ID/SECRET 错误或应用被停用 | 到飞书开放平台核对凭证与应用状态 |
| `未找到 email=... 对应的飞书用户` | 该用户邮箱不在企业内 / 应用无通讯录权限 | 校对用户 `email`，给应用开通「读取通讯录」权限 |
| `网络失败` / `连接失败` | 服务器无法访问 `open.feishu.cn` | 检查出网防火墙 / 代理（见上面第 4 步） |

> 改完 `backend/.env` 后需**重启后端**令配置生效（本地 `./stop.sh && ./start.sh`；服务器按部署脚本/进程管理方式重启，如 `deploy/` 下的脚本或 `kill $(cat uvicorn.pid)` 后重新拉起）。

### 其他

- 后端起不来：`tail -n 50 logs/backend.log`；端口占用 `lsof -ti tcp:8001`。
- MySQL/Redis 不通：用 `./scripts/check_server_env.sh` 体检环境。
- 问数报「权限不足」：到管理后台「权限」页为该用户开通数据权限。

---

## 九、数据范围与扩展

- 业务库 `chatbi` 共 5 张表（`ads_bi_hs_sale_info_df` 等），数据区间 **2025-01 ~ 2026-04**。
- **新增表/指标/口径**：编辑 `backend/config/semantic.yaml` → 后台「语义层」热重载或重启后端，无需改代码。

---

## 十、测试

```bash
# 单元 + API（不需 LLM/DB）——数量以 pytest 实际输出为准
backend/.venv/bin/python -m pytest backend/tests/ -m "not e2e" -v

# 全量（含 LLM/DB Golden Case，需要 .env 已配好）
backend/.venv/bin/python -m pytest backend/tests/ -v
```

---

## 十一、安全基线

- 所有 SQL 走 `sqlglot AST guard` + 危险词 + 表白名单 + 自动 LIMIT；仅允许 `SELECT`，禁止 `INSERT/UPDATE/DELETE/DROP/TRUNCATE`、多语句、`SELECT *`。
- 鉴权：除 `/api/login`、`/api/health`、`/api/bootstrap`、`/api/suggestions` 外，所有 `/api/*` 需 Bearer JWT；按 `user_id` 隔离会话与权限。
- `/api/feishu/push` 禁止请求体指定任意 webhook/url（防 SSRF/内网探测），推送目标只允许服务端配置或用户自己的飞书邮箱。
- 用户友好错误统一脱敏 + `trace_id`，真实异常只进后端日志。
- 切勿提交任何密钥、`backend/.env`、`backend/logs/`。

---

## 目录结构总览

```
DataChatV1/
├── backend/                 ← FastAPI 问数后端（:8001）
│   ├── app/main.py          ← 入口（路由 + 鉴权 + 托管前端）
│   ├── app/core/            ← orchestrator / nl2sql / retrieval / guard / exec / cache / llm / feishu …
│   ├── config/semantic.yaml ← 业务语义层（可热编辑）
│   ├── config/env/          ← local.env / production.env 环境专属默认
│   ├── web/                 ← 前端构建产物（由 vite 输出）
│   ├── tests/               ← unit + api + e2e
│   └── .env(.example) / requirements.txt
├── frontend/                ← React + Vite SPA（构建进 backend/web/）
├── datacode/                ← Spring Boot 写代码平台（:18082）+ CDP 上报 CLI + DataChat SSO
├── scripts/                 ← reset_admin / safe_push / check_server_env / init_local_mysql
├── logs/                    ← 运行期日志与本地 SQLite
├── start.sh / start_local.sh / stop.sh
└── README.md
```
