#!/usr/bin/env bash
# 本地一键启动：用户库 + 业务库都走"本地 MySQL"。
# 关键：自动把本地 MySQL 拉起来（优先复用已在跑的 3306；否则用 Docker 起一个），
# 解决 "Can't connect to MySQL server on '127.0.0.1' (61 Connection refused)"。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export APP_ENV=local

cyan(){ printf "\033[36m%s\033[0m\n" "$*"; }
green(){ printf "\033[32m%s\033[0m\n" "$*"; }
red(){ printf "\033[31m%s\033[0m\n" "$*"; }
gray(){ printf "\033[90m%s\033[0m\n" "$*"; }

cyan "═══ DataChat 本地一键启动 (APP_ENV=local) ═══"

# ---- 读取本地 MySQL 连接参数（不回显密码）----
load_kv(){ # file key  -> echo value
  [ -f "$1" ] || return 0
  local v; v="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n1 | cut -d= -f2-)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}
ENVF_LOCAL="$ROOT/backend/config/env/local.env"
ENVF_DOTENV="$ROOT/backend/.env"
ENVF_RUNTIME="$ROOT/backend/config/runtime.local.env"

MYSQL_HOST="${MYSQL_HOST:-$(load_kv "$ENVF_LOCAL" MYSQL_HOST)}"; MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-$(load_kv "$ENVF_LOCAL" MYSQL_PORT)}"; MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_DATABASE="${MYSQL_DATABASE:-$(load_kv "$ENVF_LOCAL" MYSQL_DATABASE)}"; MYSQL_DATABASE="${MYSQL_DATABASE:-chatbi}"
# 密码优先级：环境变量 > backend/.env > runtime.local.env
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(load_kv "$ENVF_DOTENV" MYSQL_PASSWORD)}"
[ -z "${MYSQL_PASSWORD:-}" ] && MYSQL_PASSWORD="$(load_kv "$ENVF_RUNTIME" MYSQL_PASSWORD)"

CONTAINER="datachat-mysql-local"
VOLUME="datachat-mysql-local-data"

mysql_alive(){ (exec 3<>"/dev/tcp/127.0.0.1/${MYSQL_PORT}") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1; }

cyan "[1/3] 准备本地 MySQL (127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE})…"
if mysql_alive; then
  green "  ✓ 检测到 ${MYSQL_PORT} 已有 MySQL 在运行，直接复用"
else
  if ! command -v docker >/dev/null 2>&1; then
    red "  ✗ 3306 无 MySQL 且未安装 Docker。"
    red "    方案A：启动本机 MySQL  →  sudo /usr/local/mysql/support-files/mysql.server start"
    red "    方案B：安装 Docker Desktop 后重跑本脚本"
    exit 2
  fi
  if ! docker info >/dev/null 2>&1; then
    red "  ✗ Docker 已安装但未运行，请先启动 Docker Desktop 再重跑。"
    exit 2
  fi
  # 没有密码就生成一个仅本地用的，并写回 backend/.env，保证后续一致
  if [ -z "${MYSQL_PASSWORD:-}" ]; then
    MYSQL_PASSWORD="Local$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 12)@1"
    touch "$ENVF_DOTENV"
    grep -q '^MYSQL_PASSWORD=' "$ENVF_DOTENV" 2>/dev/null \
      || printf 'MYSQL_PASSWORD=%s\n' "$MYSQL_PASSWORD" >> "$ENVF_DOTENV"
    gray "  · 未配置本地 MySQL 密码，已生成并写入 backend/.env（不回显）"
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    gray "  · 复用已有容器 $CONTAINER"
    docker start "$CONTAINER" >/dev/null
  else
    gray "  · 首次创建 MySQL 8 容器 $CONTAINER（数据持久化在卷 $VOLUME）"
    docker volume create "$VOLUME" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" \
      -e MYSQL_ROOT_PASSWORD="$MYSQL_PASSWORD" \
      -e MYSQL_DATABASE="$MYSQL_DATABASE" \
      -p "${MYSQL_PORT}:3306" \
      -v "${VOLUME}:/var/lib/mysql" \
      --restart unless-stopped \
      mysql:8.0 --default-authentication-plugin=mysql_native_password >/dev/null
  fi
  gray "  · 等待 MySQL 就绪…"
  for i in $(seq 1 60); do
    if docker exec "$CONTAINER" mysqladmin ping -h 127.0.0.1 -uroot -p"$MYSQL_PASSWORD" --silent >/dev/null 2>&1; then
      break
    fi
    sleep 2
    [ "$i" = 60 ] && { red "  ✗ MySQL 60s 内未就绪，看 docker logs $CONTAINER"; exit 3; }
  done
  docker exec "$CONTAINER" mysql -uroot -p"$MYSQL_PASSWORD" \
    -e "CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4;" >/dev/null 2>&1 || true
  green "  ✓ 本地 MySQL 就绪（容器 $CONTAINER，库 ${MYSQL_DATABASE}）"
fi
export MYSQL_HOST MYSQL_PORT MYSQL_DATABASE MYSQL_PASSWORD

cyan "[2/3] 用户体系：本地 SQLite（用户与权限和业务库解耦）"
gray "  · 默认写入 backend/logs/user_store.db（users + user_permission_v1 同库）"

cyan "[3/3] 交给 start.sh 完成依赖/前端/后端启动…"
exec "$ROOT/start.sh" "$@"
