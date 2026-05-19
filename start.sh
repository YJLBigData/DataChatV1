#!/usr/bin/env bash
# DataChat 一键启动脚本（Mac 本地）
# 启动顺序：检查依赖 → Redis → MySQL → 后端依赖/建样例库 → 前端构建 → uvicorn → 健康检查
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
LOG_DIR="$ROOT/logs"
PID_DIR="$ROOT/.pids"
PORT=8001

mkdir -p "$LOG_DIR" "$PID_DIR"

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
red()  { printf "\033[31m%s\033[0m\n" "$*"; }
green(){ printf "\033[32m%s\033[0m\n" "$*"; }
gray() { printf "\033[90m%s\033[0m\n" "$*"; }

cyan "═══ DataChat 一键启动 ═══"

# ───────────────────────── 1. 依赖检查 ─────────────────────────
cyan "[1/7] 检查工具链…"

# 兼容 brew + 用户自定义安装（如 ~/app/node/bin）
for extra in /opt/homebrew/bin /usr/local/bin /usr/local/mysql/bin "$HOME/app/node/bin" "$HOME/.nvm/versions/node/$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | tail -n1)/bin"; do
  if [ -d "$extra" ]; then
    case ":$PATH:" in *":$extra:"*) ;; *) export PATH="$extra:$PATH" ;; esac
  fi
done

need_install=0
for cmd in python3.11 redis-server node npm; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    case "$cmd" in
      python3.11)   red "  缺少 python3.11（brew install python@3.11）";   need_install=1 ;;
      redis-server) red "  缺少 redis-server（brew install redis）";       need_install=1 ;;
      node|npm)     red "  缺少 node/npm（brew install node@20 或自行安装到 ~/app/node）"; need_install=1 ;;
    esac
  fi
done
command -v mysql >/dev/null 2>&1 || gray "  · mysql 客户端不在 PATH（仅用于体检，不影响后端运行）"
if [ "$need_install" = "1" ]; then red "请先安装上面缺失的工具，再重跑 ./start.sh"; exit 1; fi
green "  ✓ python3.11 / node / redis-server 就绪 ($(node -v 2>/dev/null))"

# ───────────────────────── 2. .env 检查 ─────────────────────────
cyan "[2/7] 检查 backend/.env…"
if [ ! -f "$BACKEND/.env" ]; then
  if [ -f "$BACKEND/.env.example" ]; then
    cp "$BACKEND/.env.example" "$BACKEND/.env"
    red "  已根据 .env.example 创建 backend/.env"
    red "  请打开 backend/.env 填写 DASHSCOPE_API_KEY / MYSQL_* 后重跑"
    exit 2
  else
    red "  缺少 backend/.env 且无 .env.example，无法自动生成"
    exit 2
  fi
fi
env_get() {
  local key="$1"
  awk -F= -v k="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == k {
      sub(/^[^=]*=/, "", $0); gsub(/^["'\'']|["'\'']$/, "", $0); print $0
    }
  ' "$BACKEND/.env" 2>/dev/null | tail -n1
}
is_placeholder() {
  case "${1:-}" in
    ""|*请填写*|*PLEASE_REPLACE*|sk-请填写*) return 0 ;;
    *) return 1 ;;
  esac
}

api_key="$(env_get DASHSCOPE_API_KEY)"
if is_placeholder "$api_key"; then
  red "  backend/.env 中 DASHSCOPE_API_KEY 为空，请填入再启动"
  exit 2
fi
green "  ✓ backend/.env 已配置 (API key 长度 ${#api_key})"

export APP_ENV="${APP_ENV:-$(env_get APP_ENV)}"; export APP_ENV="${APP_ENV:-local}"
export MYSQL_HOST="${MYSQL_HOST:-$(env_get MYSQL_HOST)}"; export MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
export MYSQL_PORT="${MYSQL_PORT:-$(env_get MYSQL_PORT)}"; export MYSQL_PORT="${MYSQL_PORT:-3306}"
export MYSQL_USER="${MYSQL_USER:-$(env_get MYSQL_USER)}"; export MYSQL_USER="${MYSQL_USER:-root}"
export MYSQL_DATABASE="${MYSQL_DATABASE:-$(env_get MYSQL_DATABASE)}"; export MYSQL_DATABASE="${MYSQL_DATABASE:-chatbi}"
mysql_pwd_from_env="${MYSQL_PASSWORD:-$(env_get MYSQL_PASSWORD)}"
if is_placeholder "$mysql_pwd_from_env"; then
  export MYSQL_PASSWORD=""
else
  export MYSQL_PASSWORD="$mysql_pwd_from_env"
fi

mysql_ping() {
  command -v mysql >/dev/null 2>&1 || return 1
  MYSQL_PWD="${MYSQL_PASSWORD:-}" mysql --protocol=TCP \
    -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" \
    --connect-timeout=3 -Nse "SELECT 1" >/dev/null 2>&1
}
private_mysql_socket_ping() {
  command -v mysql >/dev/null 2>&1 || return 1
  mysql --protocol=SOCKET --socket="$ROOT/.mysql/mysql.sock" -u root \
    -Nse "SELECT 1" >/dev/null 2>&1
}
configure_private_mysql_access() {
  command -v mysql >/dev/null 2>&1 || return 1
  mysql --protocol=SOCKET --socket="$ROOT/.mysql/mysql.sock" -u root <<SQL >/dev/null 2>&1
CREATE USER IF NOT EXISTS 'root'@'127.0.0.1' IDENTIFIED BY '';
CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY '';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL
}
port_busy() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 "$port" >/dev/null 2>&1 && return 0
  return 1
}
start_private_mysql() {
  command -v mysqld >/dev/null 2>&1 || return 1
  local data_dir="$ROOT/.mysql"
  local socket_file="$data_dir/mysql.sock"
  local pid_file="$PID_DIR/mysql.pid"
  mkdir -p "$data_dir" "$LOG_DIR" "$PID_DIR"
  if [ ! -d "$data_dir/mysql" ]; then
    gray "  初始化项目私有 MySQL 数据目录: .mysql"
    mysqld --initialize-insecure --datadir="$data_dir" --basedir=/usr/local/mysql --user="$USER" \
      --log-error="$LOG_DIR/mysql-init.log" >/dev/null 2>&1 || return 1
    # macOS 上 initialize 后残留的 undo 文件会让首次启动误判重复创建，删除后由正式启动重建。
    rm -f "$data_dir/undo_001" "$data_dir/undo_002"
  fi
  if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    return 0
  fi
  gray "  启动项目私有 MySQL: 127.0.0.1:$MYSQL_PORT"
  nohup mysqld \
    --datadir="$data_dir" \
    --basedir=/usr/local/mysql \
    --port="$MYSQL_PORT" \
    --socket="$socket_file" \
    --pid-file="$pid_file" \
    --log-error="$LOG_DIR/mysql.log" \
    --bind-address=127.0.0.1 \
    --skip-name-resolve \
    --mysqlx=0 \
    --lower-case-table-names=2 \
    > "$LOG_DIR/mysql.out" 2>&1 &
  echo $! > "$pid_file"
  return 0
}

# ───────────────────────── 3. Redis ─────────────────────────
cyan "[3/7] 启动 Redis (db=2 用作问数缓存)…"
if redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
  green "  ✓ Redis 已在 6379 运行"
else
  redis-server --daemonize yes --port 6379 --dir "$LOG_DIR" --logfile "$LOG_DIR/redis.log" >/dev/null
  sleep 1
  if redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    green "  ✓ 已启动 Redis (后台模式，日志: logs/redis.log)"
  else
    red "  Redis 启动失败，查看 logs/redis.log"
    exit 3
  fi
fi

# ───────────────────────── 4. MySQL ─────────────────────────
cyan "[4/7] 准备本地 MySQL (chatbi)…"
if mysql_ping; then
  green "  ✓ MySQL 已可连接: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"
else
  case "$MYSQL_HOST" in
    127.0.0.1|localhost)
      if ! is_placeholder "$mysql_pwd_from_env"; then
        red "  MySQL 不可连接: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"
        red "  backend/.env 已显式配置 MYSQL_PASSWORD，请确认本机 MySQL 已启动且密码正确。"
        exit 3
      fi
      if port_busy "$MYSQL_PORT"; then
        for p in 3307 3308 3309; do
          if ! port_busy "$p"; then export MYSQL_PORT="$p"; break; fi
        done
        gray "  3306 已被本机其它 MySQL 占用，改用本项目 MySQL 端口 $MYSQL_PORT"
      fi

      private_mysql_ready=0
      export MYSQL_PASSWORD=""
      if start_private_mysql; then
        for _ in $(seq 1 60); do private_mysql_socket_ping && break; sleep 1; done
        configure_private_mysql_access || true
        for _ in $(seq 1 10); do mysql_ping && break; sleep 1; done
        if mysql_ping; then
          green "  ✓ 项目私有 MySQL 已就绪: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"
          private_mysql_ready=1
        fi
      fi

      if [ "$private_mysql_ready" = "0" ]; then
        gray "  项目私有 MySQL 启动失败或不可用，查看 logs/mysql.log；尝试 Docker 兜底…"
        if ! command -v docker >/dev/null 2>&1; then
          red "  本地 MySQL 不可连接，项目私有 MySQL 启动失败，且未安装 Docker。"
          exit 3
        fi
        if ! docker info >/dev/null 2>&1; then
          red "  本地 MySQL 不可连接，项目私有 MySQL 启动失败，且 Docker Desktop 未运行。"
          exit 3
        fi

        export MYSQL_PASSWORD="datachat_local_2026"
        MYSQL_CONTAINER="datachat-mysql-${MYSQL_PORT}"
        if docker ps -a --format '{{.Names}}' | grep -qx "$MYSQL_CONTAINER"; then
          gray "  启动已有容器 ${MYSQL_CONTAINER}…"
          docker start "$MYSQL_CONTAINER" >/dev/null
        else
          gray "  创建本地 MySQL 容器 ${MYSQL_CONTAINER}（root 密码: datachat_local_2026）…"
          docker run -d --name "$MYSQL_CONTAINER" \
            -e MYSQL_ROOT_PASSWORD="$MYSQL_PASSWORD" \
            -e MYSQL_DATABASE="$MYSQL_DATABASE" \
            -p "$MYSQL_PORT:3306" \
            mysql:8.0 >/dev/null || {
              red "  MySQL 容器启动失败。若端口已被占用，请把真实 MYSQL_PASSWORD 写入 backend/.env 后重跑。"
              exit 3
            }
        fi
        for _ in $(seq 1 60); do mysql_ping && break; sleep 1; done
        mysql_ping || { red "  MySQL 60s 内未就绪，请查看: docker logs ${MYSQL_CONTAINER}"; exit 3; }
        green "  ✓ Docker MySQL 已就绪: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"
      fi
      ;;
    *)
      red "  MySQL 不可连接: $MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"
      red "  请检查 backend/.env 中 MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DATABASE"
      exit 3
      ;;
  esac
fi

# ───────────────────────── 5. Python venv & 依赖 ─────────────────────────
cyan "[5/7] 准备 Python 环境…"
if [ ! -d "$BACKEND/.venv" ]; then
  gray "  创建 venv (python3.11)…"
  python3.11 -m venv "$BACKEND/.venv"
fi
PYBIN="$BACKEND/.venv/bin/python"
if ! "$PYBIN" -c "import fastapi, sqlglot, redis, bcrypt, jwt" >/dev/null 2>&1; then
  gray "  安装/更新 backend/requirements.txt …"
  "$PYBIN" -m pip install --upgrade pip >/dev/null
  "$PYBIN" -m pip install -r "$BACKEND/requirements.txt" >/tmp/datachat-pip.log 2>&1 || {
    red "  pip install 失败，查看 /tmp/datachat-pip.log"; exit 4;
  }
fi
green "  ✓ Python 依赖就绪"
gray "  初始化本地 chatbi 样例表（已有数据则跳过插入）…"
"$PYBIN" "$ROOT/scripts/init_local_mysql.py" >/tmp/datachat-mysql-init.log 2>&1 || {
  red "  初始化 MySQL 失败，查看 /tmp/datachat-mysql-init.log"
  tail -n 40 /tmp/datachat-mysql-init.log
  exit 4
}
green "  ✓ $(tail -n 1 /tmp/datachat-mysql-init.log)"

# ───────────────────────── 6. 前端构建 ─────────────────────────
cyan "[6/7] 准备前端…"
if [ ! -d "$FRONTEND/node_modules" ]; then
  gray "  npm install (首次较慢)…"
  ( cd "$FRONTEND" && npm install --no-audit --no-fund ) >/tmp/datachat-npm.log 2>&1 || {
    red "  npm install 失败，查看 /tmp/datachat-npm.log"; exit 5;
  }
fi
if [ ! -f "$BACKEND/web/index.html" ] || [ "${1:-}" = "--rebuild" ]; then
  gray "  vite build → backend/web …"
  ( cd "$FRONTEND" && npx vite build ) >/tmp/datachat-build.log 2>&1 || {
    red "  前端构建失败，查看 /tmp/datachat-build.log"; exit 5;
  }
fi
green "  ✓ 前端构建产物在 backend/web/"

# ───────────────────────── 7. 启动 uvicorn ─────────────────────────
cyan "[7/7] 启动后端服务…"
PID_FILE="$PID_DIR/uvicorn.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  gray "  后端已在运行 pid=$(cat "$PID_FILE")，先停止…"
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  sleep 1
fi
# 杀掉任何残留 8001 占用
lsof -ti tcp:$PORT 2>/dev/null | xargs -r kill -9 2>/dev/null || true

cd "$BACKEND"
nohup "$PYBIN" -m uvicorn app.main:app \
  --host 127.0.0.1 --port $PORT --log-level info \
  > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PID_FILE"

# 等待健康检查
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  sleep 1
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    break
  fi
done
if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; then
  red "  后端 12s 内未就绪，查看 logs/backend.log:"
  tail -n 30 "$LOG_DIR/backend.log"
  exit 6
fi

green "  ✓ 后端已就绪 pid=$(cat "$PID_FILE")"
echo ""
cyan "─────────────────── 启动成功 ───────────────────"
green "  Web UI:    http://127.0.0.1:$PORT/web/"
green "  API 文档:  http://127.0.0.1:$PORT/api/docs"
green "  健康探活:  http://127.0.0.1:$PORT/api/health"
echo ""
gray "  默认管理员: admin"
gray "  默认密码:   admin@2026   (建议立即修改: ./scripts/reset_admin.sh)"
gray "  停止服务:   ./stop.sh"
gray "  实时日志:   tail -F logs/backend.log"
cyan "──────────────────────────────────────────────"
