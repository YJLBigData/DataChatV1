# DataChatV1 生产部署 / 容量升级 Runbook

> 目标：30 人 × 200 次/天（≈6000 次/天）问数，服务器 4C/8GB。功能已固定，本次只做
> **容量、稳定性、部署、压测、监控、兜底**。架构决策：**单 Uvicorn worker + 显式线程池 +
> 全局并发闸 + DB 池扩容 + Redis 封顶 + 生产网关 + systemd/nginx 兜底**。当前规格不上多 worker。

本次升级对应技能文档 `SKILL_问数服务架构优化修复.md` 的 P0 全量项。

---

## 0. 上线阻塞项（必须先办，非代码）

| 项 | 说明 |
|---|---|
| **申请生产 LLM 网关** | 现配置指向 `adp-test.feihe.com`（测试，不保 SLA）。拿到生产地址后写入 `/opt/datachatv1/.env` 的 `FEIHE_AGENT_API_URL`。|
| **申请业务库只读账号/读副本** | 应用侧最大连接 35（`DB_POOL_SIZE+DB_MAX_OVERFLOW`），向 DBA 申请配额 ≥60，优先只读副本，避免与其它业务抢连接。|
| **LLM 网关配额** | 建议申请 **并发 ≥50、QPS ≥30/s**（实际峰值约 25 并发，2~3 倍安全垫）。|

---

## 1. 本次代码改动（已在仓库）

| 改动 | 文件 | 作用 |
|---|---|---|
| 线程池放大到 32 + anyio 限额 32 | `backend/app/main.py` `_lifespan` | SSE 问数不再被默认 ~8 线程卡死（P0-1/P0-2）|
| 全局在途并发闸 + 自定义指标 | `backend/app/core/concurrency.py`（新增）+ 两个 chat 端点 | 过载 429 泄洪；暴露 `chat_inflight`/`chat_rejected_total`/`llm_timeout_total`/`db_pool_timeout_total`（P0-3）|
| DB 池 env 化（20+15，超时 10s） | `backend/app/core/config.py` + `core/exec/mysql_exec.py` | 匹配 30 并发（P0-4）|
| LLM 读超时收紧到 90s（env 化） | `backend/app/core/config.py`（→ router & feihe_gateway）| 慢调用不长时间钉线程（P0-5）|
| 容量参数集中声明 | `backend/config/env/production.env` | 运维可见、一处可调 |

> 所有参数都有代码默认值，`production.env` 只是显式列出 + 可调。`APP_ENV=production` 时
> `config.py` 会自动加载 `backend/config/env/production.env`；真实密钥仍只在 `/opt/datachatv1/.env`。

### 关键环境变量

| 变量 | 默认 | 含义 |
|---|---:|---|
| `CHAT_THREAD_POOL_SIZE` | 32 | SSE 问数线程池 |
| `ANYIO_THREAD_LIMIT` | 32 | 同步端点线程限额 |
| `CHAT_MAX_INFLIGHT` | 30 | 全局在途上限（超过 429）|
| `CHAT_SEMAPHORE_ACQUIRE_TIMEOUT` | 0.2 | 抢名额最长等待（秒）|
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | 20 / 15 / 10 | 上限 35 连接 |
| `DB_POOL_RECYCLE` | 1800 | 连接回收（秒）|
| `LLM_READ_TIMEOUT` / `LLM_CONNECT_TIMEOUT` / `LLM_MAX_RETRIES` | 90 / 10 / 1 | LLM 超时与重试 |

---

## 2. 运维文件（`deploy/`）

| 文件 | 安装位置 / 用法 |
|---|---|
| `systemd/datachatv1.service` | `cp → /etc/systemd/system/`，`systemctl enable --now datachatv1`（P0-7，崩溃自愈+开机自启）|
| `nginx/datachatv1.conf` | `cp → /etc/nginx/conf.d/`，`nginx -t && systemctl reload nginx`（P0-8，SSE 不缓冲、`/metrics` 内网）|
| `redis/redis-tuning.sh` | `bash deploy/redis/redis-tuning.sh`（P0-6，maxmemory 768mb + allkeys-lru）|
| `scripts/cleanup_artifacts.sh` | 加 crontab `0 3 * * *`（P0-9，导出/报告/图表 7 天清理 + 磁盘告警）|
| `loadtest/k6_chat_smoke.js` | `k6 run …`（P0-8 / 第 8 节，容量压测）|

> ⚠️ 路径核对：service 里的 `WorkingDirectory=/opt/datachatv1/backend`、venv
> `/opt/datachatv1/backend/.venv`、端口 8001、CentOS7 用 `MemoryLimit`——按实际部署核对后再启用。

---

## 3. 部署步骤

```bash
# 0) 阻塞项已办：生产网关地址 + DB 只读账号已写入 /opt/datachatv1/.env
cd /opt/datachatv1 && cp -a .env .env.bak.$(date +%F_%H%M%S)   # 备份现网 env

# 1) 拉代码 + 装依赖
git pull
source backend/.venv/bin/activate
pip install -r backend/requirements.txt

# 2) Redis 调优（一次性，重启后由 redis.conf 保持）
bash deploy/redis/redis-tuning.sh

# 3) systemd（首次）
sudo cp deploy/systemd/datachatv1.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now datachatv1
sudo systemctl status datachatv1 --no-pager

# 4) nginx（首次）
sudo cp deploy/nginx/datachatv1.conf /etc/nginx/conf.d/datachatv1.conf
sudo nginx -t && sudo systemctl reload nginx

# 5) 产物清理 cron（首次）
( crontab -l 2>/dev/null; echo "0 3 * * * /opt/datachatv1/deploy/scripts/cleanup_artifacts.sh >> /var/log/datachatv1_cleanup.log 2>&1" ) | crontab -

# 6) 健康检查
curl -i http://127.0.0.1:8001/api/health
curl -s http://127.0.0.1:8001/metrics | grep -E 'chat_inflight|chat_rejected_total' | head
```

非首次发版只需：`git pull && pip install -r backend/requirements.txt && sudo systemctl restart datachatv1`。

---

## 4. 冒烟验证

1. 登录页可打开、可登录。
2. 普通问数返回正常；SSE 流式输出正常（逐块出，不卡到结束）。
3. 一条含 SQL 查询的问题能返回结果表。
4. 同一问题二次提问明显变快（缓存命中）。
5. 导出 Excel 能生成、能下载。
6. `curl /metrics` 能看到 `chat_inflight` 等自定义指标。
7. `journalctl -u datachatv1 -f` 启动无错误。

---

## 5. 压测与验收

```bash
# 单账号快速冒烟（注意：会先撞 30/min 限流，仅验证链路通）
k6 run deploy/loadtest/k6_chat_smoke.js -e BASE_URL=http://127.0.0.1:8001 \
  -e DATACHAT_USER=admin -e DATACHAT_PASS='***'

# 真实 30 人容量：先 seed ≥16 个测试账号，写 users.json
cp deploy/loadtest/users.json.example deploy/loadtest/users.json   # 填真实账号
k6 run deploy/loadtest/k6_chat_smoke.js -e BASE_URL=http://127.0.0.1:8001 \
  -e USERS_FILE=deploy/loadtest/users.json
```

**验收标准**：5xx 错误率 < 1%；p95 < 25s（LLM 主导）；`db_pool_timeout_total` == 0；
`llm_timeout_total` 占比 < 1%；`chat_inflight` 不长期顶满 30；429 仅突发期短时出现；
Redis `used_memory` 不逼近 768MB。

> 限流提示：`/api/chat` 默认 30/min/用户。单账号压测的 429 多来自**限流**而非容量闸；要
> 看真实容量，用多账号 `USERS_FILE`，或压测窗口临时调高该端点限流。

---

## 6. 监控（Prometheus `/metrics`）

| 指标 | 告警建议 |
|---|---|
| `http_request_duration_seconds`（p95） | > 30s 持续 5 分钟 |
| `http_requests_total{status=~"5.."}` | 错误率 > 1% |
| `chat_inflight` | 长期接近 30 |
| `chat_rejected_total` | 持续增长 → 需扩容 |
| `llm_timeout_total` | 持续增长 → 网关慢/配额不足 |
| `db_pool_timeout_total` | > 0 即排查 |
| `process_resident_memory_bytes` | > 5G 危险（接近 MemoryLimit）|

---

## 7. 回滚

```bash
# 代码回滚
cd /opt/datachatv1 && git log --oneline -5
git reset --hard <上一个稳定 commit>
sudo systemctl restart datachatv1 && curl -i http://127.0.0.1:8001/api/health

# 配置回滚
cp /opt/datachatv1/.env.bak.<时间戳> /opt/datachatv1/.env
sudo systemctl restart datachatv1

# Redis（仅纯缓存时可清空，会临时降低命中率，不丢业务数据）
redis-cli -n 2 FLUSHDB
```

参数级回滚（无需回代码）：在 `/opt/datachatv1/.env` 覆盖单个变量再 `systemctl restart`，
例如 LLM 超时切回 180s：`LLM_READ_TIMEOUT=180`。

---

## 8. 何时扩容 / 进入 P1·P2

出现任一情况即评估扩容：429 持续（非短时突发）、LLM p95 长期 >60s、`db_pool_timeout_total`>0、
进程内存长期 >5G、CPU load 长期 >4、要扩到 50 人以上、要不停机发布、要多节点容灾。

**扩容优先级**：先升内存到 16GB → 再把 jobs 事件总线迁到 Redis（解除单 worker 约束）→
再开 2 worker → 最后多机器。多 worker 前必须：jobs 事件/取消跨进程可见 +
写热 SQLite（会话/导出/任务）迁 MySQL/PG + nginx 负载均衡 + 滚动发布。
