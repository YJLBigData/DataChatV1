# DataChat · 飞鹤小Q智能问数平台

面向飞鹤高管与数据团队的一体化数据智能平台。设计原则：**准确优先 / 稳定优先 / 可解释优先 / 宁可澄清不答错**。

当前仓库只保留 DataChat 主系统。

| 子系统 | 目录 | 技术栈 | 端口 | 职责 |
|--------|------|--------|------|------|
| DataChat 问数后端 | `backend/` | Python 3.11 · FastAPI | `8001` | 自然语言问数、鉴权、报告、飞书推送，并托管前端静态资源 |
| DataChat 前端 SPA | `frontend/` | React 18 · Vite · TS · Tailwind | 构建进 `backend/web/` | 问数对话界面、图表、管理后台 |

## 一、整体技术架构

完整架构流程图见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

### 1.1 系统拓扑

```text
                         浏览器（高管 / 数据工程师）
                                    |
                                    v
                       Nginx / 负载均衡 / 域名入口
                                    |
                                    v
   +----------------------------------------------------------------+
   | DataChat 后端 :8001                                             |
   | FastAPI + Uvicorn 多 worker                                     |
   |                                                                |
   | /web/   托管前端 SPA                                            |
   | /api/*  问数 / 鉴权 / 报告 / 管理后台                           |
   | /api/chat/stream  SSE 流式问数                                  |
   +-------------------+--------------------+-----------------------+
                       |                    |
                +------v------+      +------v------+
                | MySQL 业务库 |      | Redis 缓存 |
                +-------------+      +-------------+
                       |
                +------v----------------------------------+
                | SQLite 用户/权限/会话/日志/模板/预设     |
                +-----------------------------------------+
                       |
                       v
             飞书 Open API / DashScope / 飞鹤统一模型网关
```

### 1.2 核心设计思想

- **语义层驱动**：所有指标、维度、表、计算口径集中在 `backend/config/semantic.yaml`，新增表/指标优先改 YAML、热重载，不写死在代码里。
- **Plan-First NL2SQL**：LLM 只负责把自然语言翻译成受控的 `QueryPlan` IR，真正 SQL 由确定性编译器生成。
- **多层防护**：SQL 必经 `sqlglot` AST 校验、危险词黑名单、表白名单、强制 LIMIT；仅允许 `SELECT`。
- **可解释**：每次问数都返回口径、SQL、图表与业务文案；失败时给澄清选项，不硬答。
- **本地权限库**：用户、权限、会话、日志、模板和预设默认落本地 SQLite，业务数据仍走 MySQL 查询。

## 二、DataChat 问数后端（`backend/`）

FastAPI 应用入口是 `backend/app/main.py`，对外托管前端 SPA（`/web/`）并提供 REST + SSE 接口（`/api/*`）。

一次问数是一条七阶段 DAG，通过 SSE 把每个阶段状态实时推给前端：

```text
session -> cache -> retrieval -> plan -> compile -> guard -> execute -> answer
```

| 阶段 | 模块 | 做什么 |
|------|------|--------|
| session | `core/conversation.py` | 载入多轮上下文，继承上一轮指标、维度、筛选 |
| cache | `core/cache/redis_cache.py` | L1 问题缓存、L2 plan 缓存、L3 SQL 结果缓存 |
| retrieval | `core/retrieval/hybrid.py` | embedding + BM25 + alias 加权，定位相关指标和维度 |
| plan | `core/nl2sql/planner.py`、`plan.py` | LLM 生成受控 QueryPlan IR，模糊则返回澄清选项 |
| compile | `core/nl2sql/compiler.py` | 把 QueryPlan 确定性编译成 SQL |
| guard | `core/guard/sql_guard.py` | SQL AST 校验、危险词、表白名单、强制 LIMIT |
| execute | `core/exec/mysql_exec.py` | 连接池执行和超时控制 |
| answer | `core/answerer.py` | 生成业务文案、图表配置和口径解释 |

主要模块：

| 模块 | 说明 |
|------|------|
| `auth.py` / `user_directory.py` | bcrypt 口令 + JWT 签发/校验；用户目录支持本地 SQLite 或业务库 |
| `permissions.py` | 按 `user_id` 的行列级数据权限 |
| `config.py` | 集中配置 + 分层 `.env` 加载 |
| `semantic/layer.py` / `semantic_editor.py` | 业务语义层加载、热重载、在线编辑 |
| `nl2sql/` | QueryPlan IR、规划器、确定性 SQL 编译器 |
| `retrieval/hybrid.py` | 混合检索 |
| `guard/sql_guard.py` | SQL 安全护栏 |
| `exec/mysql_exec.py` | MySQL 执行器 |
| `cache/redis_cache.py` | Redis 三层缓存 |
| `llm/router.py` / `llm/feihe_gateway.py` | 百炼 OpenAI 兼容接口和飞鹤统一模型网关 |
| `answerer.py` | 高管级文案、图表、解释 |
| `feishu.py` | 飞书富文本卡片推送 |
| `report.py` / `report_templates.py` | DOCX 报告生成和模板管理 |
| `conversation.py` / `folders.py` / `query_log.py` | 会话、收藏夹、审计日志 |
| `orchestrator.py` | 问数 DAG 编排和 SSE 事件流 |
| `direct_sql.py` | 管理员直查 SQL 通道 |

### 2.1 接口总览

公开接口：`GET /health`、`GET /api/health`、`GET /api/bootstrap`、`GET /api/suggestions`、`POST /api/login`

普通用户接口：`/api/me`、`/api/me/password`、`/api/conversations*`、`POST /api/chat`、`POST /api/chat/stream`、`POST /api/feishu/push`、`POST /api/report/generate`、`GET /api/semantic/overview`

管理员接口：`/api/admin/users*`、`/api/admin/logs`、`/api/admin/semantic`、`/api/admin/permissions*`、`GET /api/admin/diagnostics`

## 三、DataChat 前端 SPA（`frontend/`）

React 18 + Vite + TypeScript + Tailwind，构建产物输出到 `backend/web/`，生产环境由后端统一托管。

```text
frontend/src/
├── App.tsx
├── api.ts
├── types.ts
├── components/
│   ├── LoginScreen / Hero / Composer / AnswerCard
│   ├── EChartView / ChartSwitcher / KpiCards / TableView
│   ├── StagePill / Sidebar / ConversationList / UserMenu
│   ├── ReportDownloadModal / PasswordModal / ErrorBoundary
│   └── pages/  管理后台：Users / Logs / Permissions / Semantic / LLMSettings / ReportTemplates
└── utils/chartDetect.ts
```

## 四、一键启动与默认账号

```bash
./start_local.sh
./start.sh
./start.sh --rebuild
./stop.sh
./stop.sh --redis
```

- 首次启动若 `backend/.env` 不存在，脚本会从 `backend/.env.example` 复制并退出，填好必要配置后重跑。
- 本机没有 MySQL 时，`start.sh` 会先尝试项目私有 MySQL（`.mysql/`），再用 Docker 兜底拉起 `datachat-mysql`。

默认管理员：

- 用户名：`admin`
- 密码：在 `backend/.env` 的 `DATACHAT_ADMIN_PASSWORD` 中自行设置；脚本不内置任何默认明文密码。

```bash
./scripts/reset_admin.sh
./scripts/reset_admin.sh 新的强密码
```

## 五、配置与环境变量

后端采用分层加载，高优先级在前：

```text
真实 os.environ > backend/.env > backend/config/runtime.local.env
                > <project>/.env > backend/config/env/<APP_ENV>.env
```

| 类别 | 关键变量 |
|------|----------|
| 应用 | `APP_ENV` `APP_HOST` `APP_PORT` `LOG_LEVEL` |
| 业务库 | `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`，兼容 `DB_*` 别名 |
| LLM | `DASHSCOPE_API_KEY` `DASHSCOPE_BASE_URL` `DASHSCOPE_MODEL` `DASHSCOPE_EMBED_MODEL` |
| 缓存 | `DATACHAT_REDIS_URL` `DATACHAT_CACHE_ENABLED` |
| 鉴权 | `JWT_SECRET` `DATACHAT_ADMIN_PASSWORD` `USER_DIRECTORY` `DATACHAT_AUTH_DB` |
| 飞书 | `FEISHU_WEBHOOK` 或 `FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_DEFAULT_USER_EMAIL` |

### 5.1 配置分层与密钥管理

Git 只承载代码和零密钥默认值，所有密钥永远只在服务器本地第三层，不入库、不被部署覆盖：

| 层 | 文件 | 入库 | `git pull` 部署是否覆盖 | 放什么 |
|----|------|:----:|:----:|--------|
| 代码 | `backend/` `frontend/` `scripts/` | 是 | 覆盖 | 纯代码与构建产物 |
| 非敏感默认 | `backend/config/env/{local,production}.env` | 是 | 覆盖 | `APP_ENV`、`DB_NAME`、网关 URL 等零密钥默认值 |
| 密钥 | `/opt/datachatv1/.env` | 否 | 不覆盖 | `DB_PASSWORD`、`JWT_SECRET`、`AES_KEY`、`DASHSCOPE_API_KEY`、`FEISHU_APP_ID/SECRET` |

红线：

1. 任何密钥只能写进服务器本地 `/opt/datachatv1/.env` 或本地开发 `backend/.env`。
2. `backend/config/env/production.env` 会入库且会被 `git pull` 覆盖，严禁写入密钥。
3. 切勿提交 `*/.env`、`*.key`、`*.pem`、`backend/logs/`、`*.db`。

## 六、飞书推送与排查

飞书凭证属于密钥，生产写进 `/opt/datachatv1/.env`，本地开发写进 `backend/.env`。

可配置任一组合：

- 群机器人 webhook：`FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`
- 企业自建应用：`FEISHU_APP_ID` + `FEISHU_APP_SECRET`，可按用户邮箱推给个人

改完密钥文件后需重启后端：

```bash
systemctl restart datachatv1
```

排查飞书真实错误：

```bash
grep -iE "feishu push (failed|crashed)" logs/backend*.log* | tail -n 30
grep -hiE "feishu|飞书" logs/backend*.log* | grep -iE "fail|error|exception|失败|未配置|code=|webhook|open_id" | tail -n 30
grep -E "^FEISHU_(WEBHOOK|APP_ID|APP_SECRET|DEFAULT_USER_EMAIL)=" backend/.env
```

## 七、问题排查

- 后端起不来：`tail -n 50 logs/backend.log`；端口占用：`lsof -ti tcp:8001`。
- MySQL/Redis 不通：用 `./scripts/check_server_env.sh` 体检环境。
- 问数报「权限不足」：到管理后台「权限」页为该用户开通数据权限。

## 八、数据范围与扩展

- 业务库 `chatbi` 包含销售等问数表，数据范围以服务器业务库为准。
- 新增表、指标、口径：编辑 `backend/config/semantic.yaml`，后台「语义层」热重载或重启后端。

## 九、测试

```bash
backend/.venv/bin/python -m pytest backend/tests/ -m "not e2e" -v
backend/.venv/bin/python -m pytest backend/tests/ -v
```

## 十、安全基线

- 所有 SQL 走 `sqlglot` AST guard、危险词、表白名单、自动 LIMIT；仅允许 `SELECT`。
- 除 `/api/login`、`/api/health`、`/api/bootstrap`、`/api/suggestions` 外，所有 `/api/*` 需 Bearer JWT。
- `/api/feishu/push` 禁止请求体指定任意 webhook/url，防 SSRF 和内网探测。
- 用户友好错误统一脱敏并返回 `trace_id`，真实异常只进后端日志。
- 切勿提交任何密钥、`backend/.env`、`backend/logs/`、数据库文件。

## 目录结构总览

```text
DataChatV1/
├── backend/                 ← FastAPI 问数后端（:8001）
│   ├── app/main.py          ← 入口（路由 + 鉴权 + 托管前端）
│   ├── app/core/            ← orchestrator / nl2sql / retrieval / guard / exec / cache / llm / feishu
│   ├── config/semantic.yaml ← 业务语义层
│   ├── config/env/          ← local.env / production.env 环境专属默认
│   ├── web/                 ← 前端构建产物
│   └── tests/               ← unit + api + e2e
├── frontend/                ← React + Vite SPA（构建进 backend/web/）
├── scripts/                 ← reset_admin / safe_push / check_server_env / init_local_mysql
├── logs/                    ← 运行期日志与本地 SQLite
├── start.sh / start_local.sh / stop.sh
└── README.md
```
