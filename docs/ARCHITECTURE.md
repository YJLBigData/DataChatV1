# DataChat · 飞鹤小Q 技术架构图

> 本文用 [Mermaid](https://mermaid.js.org/) 绘制，GitHub / VS Code / IntelliJ 的 Markdown 预览可直接渲染成图。
> 涵盖 6 张视图：①系统总览 ②七阶段问数管线 ③问数请求时序 ④SSO 鉴权流程 ⑤配置分层与部署边界 ⑥飞书推送路由。

---

## ① 系统总览（容器视图）

三个子系统（DataChat 后端 / 前端 SPA / DataCode 平台）+ 基础设施 + 外部服务，靠**统一登录（SSO）**串联。

```mermaid
flowchart TB
    subgraph client["👤 客户端"]
        B["浏览器 SPA<br/>React 18 + ECharts 6"]
    end

    subgraph edge["接入层"]
        LB["Nginx / 负载均衡<br/>proxy-headers 透传真实 IP/协议"]
    end

    subgraph datachat["DataChat 后端 :8001 — FastAPI/uvicorn ×3 (systemd)"]
        WEB["/web/ 静态托管<br/>backend/web 构建产物"]
        API["/api/* — REST + SSE"]
        AUTH["鉴权 bcrypt + JWT"]
        ORCH["问数管线 Orchestrator<br/>七阶段 DAG"]
    end

    subgraph datacode["DataCode 平台 :18082 — Spring Boot"]
        SSOF["DataChatSsoFilter<br/>回源校验 Bearer"]
        CODE["写代码 / SQL 校验 / 血缘 / CDP 上报"]
    end

    subgraph infra["🗄️ 基础设施（服务器本地）"]
        MYSQL[("MySQL<br/>chatbi(本地) / hs_poc(生产)")]
        REDIS[("Redis db=2<br/>L1/L2/L3 三层缓存")]
        SQLITE[("SQLite<br/>用户/权限/会话/日志/模板/预设")]
    end

    subgraph ext["☁️ 外部服务"]
        DS["DashScope<br/>qwen3.6-max + embedding"]
        FEIHE["飞鹤统一模型网关<br/>AES 签名"]
        FS["飞书 Open API"]
        ODPS["MaxCompute / Dataphin"]
    end

    B --> LB --> WEB & API
    B -. "Bearer JWT（同一套登录态）" .-> SSOF
    API --> AUTH --> ORCH
    SSOF -- "GET /api/me 校验" --> API
    ORCH --> REDIS & MYSQL & DS & FEIHE
    API --> SQLITE
    API -- "富文本卡片" --> FS
    CODE --> ODPS & DS

    style datachat fill:#eef5ff,stroke:#3b82f6
    style datacode fill:#f3eeff,stroke:#8b5cf6
    style ext fill:#fff7e6,stroke:#f59e0b
```

| 子系统 | 技术栈 | 端口 | 进程/部署 |
|--------|--------|------|-----------|
| DataChat 后端 | Python 3.11 · FastAPI · uvicorn | 8001 | systemd `datachatv1.service`，3 workers |
| DataChat 前端 | React 18 · Vite · TS · Tailwind | — | 构建进 `backend/web/`，由后端托管 |
| DataCode 平台 | Java 8 · Spring Boot · OkHttp | 18082 | 独立服务，复用 DataChat JWT |

---

## ② 七阶段问数管线（核心流程）

一次问数是一条 DAG，逐阶段通过 SSE 实时回推前端。三层缓存任一命中即短路返回。

```mermaid
flowchart TB
    Q["用户提问（自然语言·中文）"] --> SESSION

    subgraph pipe["orchestrator.py — 每阶段 SSE 回推状态"]
        SESSION["① session<br/>载入多轮上下文<br/>继承上轮指标/维度/筛选"]
        C1{"② cache · L1<br/>问题级命中?"}
        RET["③ retrieval<br/>embedding + BM25 + alias 加权"]
        PLAN["④ plan<br/>LLM → 受控 QueryPlan IR"]
        CLARIFY{"问题模糊?<br/>needs_clarify"}
        C2{"cache · L2<br/>plan 命中?"}
        COMPILE["⑤ compile<br/>确定性 SQL 编译器<br/>同比/环比/占比/TopN/趋势"]
        GUARD{"⑥ guard<br/>sqlglot AST + 表白名单<br/>+ 危险词 + 自动 LIMIT"}
        PERM{"数据权限<br/>按 user_id"}
        C3{"cache · L3<br/>SQL 结果命中?"}
        EXEC["⑦ execute<br/>MySQL 连接池 + 15s 超时"]
        ANS["⑧ answer<br/>高管文案 + 图表 + 口径解释"]
    end

    SESSION --> C1
    C1 -- "hit" --> ANS
    C1 -- "miss" --> RET --> PLAN --> CLARIFY
    CLARIFY -- "是" --> ASK["返回 clarify_options<br/>请用户澄清（不硬答）"]
    CLARIFY -- "否" --> C2
    C2 -- "hit" --> ANS
    C2 -- "miss" --> COMPILE --> GUARD
    GUARD -- "拒绝" --> REJECT["拦截 + trace_id"]
    GUARD -- "通过" --> PERM
    PERM -- "无权" --> DENY["权限不足，提示联系管理员"]
    PERM -- "有权" --> C3
    C3 -- "hit" --> ANS
    C3 -- "miss" --> EXEC --> ANS
    ANS --> OUT["SSE done → 前端渲染卡片/图表"]

    style GUARD fill:#fff0f0,stroke:#cc0000
    style ANS fill:#eefbef,stroke:#16a34a
    style REJECT fill:#ffe6e6
    style DENY fill:#ffe6e6
```

---

## ③ 问数请求时序（SSE 流式）

```mermaid
sequenceDiagram
    autonumber
    participant U as 浏览器
    participant API as 后端 /api/chat/stream
    participant CV as 会话存储 SQLite
    participant R as Redis 缓存
    participant RT as 检索 Retrieval
    participant LLM as DashScope / 飞鹤网关
    participant CP as SQL 编译器
    participant G as SQL Guard
    participant DB as MySQL
    participant AN as Answerer

    U->>API: POST 提问 + Bearer JWT
    API->>API: 校验 JWT → user
    API->>CV: 载入会话上下文
    API->>R: L1 问题缓存?
    alt L1 命中
        R-->>API: 缓存答案
        API-->>U: SSE cache hit → done
    else 未命中
        API->>RT: 召回相关指标/维度
        API-->>U: SSE retrieval ok
        API->>LLM: 生成 QueryPlan（温度 0）
        API-->>U: SSE plan ok（或 needs_clarify）
        API->>CP: QueryPlan → SQL
        API-->>U: SSE compile ok
        API->>G: AST 校验 + 强制 LIMIT
        API-->>U: SSE guard ok
        API->>DB: 执行 SQL（≤15s）
        API-->>U: SSE execute ok（rows）
        API->>AN: 生成文案 + 图表 + 口径
        API->>R: 回写 L1/L2/L3 缓存
        API-->>U: SSE answer → done
    end
```

---

## ④ SSO 鉴权流程（DataChat ↔ DataCode）

DataChat 是唯一鉴权中心；DataCode 不维护独立账号，凭 DataChat 的 JWT 单点登录。

```mermaid
sequenceDiagram
    autonumber
    participant U as 浏览器
    participant DC as DataCode 18082
    participant F as DataChatSsoFilter
    participant ME as DataChat /api/me
    participant A as 影子用户 AuthService

    U->>DC: GET /api/* + Bearer DataChat-JWT
    DC->>F: 请求被拦截
    F->>F: 取 Bearer（无则放行 → 控制器自行 401）
    F->>ME: GET /api/me（Authorization: Bearer）
    alt token 有效
        ME-->>F: id / username / role / email
        F->>A: attachSsoSession 建/更新本地影子用户
        F->>DC: 透传 token 为 Cookie → 既有控制器零改动
        DC-->>U: 200 业务数据
    else 401 / 403
        ME-->>F: 鉴权失败
        F-->>U: 401（清缓存，要求重新登录）
    end
    Note over F,ME: 校验结果按 token 内存缓存 60s，命中 401 立即失效
```

---

## ⑤ 配置分层与部署边界（密钥永不入库 / 不被覆盖）

Git 只承载前两层（代码 + 零密钥默认值）；所有密钥只在服务器本地第三层，`git pull` 部署不覆盖、优先级最高。

```mermaid
flowchart LR
    subgraph git["📦 Git 仓库（可入库）"]
        direction TB
        CODE2["① 代码<br/>backend / frontend / datacode"]
        DEF["② 非敏感默认<br/>config/env/production.env<br/>APP_ENV · DB_NAME · 网关URL（零密钥）"]
    end

    subgraph server["🖥️ 服务器 /opt/datachatv1（本地·不入库）"]
        direction TB
        SEC["③ 密钥文件 /opt/datachatv1/.env（chmod 600）<br/>DB_PASSWORD · JWT_SECRET · AES_KEY<br/>DASHSCOPE_API_KEY · FEISHU_APP_ID/SECRET"]
        SVC["systemd datachatv1.service<br/>EnvironmentFile = ③<br/>WorkingDirectory = app/backend"]
        RUN["uvicorn ×3 @ :8001"]
    end

    CODE2 -- "git pull（覆盖代码）" --> server
    DEF -- "git pull（覆盖默认值）" --> server
    SEC -- "EnvironmentFile 注入<br/>真实环境变量优先级最高" --> SVC --> RUN

    NOTE["加载优先级（高→低）：<br/>真实 env ③ ＞ backend/.env ＞ production.env ②"]
    NOTE -.-> RUN

    style git fill:#e6f0ff,stroke:#3b82f6
    style SEC fill:#ffe6e6,stroke:#cc0000
    style server fill:#f6fff6,stroke:#16a34a
```

> 🔴 红线：密钥只能进第③层；第②层 `production.env` 会被部署覆盖且入库，**严禁写任何密钥**。

---

## ⑥ 飞书推送路由（按角色 + 配置自动选通道）

解释「飞书推送失败」最常见的两种落点：**未配置** 与 **admin 未选收件人**。

```mermaid
flowchart TB
    START["POST /api/feishu/push"] --> ROLE{"调用者角色?"}
    ROLE -- "管理员" --> ADM{"显式传 user_email?"}
    ADM -- "是" --> TGT["target_email = 该邮箱"]
    ADM -- "否" --> NONE["target_email = None"]
    ROLE -- "普通用户" --> USR["target_email = 本人绑定邮箱<br/>（忽略请求体，防越权）"]

    TGT --> R1
    USR --> R1
    NONE --> R1

    R1{"有 email 且<br/>配了 APP_ID + APP_SECRET?"}
    R1 -- "是" --> APP["应用模式：token → open_id<br/>→ im/v1/messages 个人推送 ✅"]
    R1 -- "否" --> R2{"配了 FEISHU_WEBHOOK?"}
    R2 -- "是" --> HOOK["群机器人 webhook ✅"]
    R2 -- "否" --> R3{"APP_ID+SECRET+DEFAULT_USER_EMAIL?"}
    R3 -- "是" --> DEF2["应用模式 → 默认收件人 ✅"]
    R3 -- "否" --> ERR["❌ 飞书未配置（FeishuError）<br/>前端：飞书推送失败，请联系管理员"]

    style ERR fill:#ffe6e6,stroke:#cc0000
    style APP fill:#e6ffe6,stroke:#16a34a
    style HOOK fill:#e6ffe6,stroke:#16a34a
    style DEF2 fill:#e6ffe6,stroke:#16a34a
```

---

## 技术栈一览

| 层 | 选型 |
|----|------|
| 前端 | React 18 · Vite 5 · TypeScript 5 · TailwindCSS 3 · ECharts 6 · framer-motion |
| 后端 | Python 3.11 · FastAPI · uvicorn(多 worker) · Pydantic · httpx · sqlglot · PyMySQL · bcrypt · PyJWT |
| 检索/NL2SQL | DashScope embedding(text-embedding-v3) · BM25 · Plan-First IR · 确定性 SQL 编译器 |
| 存储 | MySQL 8（业务库）· Redis（三层缓存）· SQLite（用户/权限/会话/日志/模板/预设） |
| LLM | 阿里百炼 DashScope（qwen3.6-max-preview）/ 飞鹤统一网关（AES 签名，二选一，可多预设热切换） |
| DataCode | Java 8 · Spring Boot · OkHttp · MaxCompute/Dataphin SDK · SQLite |
| 部署 | systemd · Nginx/LB · git pull 部署（CentOS7，前端构建产物随仓库） |
| 安全 | JWT 鉴权 · 仅 SELECT · sqlglot AST 护栏 · 表白名单 · 自动 LIMIT · 按 user_id 数据权限 · 密钥服务器本地 chmod 600 |
