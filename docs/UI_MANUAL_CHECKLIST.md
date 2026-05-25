# DataChatV1 UI 人工测试清单（每次发布前过一遍）

> 自动化覆盖：`frontend/playwright/smoke.spec.ts`（登录 / 首屏 / 提问 / 切换 / 健康 / 限流 / metrics）
> 本清单覆盖**自动化未触达的可点击点**——按页面分组，逐项打勾即可。每项目标都是 **不白屏 / 无 console error / 行为符合预期**。

---

## 0. 启动前置
- [ ] `/api/health` 返回 200，`db.ok=true`、`cache.ok=true`
- [ ] 公网 `https://datachatv1.feihe.com/web/` 200，无证书警告
- [ ] 浏览器 DevTools Console 干净（无红色 error）
- [ ] DevTools Network 没有 4xx/5xx 飘红

---

## 1. 登录页
- [ ] 默认聚焦在用户名输入框
- [ ] 用户名/密码留空点登录 → 友好错误（非 500）
- [ ] 密码错 → 「用户名或密码错误」（不泄漏哪个错）
- [ ] 强密码策略：试 `123456` 改密 → 被拒（如果你是新建用户被强制改密）
- [ ] 登录成功 → 跳到聊天页，URL hash 应该为 `#/chat`

---

## 2. 聊天页 / 主问数
- [ ] 输入框正常输入、回车提交（Shift+Enter 换行不提交）
- [ ] 发送一个标准问题（如「2025年1月各大区销售额」）→ SSE 流式有 stage pill 滚动
- [ ] 结果出现：narrative、表格、图表切换器、推荐继续问 chip
- [ ] **图表切换**：列表 / KPI / 柱 / 折 / 饼 / 雷达 / 散点 / 漏斗 / 堆叠 …… 每个都点一次，**不白屏**
- [ ] 「复制 SQL」→ 剪贴板里就是 SQL
- [ ] 「下载报告」→ 弹模板选择 → 选一个 → 下载 .docx，能打开
- [ ] 「推送飞书」→ 友好回执（成功"已推送"或失败"飞书未配置/网络…"）
- [ ] 「展开口径与 SQL」→ 看到指标定义/来源表/分组/规划理由/SQL，无 raw stacktrace
- [ ] 追问"按大区拆开看" → 沿用上下文，**不切表/不切时间**（这是回归点 #7 的核心）
- [ ] 推荐继续问 chip → 点击直接发起新问

---

## 3. 会话侧栏
- [ ] 列表显示，最近会话在顶
- [ ] 切换不同会话 → 主区切换内容，**无 hero 闪一下首页**
- [ ] 「新会话」按钮 → 切空 hero
- [ ] 重命名会话 → 中文弹窗（不应再有英文 "Rename"）
- [ ] 删除会话 → 中文确认 → 成功删除，主区切空
- [ ] 收藏到文件夹（如有）→ 文件夹列表更新
- [ ] 流式中切到别的会话 → 老会话 unread 红点出来，不阻塞新操作

---

## 4. 顶部用户菜单
- [ ] 用户头像点击 → 显示用户名/角色
- [ ] 「修改密码」→ 弹密码框，旧密码错 → 提示「原密码不正确」
- [ ] 修改成功后 → 自动登出或要求重登
- [ ] 「退出」→ 回登录页
- [ ] 顶栏 LLM provider 下拉（如显示）→ 切换 bailian/feihe → 下一句问数走新 provider（看 stage pill 的 model 标签）

---

## 5. 管理员页（仅 admin）
### 5.1 用户管理 `/web/#/admin/users`
- [ ] 用户列表显示，含 username / display_name / role / is_active / 飞书邮箱
- [ ] 「新建用户」→ 表单提交 → 列表多一行 + 一次性密码可复制
- [ ] 「重置密码」→ 弹新随机密码，强度 ≥10 位
- [ ] 「禁用/启用」→ 状态切换
- [ ] 删除用户 → 确认后消失（除内置 admin）

### 5.2 语义层 `/web/#/admin/semantic`
- [ ] 表/维度/指标 三个 tab 切换不白屏
- [ ] 点开一个 metric → 看到 label / table / expression / typical_questions
- [ ] 编辑保存 → 友好结果；故意填错（缺 table）→ 后端拒绝，前端友好提示
- [ ] 「LLM 分析新表」→ 输入物理表名 → 等候完成，给出建议 metric/dim

### 5.3 日志 `/web/#/admin/logs`
- [ ] 列表分页正常，total 数字正确
- [ ] 状态筛选（ok/clarify/error/legacy）切换
- [ ] 关键字搜索按 question / sql / metric 命中
- [ ] 点开行详情看到 plan / sql / rows
- [ ] 旧 legacy 行不应错标成 ok（阶段 2.4 修复点）

### 5.4 权限 `/web/#/admin/permissions`
- [ ] 用户列表 + 点开看 row_rules / allowed_tables / allowed_columns / deny_by_default
- [ ] 编辑保存后立即生效（被授权的普通用户能查；未授权的拒绝）

### 5.5 报告模板 `/web/#/admin/report-templates`
- [ ] system 模板列表 + 我的模板列表
- [ ] 「新建模板」→ 普通用户只能建 user 模板；admin 勾 `system` 能建系统模板（阶段 P2 修复点）
- [ ] 编辑保存 / 删除（非系统模板）

---

## 6. 安全 / 限流 / 错误处理
- [ ] 拿到一个 user 的 token，对 `/api/chat` 用 `for i in {1..40}; do curl ... ; done`，第 31 次起出现 429（阶段 1.4 限流）
- [ ] 故意拼一个 `{... "webhook": "http://127.0.0.1:9/x" ...}` 打飞书 → 返回友好错误，不暴露 `[Errno 61]`（阶段 P1 修复点）
- [ ] 普通用户访问 `/api/admin/users` → 403
- [ ] 未改密的用户访问 `/api/chat` → 403 + 提示先改密
- [ ] 整个会话期间 DevTools Network 里没看到 password / token 完整字符串明文打印到 console / 响应体

---

## 7. 多表 JOIN 能力开关（阶段 3.1，默认关闭）
- [ ] 默认情况下，问"销售实绩 vs 销售目标"等需要多表的问题 → 不会试图生成跨表 SQL（规划器走单表口径）
- [ ] 在服务器 `.env` 加 `DATACHAT_ALLOW_MULTI_TABLE=1`，redeploy 后再问同样问题 → 可以生成 JOIN（受 semantic.yaml joins 图约束）
- [ ] 关闭后回归：跨表问题被 guard 拒绝，给清楚的错（"未启用多表 JOIN…"）

---

## 8. EXPLAIN 闸门（阶段 3.3，默认关闭）
- [ ] 默认查询不变
- [ ] 临时 `.env` 加 `DATACHAT_EXPLAIN_GATE=1 DATACHAT_EXPLAIN_MAX_ROWS=1000`，问一个超大查询 → 友好拒绝"EXPLAIN 成本闸门拦截…"
- [ ] 关闭后回归正常

---

## 9. 跨浏览器小屏（人工 5 分钟）
- [ ] Chrome / Edge / Safari 各开一次：登录 + 问一次 + 看图表
- [ ] iPhone Safari 真机 / Chrome DevTools 模拟 iPhone 12：侧栏可折叠、主区不挤压、不出现横向滚动条

---

## 10. 部署后冒烟（每次 redeploy 之后）
- [ ] `systemctl status datachatv1` active
- [ ] `systemctl status datachatv1-worker` active（如启用 Celery）
- [ ] `ps -ef | grep uvicorn | grep -v grep | wc -l` ≥ 4（master + 3 worker）
- [ ] `sh datachatv1_healthcheck.sh` 全 PASS（除公网 WARN 视 Nginx 是否就绪）
- [ ] `curl -s /metrics | grep http_requests_total | head` 有输出
- [ ] `tail -n 20 /opt/datachatv1/logs/uvicorn.log` 是 JSON 行而非纯文本（阶段 2.4 生效）

---

发现问题时：截图 + 控制台错误粘上面，记到 issue，按问题严重等级排修复优先级。
