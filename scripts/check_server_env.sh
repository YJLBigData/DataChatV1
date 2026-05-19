#!/usr/bin/env bash
# DataChat server preflight checker.
# Run this on the target server before deployment:
#   bash scripts/check_server_env.sh
#
# The script is read-only. It does not print secret values.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<'USAGE'
DataChat server preflight checker.

Usage:
  bash scripts/check_server_env.sh
  bash /tmp/check_server_env.sh /path/to/DataChatV1
  DATACHAT_PROJECT_ROOT=/path/to/DataChatV1 bash /tmp/check_server_env.sh

When this script is copied to /tmp, pass the project root explicitly.
USAGE
  exit 0
fi

if [ -n "${1:-}" ]; then
  ROOT="$1"
elif [ -n "${DATACHAT_PROJECT_ROOT:-}" ]; then
  ROOT="$DATACHAT_PROJECT_ROOT"
elif [ -f "$PWD/backend/app/main.py" ]; then
  ROOT="$PWD"
elif [ -f "$SCRIPT_DIR/../backend/app/main.py" ]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

ROOT="$(cd "$ROOT" 2>/dev/null && pwd || printf "%s" "$ROOT")"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
REQ_FILE="$BACKEND/requirements.txt"

FAILS=0
WARNS=0

if [ -t 1 ]; then
  C_RED="$(printf '\033[31m')"
  C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"
  C_CYAN="$(printf '\033[36m')"
  C_GRAY="$(printf '\033[90m')"
  C_RESET="$(printf '\033[0m')"
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_GRAY=""; C_RESET=""
fi

section() { printf "\n%s== %s ==%s\n" "$C_CYAN" "$*" "$C_RESET"; }
info()    { printf "%s[INFO]%s %s\n" "$C_GRAY" "$C_RESET" "$*"; }
pass()    { printf "%s[ OK ]%s %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()    { WARNS=$((WARNS + 1)); printf "%s[WARN]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
fail()    { FAILS=$((FAILS + 1)); printf "%s[FAIL]%s %s\n" "$C_RED" "$C_RESET" "$*"; }
have()    { command -v "$1" >/dev/null 2>&1; }

line_value_from_env_file() {
  local file="$1"
  local key="$2"
  [ -f "$file" ] || return 1
  local line
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 || true)"
  [ -n "$line" ] || return 1
  local value="${line#*=}"
  value="${value%$'\r'}"
  value="${value#\"}"; value="${value%\"}"
  value="${value#\'}"; value="${value%\'}"
  printf "%s" "$value"
}

env_get() {
  local key="$1"
  local value="${!key-}"
  if [ -n "$value" ]; then
    printf "%s" "$value"
    return 0
  fi
  local f
  for f in "$BACKEND/.env" "$ROOT/.env"; do
    if value="$(line_value_from_env_file "$f" "$key")"; then
      printf "%s" "$value"
      return 0
    fi
  done
  return 1
}

secret_state() {
  local key="$1"
  local value="${2:-}"
  if [ -z "$value" ]; then
    fail "$key is missing"
    return
  fi
  if [[ "$value" == *"请填写"* ]] || [[ "$value" == *"please-change"* ]] || [[ "$value" == *"password"* && "$key" != "DATACHAT_ADMIN_PASSWORD" ]]; then
    fail "$key looks like a placeholder"
    return
  fi
  pass "$key is configured (length ${#value})"
}

check_port() {
  local port="$1"
  local label="$2"
  if have ss; then
    local hit
    hit="$(ss -ltnp 2>/dev/null | awk -v p=":$port" '$4 ~ p "$" {print}' | head -n 1)"
    if [ -n "$hit" ]; then
      warn "$label port $port is already listening: $hit"
    else
      pass "$label port $port is free"
    fi
  elif have lsof; then
    local hit
    hit="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed -n '2p')"
    if [ -n "$hit" ]; then
      warn "$label port $port is already listening: $hit"
    else
      pass "$label port $port is free"
    fi
  else
    warn "Cannot check $label port $port because neither ss nor lsof is installed"
  fi
}

section "Project"
info "Project root: $ROOT"
for p in \
  "$BACKEND/app/main.py" \
  "$BACKEND/app/core/config.py" \
  "$BACKEND/config/semantic.yaml" \
  "$REQ_FILE" \
  "$FRONTEND/package.json" \
  "$FRONTEND/package-lock.json" \
  "$ROOT/start.sh" \
  "$ROOT/stop.sh"; do
  if [ -e "$p" ]; then
    pass "Found ${p#$ROOT/}"
  else
    fail "Missing ${p#$ROOT/}"
  fi
done

if [ -f "$BACKEND/web/index.html" ]; then
  pass "Found built frontend: backend/web/index.html"
else
  warn "backend/web/index.html not found; run frontend build before serving static UI"
fi

if have git && [ -d "$ROOT/.git" ]; then
  info "Git branch: $(git -C "$ROOT" branch --show-current 2>/dev/null || echo unknown)"
  dirty_count="$(git -C "$ROOT" status --short 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${dirty_count:-0}" -gt 0 ]; then
    warn "Git worktree is dirty ($dirty_count changed/untracked entries)"
  else
    pass "Git worktree is clean"
  fi
else
  warn "Git is not installed or this is not a git checkout"
fi

section "Operating System"
info "Hostname: $(hostname 2>/dev/null || echo unknown)"
info "Current user: $(id -un 2>/dev/null || whoami 2>/dev/null || echo unknown)"
info "Date: $(date '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || date)"
info "Kernel: $(uname -a 2>/dev/null || echo unknown)"
if [ -f /etc/os-release ]; then
  . /etc/os-release
  info "OS: ${PRETTY_NAME:-unknown}"
elif have sw_vers; then
  info "OS: $(sw_vers -productName) $(sw_vers -productVersion)"
else
  warn "Cannot determine OS release"
fi

if have nproc; then
  cpu_count="$(nproc)"
elif have sysctl; then
  cpu_count="$(sysctl -n hw.ncpu 2>/dev/null || echo 0)"
else
  cpu_count=0
fi
if [ "${cpu_count:-0}" -ge 2 ]; then
  pass "CPU cores: $cpu_count"
else
  warn "CPU cores: ${cpu_count:-unknown}; recommended >= 2"
fi

if have free; then
  free -h | sed 's/^/[INFO] /'
  mem_mb="$(free -m | awk '/^Mem:/ {print $2}')"
  if [ "${mem_mb:-0}" -lt 2048 ]; then
    warn "Memory is below 2GB; LLM planning + build may be unstable"
  else
    pass "Memory looks sufficient (${mem_mb}MB)"
  fi
else
  warn "free command not found; skipped memory check"
fi

df -h "$ROOT" 2>/dev/null | sed 's/^/[INFO] /'
disk_pct="$(df -P "$ROOT" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}')"
if [ -n "${disk_pct:-}" ] && [ "$disk_pct" -ge 90 ]; then
  fail "Disk usage is ${disk_pct}% on project filesystem"
elif [ -n "${disk_pct:-}" ] && [ "$disk_pct" -ge 80 ]; then
  warn "Disk usage is ${disk_pct}% on project filesystem"
else
  pass "Disk usage is acceptable (${disk_pct:-unknown}%)"
fi

ulimit_n="$(ulimit -n 2>/dev/null || echo unknown)"
info "Open file limit: $ulimit_n"
if [ "$ulimit_n" != "unknown" ] && [ "$ulimit_n" -lt 4096 ] 2>/dev/null; then
  warn "Open file limit is low; recommended >= 4096"
fi
if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
  warn "You are running as root; deploy app service with a non-root user if possible"
fi

section "System Commands"
for cmd in curl git bash awk sed grep; do
  if have "$cmd"; then pass "$cmd: $(command -v "$cmd")"; else fail "$cmd is missing"; fi
done
for cmd in ss lsof systemctl nginx ufw firewall-cmd; do
  if have "$cmd"; then pass "$cmd: $(command -v "$cmd")"; else warn "$cmd not found (optional, but useful for deployment diagnostics)"; fi
done

section "Python Runtime"
PYBIN=""
if have python3.11; then
  PYBIN="$(command -v python3.11)"
elif have python3; then
  PYBIN="$(command -v python3)"
fi

if [ -z "$PYBIN" ]; then
  fail "python3.11 is missing"
else
  py_ver="$("$PYBIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
  if "$PYBIN" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  then
    pass "Python version is OK: $py_ver ($PYBIN)"
  else
    fail "Python >= 3.11 is required, found $py_ver ($PYBIN)"
  fi
fi

if [ -d "$BACKEND/.venv" ] && [ -x "$BACKEND/.venv/bin/python" ]; then
  APP_PY="$BACKEND/.venv/bin/python"
  pass "Found backend venv: backend/.venv"
else
  APP_PY="$PYBIN"
  warn "backend/.venv not found; dependency checks will use system Python"
fi

if [ -n "${APP_PY:-}" ]; then
  if "$APP_PY" -m pip --version >/dev/null 2>&1; then
    pass "pip is available: $("$APP_PY" -m pip --version 2>/dev/null)"
  else
    fail "pip is missing for $APP_PY"
  fi

  module_report="$("$APP_PY" - <<'PY' 2>&1
import importlib
import sys

required = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pymysql", "pymysql"),
    ("SQLAlchemy", "sqlalchemy"),
    ("PyYAML", "yaml"),
    ("httpx", "httpx"),
    ("numpy", "numpy"),
    ("python-docx", "docx"),
    ("lark-oapi", "lark_oapi"),
    ("bcrypt", "bcrypt"),
    ("PyJWT", "jwt"),
    ("matplotlib", "matplotlib"),
    ("aiofiles", "aiofiles"),
    ("python-multipart", "multipart"),
    ("openpyxl", "openpyxl"),
    ("duckdb", "duckdb"),
    ("psycopg", "psycopg"),
    ("pytest", "pytest"),
    ("Jinja2", "jinja2"),
    ("pycryptodome", "Crypto"),
    ("sqlglot", "sqlglot"),
    ("redis", "redis"),
]

missing = []
for package, module in required:
    try:
        importlib.import_module(module)
    except Exception as exc:
        missing.append((package, module, type(exc).__name__, str(exc)))

if missing:
    print("Missing Python modules:")
    for package, module, etype, msg in missing:
        print(f"  - {package} (import {module}): {etype}: {msg}")
    sys.exit(1)

print("All required Python modules can be imported.")
PY
)"
  if [ $? -eq 0 ]; then
    pass "$module_report"
  else
    printf "%s\n" "$module_report" | sed 's/^/[INFO] /'
    fail "Python dependency import check failed"
  fi
fi

if [ -f "$REQ_FILE" ]; then
  for pkg in sqlglot redis; do
    if grep -Eiq "^[[:space:]]*${pkg}([<=>[:space:]]|$)" "$REQ_FILE"; then
      pass "requirements.txt declares $pkg"
    else
      fail "requirements.txt does not declare $pkg; project code depends on it"
    fi
  done
fi

if [ -n "${APP_PY:-}" ]; then
  app_import_report="$(cd "$BACKEND" && "$APP_PY" - <<'PY' 2>&1
from app.core.config import load_config
from app.core.semantic import SemanticLayer
from app.main import app

cfg = load_config(reload=True)
semantic = SemanticLayer(cfg.app.semantic_path)
print(f"FastAPI import OK; routes={len(app.routes)}")
print(f"Semantic OK; tables={len(semantic.tables)} metrics={len(semantic.metrics)} dimensions={len(semantic.dimensions)}")
PY
)"
  if [ $? -eq 0 ]; then
    printf "%s\n" "$app_import_report" | sed 's/^/[INFO] /'
    pass "Backend import smoke check passed"
  else
    printf "%s\n" "$app_import_report" | sed 's/^/[INFO] /'
    fail "Backend import smoke check failed"
  fi
fi

section "Node And Frontend"
if have node; then
  node_ver="$(node -v 2>/dev/null || echo unknown)"
  node_major="$(printf "%s" "$node_ver" | sed 's/^v//' | cut -d. -f1)"
  if [ "${node_major:-0}" -ge 20 ] 2>/dev/null; then
    pass "Node version is OK: $node_ver"
  else
    fail "Node >= 20 is recommended, found $node_ver"
  fi
else
  fail "node is missing"
fi

if have npm; then
  pass "npm version: $(npm -v 2>/dev/null || echo unknown)"
else
  fail "npm is missing"
fi

if [ -d "$FRONTEND/node_modules" ]; then
  pass "frontend/node_modules exists"
else
  warn "frontend/node_modules not found; run 'cd frontend && npm ci' or let start.sh install"
fi

if [ "${RUN_BUILD_CHECK:-0}" = "1" ]; then
  if (cd "$FRONTEND" && npm run build); then
    pass "Frontend production build passed"
  else
    fail "Frontend production build failed"
  fi
else
  info "Skipped frontend build. Set RUN_BUILD_CHECK=1 to run npm build."
fi

section "Environment Variables"
ENV_FILES_FOUND=0
for f in "$BACKEND/.env" "$ROOT/.env"; do
  if [ -f "$f" ]; then
    ENV_FILES_FOUND=$((ENV_FILES_FOUND + 1))
    pass "Found env file: ${f#$ROOT/}"
    if have stat; then
      mode="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f" 2>/dev/null || echo unknown)"
      info "${f#$ROOT/} permission mode: $mode"
      if [ "$mode" != "unknown" ] && [ "$mode" -gt 640 ] 2>/dev/null; then
        warn "${f#$ROOT/} is readable too broadly; recommended chmod 600 or 640"
      fi
    fi
  fi
done
if [ "$ENV_FILES_FOUND" -eq 0 ]; then
  fail "No .env file found. Expected backend/.env for deployment."
fi

for key in DASHSCOPE_API_KEY MYSQL_HOST MYSQL_PORT MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE DATACHAT_ADMIN_PASSWORD; do
  value="$(env_get "$key" || true)"
  secret_state "$key" "$value"
done

jwt_secret="$(env_get JWT_SECRET || true)"
if [ -z "$jwt_secret" ]; then
  fail "JWT_SECRET is missing; do not deploy with code default"
elif [ "$jwt_secret" = "datachat-local-dev-secret" ] || [[ "$jwt_secret" == *"local-dev"* ]] || [[ "$jwt_secret" == *"please-change"* ]] || [ "${#jwt_secret}" -lt 32 ]; then
  fail "JWT_SECRET is weak or looks like a local placeholder (length ${#jwt_secret})"
else
  pass "JWT_SECRET looks configured (length ${#jwt_secret})"
fi

redis_url="$(env_get DATACHAT_REDIS_URL || true)"
if [ -z "$redis_url" ]; then
  redis_url="redis://127.0.0.1:6379/2"
  warn "DATACHAT_REDIS_URL missing; backend default will be $redis_url"
else
  pass "DATACHAT_REDIS_URL is configured"
fi

for key in FEISHU_APP_ID FEISHU_APP_SECRET FEISHU_WEBHOOK FEISHU_DEFAULT_USER_EMAIL; do
  value="$(env_get "$key" || true)"
  if [ -n "$value" ]; then
    pass "$key is configured (optional, length ${#value})"
  else
    warn "$key is not configured (optional; Feishu push may be unavailable)"
  fi
done

section "Network And Services"
app_port="$(env_get APP_PORT || true)"
app_port="${app_port:-8001}"
check_port "$app_port" "DataChat app"
check_port 6379 "Redis"

if have redis-server; then
  pass "redis-server exists: $(redis-server --version 2>/dev/null | head -n 1)"
else
  warn "redis-server is missing. OK only if using managed/external Redis."
fi

if have redis-cli; then
  if redis-cli -u "$redis_url" ping >/tmp/datachat-redis-ping.$$ 2>&1; then
    pass "Redis ping OK: $redis_url"
  else
    warn "Redis ping failed for $redis_url: $(tr '\n' ' ' </tmp/datachat-redis-ping.$$ | cut -c1-200)"
  fi
  rm -f /tmp/datachat-redis-ping.$$
else
  warn "redis-cli is missing; cannot test Redis connectivity"
fi

mysql_host="$(env_get MYSQL_HOST || true)"
mysql_port="$(env_get MYSQL_PORT || true)"
mysql_user="$(env_get MYSQL_USER || true)"
mysql_pwd="$(env_get MYSQL_PASSWORD || true)"
mysql_db="$(env_get MYSQL_DATABASE || true)"
mysql_port="${mysql_port:-3306}"

if have mysqladmin; then
  if MYSQL_PWD="$mysql_pwd" mysqladmin ping -h "${mysql_host:-127.0.0.1}" -P "$mysql_port" -u "${mysql_user:-root}" --connect-timeout=5 >/tmp/datachat-mysql-ping.$$ 2>&1; then
    pass "MySQL ping OK: ${mysql_user:-root}@${mysql_host:-127.0.0.1}:$mysql_port/$mysql_db"
  else
    fail "MySQL ping failed: $(tr '\n' ' ' </tmp/datachat-mysql-ping.$$ | cut -c1-240)"
  fi
  rm -f /tmp/datachat-mysql-ping.$$
elif have mysql; then
  if MYSQL_PWD="$mysql_pwd" mysql -h "${mysql_host:-127.0.0.1}" -P "$mysql_port" -u "${mysql_user:-root}" "${mysql_db:-}" -e "SELECT 1;" >/tmp/datachat-mysql-ping.$$ 2>&1; then
    pass "MySQL SELECT 1 OK"
  else
    fail "MySQL SELECT 1 failed: $(tr '\n' ' ' </tmp/datachat-mysql-ping.$$ | cut -c1-240)"
  fi
  rm -f /tmp/datachat-mysql-ping.$$
else
  fail "mysql/mysqladmin client is missing; install mysql-client or mariadb-client"
fi

if have curl; then
  dashscope_code="$(curl -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code}' https://dashscope.aliyuncs.com 2>/tmp/datachat-dashscope-curl.$$ || true)"
  if [ "$dashscope_code" != "000" ] && [ -n "$dashscope_code" ]; then
    pass "DashScope host is reachable (HTTP $dashscope_code)"
  else
    warn "Cannot reach https://dashscope.aliyuncs.com from this server: $(tr '\n' ' ' </tmp/datachat-dashscope-curl.$$ | cut -c1-200)"
  fi
  rm -f /tmp/datachat-dashscope-curl.$$
fi

section "Deployment Hints"
info "Install backend deps: cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
info "Install frontend deps: cd frontend && npm ci"
info "Build frontend: cd frontend && npm run build"
info "Start app: ./start.sh --rebuild"
info "Smoke test: curl -fsS http://127.0.0.1:${app_port}/health"
info "Run non-e2e tests: cd backend && .venv/bin/python -m pytest tests/ -m 'not e2e' -v"

section "Summary"
if [ "$FAILS" -eq 0 ] && [ "$WARNS" -eq 0 ]; then
  pass "Server looks ready for this project."
elif [ "$FAILS" -eq 0 ]; then
  warn "No blocking failures, but $WARNS warning(s) should be reviewed."
else
  fail "$FAILS blocking issue(s), $WARNS warning(s). Fix blocking issues before deployment."
fi

exit "$FAILS"
