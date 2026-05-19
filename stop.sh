#!/usr/bin/env bash
# DataChat 一键停止
#   ./stop.sh           只停后端 + 释放端口
#   ./stop.sh --redis   连同 Redis 一起停（默认保留 Redis）
#   ./stop.sh --all     同 --redis
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT/.pids"
PORT=8001

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
red()  { printf "\033[31m%s\033[0m\n" "$*"; }
green(){ printf "\033[32m%s\033[0m\n" "$*"; }

include_redis="0"
case "${1:-}" in
  --redis|--all) include_redis="1" ;;
esac

cyan "═══ DataChat 一键停止 ═══"

# 1. 停 uvicorn
PID_FILE="$PID_DIR/uvicorn.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  pid=$(cat "$PID_FILE")
  kill "$pid" 2>/dev/null || true
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  green "  ✓ 已停止 uvicorn pid=$pid"
  rm -f "$PID_FILE"
else
  green "  · uvicorn 未在运行（无 pid 文件）"
fi

# 2. 释放端口（残留进程）
remaining=$(lsof -ti tcp:$PORT 2>/dev/null || true)
if [ -n "$remaining" ]; then
  echo "$remaining" | xargs kill -9 2>/dev/null || true
  green "  ✓ 已清理 $PORT 端口残留"
fi

# 3. 可选：停 Redis
if [ "$include_redis" = "1" ]; then
  if command -v redis-cli >/dev/null 2>&1 && redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    redis-cli -h 127.0.0.1 -p 6379 shutdown nosave >/dev/null 2>&1 || true
    sleep 1
    green "  ✓ 已停止 Redis"
  else
    green "  · Redis 未在运行"
  fi
else
  green "  · 保留 Redis 运行（如需停止请加 --redis）"
fi

cyan "═══ 全部停止完成 ═══"
