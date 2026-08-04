# DataChat · 飞鹤小Q智能问数平台

面向飞鹤高管、业务分析人员与数据团队的一体化数据智能平台。平台以受控语义层和确定性 SQL 编译为核心，提供标准问数、专家团分析、Quick BI 智能小Q、可信结果导出、报告与飞书推送。设计原则：**准确优先 / 稳定优先 / 可解释优先 / 宁可澄清不答错**。

当前仓库只保留 DataChat 主系统。

> **版本状态**：仓库尚未创建 Git 版本标签或正式 Release，已审计的代码提交范围截至 `c622885a`（2026-06-25）。所有未发布的重要变化记录在 [CHANGELOG.md](CHANGELOG.md) 的 `[Unreleased]` 中。

### 核心能力

| 能力 | 面向场景 | 关键保障 |
|------|----------|----------|
| 标准问数 | 指标查询、TopN、占比、差异、同比环比、趋势与多轮下钻 | 语义检索、`QueryPlan`、准确率复核、SQL Guard、结果校验 |
| 专家团 | 销售、渠道、用户运营、市场、财务及审计协同分析 | 决策总监路由、真实数据取数、异步任务、报告合成 |
| 智能小Q | 使用 Quick BI 数据集问数 | 数据集状态校验、多数据集查询、失败降级 |
| 导出与汇报 | Excel 导出、DOCX 报告、飞书卡片 | 服务端可信结果、用户归属校验、异步队列与过期清理 |
| 平台管理 | 用户权限、语义层、模型预设、日志和报告模板 | JWT、行列级权限、版本记录、连通性测试 |

| 子系统 | 目录 | 技术栈 | 端口 | 职责 |
|--------|------|--------|------|------|
| DataChat 服务端 | `backend/` | Python 3.11 · FastAPI | `8001` | 问数管线、专家团、智能小Q、导出、鉴权、报告、飞书推送，并托管前端静态资源 |
| DataChat 前端 SPA | `frontend/` | React 18 · Vite · TypeScript · Tailwind | 构建进 `backend/web/` | 问数、专家团、图表、数据集选择、导出队列和管理后台 |

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
   | FastAPI + 单 Uvicorn worker + 显式线程池/并发闸                 |
   |                                                                |
   | /web/   托管前端 SPA                                            |
   | /api/*  问数 / 专家团 / 智能小Q / 导出 / 管理后台               |
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
       飞书 Open API / DashScope / 飞鹤统一模型网关 / Quick BI SmartQ
```

### 1.2 核心设计思想

- **语义层驱动**：所有指标、维度、表、计算口径集中在 `backend/config/semantic.yaml`，新增表/指标优先改 YAML、热重载，不写死在代码里。
- **Plan-First NL2SQL**：LLM 只负责把自然语言翻译成受控的 `QueryPlan` IR，真正 SQL 由确定性编译器生成。
- **准确率闭环**：编译前由 `AccuracyCritic` 检查指标、维度、筛选、排序和澄清必要性；执行后由 `ResultValidator` 检查返回列、TopN、排序、空结果和空指标。
- **多层防护**：SQL 必经 `sqlglot` AST 校验、危险词黑名单、表白名单、强制 LIMIT；仅允许 `SELECT`。
- **可解释**：每次问数都返回口径、SQL、图表与业务文案；失败时给澄清选项，不硬答。
- **本地权限库**：用户、权限、会话、日志、模板和预设默认落本地 SQLite，业务数据仍走 MySQL 查询。
- **可控并发**：生产基线使用单 Uvicorn worker、显式线程池、全局在途并发闸和数据库连接池，任务状态保持进程内一致。

## 二、DataChat 问数后端（`backend/`）

FastAPI 应用入口是 `backend/app/main.py`，对外托管前端 SPA（`/web/`）并提供 REST + SSE 接口（`/api/*`）。

一次问数由受控阶段管线执行，通过 SSE 把阶段状态实时推给前端：

```text
session/scope -> cache -> retrieval -> plan -> critic -> permissions
              -> compile -> guard -> execute -> answer -> validate
```

| 阶段 | 模块 | 做什么 |
|------|------|--------|
| session | `core/conversation.py` | 载入多轮上下文，继承上一轮指标、维度、筛选 |
| cache | `core/cache/redis_cache.py` | L1 问题缓存、L2 plan 缓存、L3 SQL 结果缓存 |
| retrieval | `core/retrieval/hybrid.py` | embedding + BM25 + alias 加权，定位相关指标和维度 |
| plan | `core/nl2sql/planner.py`、`plan.py` | LLM 生成受控 QueryPlan IR，模糊则返回澄清选项 |
| critic | `core/nl2sql/accuracy_critic.py` | 编译前复核计划，最多执行一次确定性修复，无法安全修复则转澄清 |
| permissions | `core/permissions.py` | 应用表、列和行级数据权限，执行前再次按 SQL 校验 |
| compile | `core/nl2sql/compiler.py` | 把 QueryPlan 确定性编译成 SQL |
| guard | `core/guard/sql_guard.py` | SQL AST 校验、危险词、表白名单、强制 LIMIT |
| execute | `core/exec/mysql_exec.py` | 连接池执行和超时控制 |
| answer | `core/answerer.py` | 生成业务文案、图表配置和口径解释 |
| validate | `core/nl2sql/result_validator.py` | 校验结果列、行数、排序、空结果和主指标空值，风险写入解释信息 |

主要模块：

| 模块 | 说明 |
|------|------|
| `auth.py` / `user_directory.py` | bcrypt 口令 + JWT 签发/校验；用户目录支持本地 SQLite 或业务库 |
| `permissions.py` | 按 `user_id` 的行列级数据权限 |
| `config.py` | 集中配置 + 分层 `.env` 加载 |
| `semantic/layer.py` / `semantic_editor.py` | 业务语义层加载、热重载、在线编辑 |
| `nl2sql/` | QueryPlan IR、规划器、准确率复核、确定性 SQL 编译器和结果校验器 |
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
| `expert_team/` | 决策总监、多领域专家、知识与技能定义、异步任务和报告合成 |
| `integrations/smartq/` | Quick BI 智能小Q签名、数据集、问数、归一化和降级策略 |
| `exports/` | 基于可信问数结果的异步 Excel 导出队列 |
| `concurrency.py` | 问数并发闸、拒绝计数和运行指标 |

### 2.1 接口总览

公开接口：`GET /health`、`GET /api/health`、`GET /api/bootstrap`、`GET /api/suggestions`、`POST /api/login`

普通用户接口：`/api/me*`、`/api/conversations*`、`/api/folders*`、`POST /api/chat`、`POST /api/chat/stream`、`/api/expert-team/*`、`/api/smartq/*`、`/api/exports/*`、`POST /api/feishu/push`、`POST /api/report/generate`、`GET /api/semantic/overview`

管理员接口：`/api/admin/users*`、`/api/admin/logs`、`/api/admin/semantic*`、`/api/admin/permissions*`、`/api/admin/llm-*`、`GET /api/admin/diagnostics`

## 三、DataChat 前端 SPA（`frontend/`）

React 18 + Vite + TypeScript + Tailwind，构建产物输出到 `backend/web/`，生产环境由后端统一托管。

```text
frontend/src/
├── App.tsx
├── api.ts                  # 对各领域 API 模块的统一导出
├── api/                    # auth / chat / expert / smartq / exports / admin
├── types.ts
├── components/
│   ├── LoginScreen / Hero / Composer / AnswerCard
│   ├── EChartView / ChartSwitcher / KpiCards / TableView
│   ├── StagePill / Sidebar / ConversationList / UserMenu
│   ├── SmartQDatasetButton / ExportQueueButton
│   ├── ReportDownloadModal / PasswordModal / ErrorBoundary
│   ├── expert/             # 专家卡片、编辑器、结果卡片
│   └── pages/              # Chat / ExpertPanel / Users / Logs / Permissions / Semantic / LLMSettings / ReportTemplates
├── hooks/                  # useChat / useExpertTeam / useConversations / useLLMProviders
└── utils/chartDetect.ts
```

## 四、一键启动与默认账号

```bash
./start_local.sh
bash scripts/start_dev.sh
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
| LLM | `DASHSCOPE_*`、`FEIHE_AGENT_*`、`LLM_READ_TIMEOUT` |
| 智能小Q | `SMARTQ_ENABLED` `SMARTQ_SERVER_DOMAIN` `SMARTQ_API_KEY/SECRET` `SMARTQ_USER_TOKEN` |
| 缓存 | `DATACHAT_REDIS_URL` `DATACHAT_CACHE_ENABLED` |
| 容量 | `CHAT_THREAD_POOL_SIZE` `CHAT_MAX_INFLIGHT` `DB_POOL_SIZE` `DB_MAX_OVERFLOW` |
| 后台任务 | `EXPERT_JOB_MAX_WORKERS` `EXPORT_JOB_MAX_WORKERS` |
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
- 当前语义层已覆盖销售汇总、门店明细、潜客/会员与激活一线人员指标；门店明细支持地区、连锁系统、渠道等级、产品品类和日均口径。
- “本月/上月”等相对时间优先读取目标表真实最新月份；读取失败时才使用语义层配置的安全回退值。
- 新增表、指标、口径：编辑 `backend/config/semantic.yaml`，后台「语义层」热重载或重启后端。

## 九、测试

```bash
backend/.venv/bin/python -m pytest backend/tests/ -m "not e2e" -v
backend/.venv/bin/python -m pytest backend/tests/ -v
backend/.venv/bin/python -m pytest \
  backend/tests/test_accuracy_critic_0624.py \
  backend/tests/test_result_validator_0624.py \
  backend/tests/test_accuracy_pipeline_integration_0624.py -v
```

## 十、安全基线

- 所有 SQL 走 `sqlglot` AST guard、危险词、表白名单、自动 LIMIT；仅允许 `SELECT`。
- 除 `/api/login`、`/api/health`、`/api/bootstrap`、`/api/suggestions` 外，所有 `/api/*` 需 Bearer JWT。
- `/api/feishu/push` 禁止请求体指定任意 webhook/url，防 SSRF 和内网探测。
- 用户友好错误统一脱敏并返回 `trace_id`，真实异常只进后端日志。
- 切勿提交任何密钥、`backend/.env`、`backend/logs/`、数据库文件。

## 十一、版本与变更管理

- 显著变化先写入 [CHANGELOG.md](CHANGELOG.md) 顶部的 `[Unreleased]`，按新增、变更、弃用、移除、修复和安全分类；没有内容的分类不保留。
- 提交信息采用 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：`feat` 对应 MINOR，`fix` 对应 PATCH，`!` 或 `BREAKING CHANGE` 对应 MAJOR。
- 正式版本采用 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)；只有负责人明确确认发版后，才归档 `[Unreleased]`、创建 `vX.Y.Z` 标签和 Release。
- `scripts/release.sh` 用于执行前端类型检查/构建、后端测试和安全推送，不等同于创建正式版本。

## 目录结构总览

```text
DataChatV1/
├── backend/                 ← FastAPI 问数后端（:8001）
│   ├── app/main.py          ← 入口（路由 + 鉴权 + 托管前端）
│   ├── app/core/            ← orchestrator / nl2sql / retrieval / guard / exec / cache / llm
│   ├── app/expert_team/     ← 专家、技能、知识与协同编排
│   ├── app/integrations/    ← Quick BI 智能小Q等外部集成
│   ├── app/exports/         ← 异步 Excel 导出
│   ├── config/semantic.yaml ← 业务语义层
│   ├── config/env/          ← local.env / production.env 环境专属默认
│   ├── web/                 ← 前端构建产物
│   └── tests/               ← unit + api + e2e
├── frontend/                ← React + Vite SPA（构建进 backend/web/）
├── deploy/                  ← systemd / Nginx / Redis / k6 / 生产重部署
├── scripts/                 ← 启停、诊断、测试、发布与安全推送
├── logs/                    ← 运行期日志与本地 SQLite
├── start.sh / start_local.sh / stop.sh
├── CHANGELOG.md
└── README.md
```
