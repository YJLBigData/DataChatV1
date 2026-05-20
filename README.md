# DataChat · 飞鹤小Q 智能问数

面向飞鹤高管的智能问数（NL2SQL）产品。**准确优先 / 稳定优先 / 可解释优先 / 宁可澄清不答错**。

## 一键启动

```bash
./start_local.sh  # 本地启动：Redis + MySQL(chatbi/users) + 后端 + 编译前端，访问 http://127.0.0.1:8001/web/
./start.sh        # 同 start_local.sh 的底层启动脚本
./stop.sh         # 停止后端（保留 Redis）
./stop.sh --redis # 停止后端 + Redis
```

首次启动如果 `backend/.env` 不存在，脚本会从 `backend/.env.example` 复制一份并退出，你按提示填好 `DASHSCOPE_API_KEY` 后重跑即可。
本机没有 MySQL 时，`./start.sh` 会自动用 Docker 启动 `datachat-mysql`，创建 `chatbi` 库和 5 张样例业务表，避免本地测试出现 `Can't connect to MySQL server on '127.0.0.1'`。
本地用户信息也存放在同一个本地 MySQL 的 `users` 表，服务器部署则由 `start_server.sh`/`deploy/deploy_datachatv1.sh` 写入生产 `.env`，改用服务器 MySQL。

## 默认账号

- 用户名：`admin`
- 密码：在 `backend/.env` 中通过 `DATACHAT_ADMIN_PASSWORD` 自行设置；脚本不再内置任何默认明文密码。
  首次启动若未设置该变量，请使用 `./scripts/reset_admin.sh` 生成强密码后再登录。

## 重置管理员密码

```bash
./scripts/reset_admin.sh                # 交互式
./scripts/reset_admin.sh 新的强密码      # 直接传参
```

## 目录结构

```
backend/
├── app/
│   ├── main.py             ← FastAPI 入口（路由 + 鉴权）
│   └── core/
│       ├── auth.py         ← bcrypt + JWT 登录
│       ├── config.py       ← 集中配置
│       ├── conversation.py ← SQLite 多轮存储
│       ├── orchestrator.py ← 7 阶段问数 DAG + SSE
│       ├── answerer.py     ← 高管级 narrative + 图表 + 解释
│       ├── feishu.py       ← 飞书富文本卡片推送
│       ├── report.py       ← DOCX 报告
│       ├── semantic/       ← 业务语义层（指标/维度/表/计算）
│       ├── retrieval/      ← 千问 embedding + BM25 + alias 加权
│       ├── nl2sql/         ← Plan-First IR + 确定性 SQL 编译器
│       ├── guard/          ← sqlglot AST + 危险词 + LIMIT 注入
│       ├── exec/           ← MySQL 执行 + 超时
│       ├── cache/          ← Redis L1 问题/L2 plan/L3 SQL 缓存
│       └── llm/            ← 阿里百炼客户端
├── config/semantic.yaml    ← 业务语义层（可手工编辑）
├── scripts/reset_admin.py
├── tests/                  ← unit + api + e2e（数量以 pytest 实际输出为准）
├── .env / .env.example
└── requirements.txt

frontend/
├── src/
│   ├── App.tsx
│   ├── api.ts              ← 含 JWT/SSE 客户端
│   ├── components/
│   │   ├── LoginScreen, Hero, Composer, AnswerCard
│   │   ├── ChartView, TableView, StagePill, ConversationList
│   ├── styles.css          ← Tailwind + qq-* 设计令牌
│   └── main.tsx
└── vite.config.ts          ← 输出到 backend/web/

start.sh / stop.sh / scripts/reset_admin.sh
```

## 端到端能力

- 自然语言问数（中文）→ 受控 QueryPlan → 确定性 SQL → MySQL 查询
- 同比 / 环比 / 占比 / Top N / 趋势 / 累计 / 差值 等口径开箱即用
- 多轮上下文：自动继承上一轮的指标/维度/筛选
- 模糊问题自动澄清（needs_clarify + clarify_options）
- 端到端 SSE 流式：`session → cache → retrieval → plan → compile → guard → execute → answer`
- 三层缓存：L1 问题、L2 plan、L3 SQL 结果
- DOCX 报告生成（含结论 / 高亮 / 风险 / 明细 / 口径 / SQL）
- 飞书富文本卡片推送
- Bcrypt + JWT 登录，按 user_id 隔离会话

## 数据范围

业务库 `chatbi` 的 5 张表（`ads_bi_hs_sale_info_df` 等），数据 2025-01 ~ 2026-04。

新增表/指标：编辑 `backend/config/semantic.yaml` → 重启后端即可生效，无需写代码。

## 测试

```bash
# 单元 + API（不需 LLM/DB）——当前 68 passed（数量以输出为准，请勿写死）
backend/.venv/bin/python -m pytest backend/tests/ -m "not e2e" -v

# 全量（含 LLM/DB Golden Case，需要 .env 已配好）
backend/.venv/bin/python -m pytest backend/tests/ -v
```

## 安全

- 所有 SQL 走 `sqlglot AST guard` + 危险词 + 表白名单 + 自动 LIMIT
- 仅允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP/TRUNCATE/多语句/SELECT *
- 鉴权：所有 `/api/*`（除 `/api/login` `/api/health` `/api/bootstrap` `/api/suggestions`）需 Bearer JWT
- 不要提交 `backend/.env`、`backend/logs/`、`DASHSCOPE_API_KEY`、`JWT_SECRET`
