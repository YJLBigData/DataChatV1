#!/usr/bin/env bash
# DataChatV1 Redis 调优 —— 容量方案 P0-6：8GB 机器必须给缓存封顶，杜绝 OOM。
# Redis 在本项目仅作**纯缓存**（三层问数缓存，可随时淘汰/丢失），因此用 allkeys-lru +
# 关闭持久化（不写 AOF/RDB，减少磁盘 IO 与内存峰值）。
#
# 用法：bash deploy/redis/redis-tuning.sh
# 如 redis 需要密码：REDISCLI_AUTH=xxx bash deploy/redis/redis-tuning.sh
set -euo pipefail

REDIS_CLI="${REDIS_CLI:-redis-cli}"
HOST="${REDIS_HOST:-127.0.0.1}"
PORT="${REDIS_PORT:-6379}"
MAXMEM="${REDIS_MAXMEMORY:-768mb}"

cli() { "$REDIS_CLI" -h "$HOST" -p "$PORT" "$@"; }

echo "== 应用 Redis 缓存调优 ($HOST:$PORT, maxmemory=$MAXMEM) =="
cli CONFIG SET maxmemory "$MAXMEM"
cli CONFIG SET maxmemory-policy allkeys-lru
cli CONFIG SET appendonly no
cli CONFIG SET save ""
# 持久化到 redis.conf，重启后不丢配置（部分发行版禁用 CONFIG REWRITE 时忽略报错）。
cli CONFIG REWRITE || echo "  (CONFIG REWRITE 跳过：请手动把上述项写入 redis.conf)"

echo "== 校验 =="
cli INFO memory | grep -E 'used_memory_human|maxmemory_human|maxmemory_policy' || true
echo "验收要求：maxmemory_human≈768M，maxmemory_policy=allkeys-lru，used_memory 不持续逼近系统总内存。"
