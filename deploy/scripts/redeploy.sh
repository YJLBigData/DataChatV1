#!/usr/bin/env bash
# DataChatV1 服务端一键部署（git-pull 模型 · 幂等 · 自带诚实自检）。
#
# 固化了 2026-06-23 三个踩坑根因，确保"一键就真上线"：
#   ① backend/web 强制与 git 一致（git clean 掉 rsync 时代遗留的未跟踪旧前端包）——
#      否则浏览器永远拿旧包（曾表现为「数据范围」改不过来 / 「数据反问」没变）。
#   ② 每次都把 systemd 单元重写成「指向 git 树 /opt/datachatv1/backend + 单 worker」——
#      根治"服务在跑 /opt/datachatv1/app/backend 旧副本、而 deploy 更新的是另一棵树"的飘移。
#   ③ 自检不仅测 /api/chat，还核对：进程 cwd 是否=git树、served 前端包是否=磁盘、
#      SmartQ 是否就绪、/api/chat/stream（浏览器真正走的）是否端到端通。
#
# 用法（root）：
#   DATACHAT_ADMIN_PASS='管理员密码' bash /opt/datachatv1/deploy/scripts/redeploy.sh
#   （不传密码也能跑，仅跳过需要登录的深度自检；其余照常）
#
# 绝不改：nginx / 域名 / 端口 / .env（密钥）。脚本会 git reset --hard，
# 但 .env、logs/、output(s)/ 均不在版本控制内，不受影响。
set -uo pipefail

REPO=/opt/datachatv1
BACKEND="$REPO/backend"
VENV="$BACKEND/.venv"
PYBIN="$VENV/bin/python"
PYBASE="${PYBASE:-/usr/local/python3.11/bin/python3.11}"
PORT=8001
BASE="http://127.0.0.1:$PORT"
SVC=datachatv1
OLD_APP="$REPO/app/backend"        # 旧副本（若还在，作为 venv 兜底来源）

g(){ printf '\033[32m%s\033[0m\n' "$*"; }
r(){ printf '\033[31m%s\033[0m\n' "$*"; }
y(){ printf '\033[33m%s\033[0m\n' "$*"; }
c(){ printf '\033[36m%s\033[0m\n' "$*"; }
die(){ r "✗ $*"; exit 1; }

cd "$REPO" || die "找不到 $REPO"

# ── 0) 对齐 git + 脚本自更新（保证跑的是最新部署逻辑）────────────────────
c "━━ 对齐 git 远端（origin/main）━━"
mkdir -p "$REPO/.deploy/backups"
tar --exclude='backend/.venv' -czf "$REPO/.deploy/backups/$(date +%Y%m%d_%H%M%S)_pre.tar.gz" \
    -C "$REPO" backend deploy scripts 2>/dev/null || true
git fetch origin --prune || die "git fetch 失败"
if [ "${_REDEPLOY_REEXEC:-0}" != "1" ]; then
  _b=$(sha256sum "$0" 2>/dev/null | cut -d' ' -f1)
  git reset --hard origin/main || die "git reset 失败"
  _a=$(sha256sum "$0" 2>/dev/null | cut -d' ' -f1)
  export _REDEPLOY_REEXEC=1
  if [ "$_b" != "$_a" ]; then y "» 部署脚本自身已更新，按最新逻辑重跑…"; exec bash "$0" "$@"; fi
else
  git reset --hard origin/main || die "git reset 失败"
fi
g "  ✓ 代码已对齐 $(git rev-parse --short HEAD)：$(git log -1 --pretty=%s)"

# ── 1) 前端产物：强制与 git 一致，清掉未跟踪旧包 ─────────────────────────
c "━━ 前端产物对齐 ━━"
git clean -fd backend/web >/dev/null 2>&1 || true
git checkout origin/main -- backend/web 2>/dev/null || true
DISK_JS=$(grep -o 'index-[A-Za-z0-9_-]*\.js' backend/web/index.html | head -1)
g "  ✓ backend/web 已对齐，主包 $DISK_JS"

# ── 2) Python 依赖：哈希闸（变了/缺口才装；清华镜像+超时重试）──────────
c "━━ Python 依赖 ━━"
if [ ! -x "$PYBIN" ]; then
  if [ -x "$OLD_APP/.venv/bin/python" ]; then y "  从旧副本复制 venv（免重下）…"; cp -a "$OLD_APP/.venv" "$VENV"
  else y "  新建 venv…"; "$PYBASE" -m venv "$VENV" || die "建 venv 失败"; fi
fi
STAMP="$VENV/.requirements.sha256"
CUR=$(sha256sum "$BACKEND/requirements.txt" | cut -d' ' -f1)
PREV=$(cat "$STAMP" 2>/dev/null || true)
if [ "$CUR" != "$PREV" ] || ! "$PYBIN" "$REPO/scripts/check_requirements.py" "$BACKEND/requirements.txt" >/dev/null 2>&1; then
  y "  依赖有变化/缺口，安装中（清华镜像，日志 /tmp/redeploy-pip.log）…"
  "$PYBIN" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 --retries 5 \
      -r "$BACKEND/requirements.txt" >/tmp/redeploy-pip.log 2>&1 || { tail -8 /tmp/redeploy-pip.log; die "pip 失败"; }
  printf '%s' "$CUR" > "$STAMP"
  g "  ✓ 依赖已安装/更新"
else
  g "  ✓ 依赖未变化，跳过"
fi
( cd "$BACKEND" && "$PYBIN" -c 'import app.main' ) >/dev/null 2>&1 \
  || die "import app.main 失败（依赖或代码有问题，已中止，未动 systemd）"
g "  ✓ app.main 可导入"

# ── 3) systemd 单元：每次重写成「指向 git 树 + 单 worker」（幂等，根治飘移）─
c "━━ systemd 单元对齐 ━━"
UNIT=/etc/systemd/system/$SVC.service
NEWUNIT=$(cat <<UNIT
[Unit]
Description=DataChatV1 问数服务 (FastAPI/Uvicorn)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$BACKEND
EnvironmentFile=$REPO/.env
ExecStart=$PYBIN -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 30 --log-level info
Restart=always
RestartSec=2
TimeoutStopSec=30
KillSignal=SIGTERM
LimitNOFILE=65535
MemoryAccounting=yes
MemoryLimit=5G

[Install]
WantedBy=multi-user.target
UNIT
)
if [ "$(cat "$UNIT" 2>/dev/null)" != "$NEWUNIT" ]; then
  [ -f "$UNIT" ] && cp -a "$UNIT" "$UNIT.bak.$(date +%Y%m%d_%H%M%S)"
  printf '%s\n' "$NEWUNIT" > "$UNIT"
  systemctl daemon-reload
  g "  ✓ 单元已更新（WorkingDirectory=$BACKEND，workers=1，host/port 不变）"
else
  g "  ✓ 单元已是规范态"
fi
systemctl enable "$SVC" >/dev/null 2>&1 || true

# ── 4) 重启 + 等存活 ─────────────────────────────────────────────────────
c "━━ 重启服务 ━━"
systemctl restart "$SVC"
for _ in $(seq 1 40); do curl -sf "$BASE/health" >/dev/null 2>&1 && break; sleep 1; done
[ "$(systemctl is-active "$SVC")" = active ] || { journalctl -u "$SVC" -n 30 --no-pager; die "服务未存活"; }
g "  ✓ 服务已存活"

# ── 5) 诚实自检 ──────────────────────────────────────────────────────────
c "━━ 自检 ━━"; FAIL=0
[ "$(systemctl is-active $SVC)" = active ] && g "  ✓ systemd active" || { r "  ✗ systemd 未 active"; FAIL=1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' $BASE/health)" = 200 ]     && g "  ✓ /health=200"     || { r "  ✗ /health≠200"; FAIL=1; }
[ "$(curl -s -o /dev/null -w '%{http_code}' $BASE/api/health)" = 200 ] && g "  ✓ /api/health=200" || { r "  ✗ /api/health≠200"; FAIL=1; }

# 关键①：进程确实在 git 树（防两棵树再次飘移）
NEWPID=$(systemctl show -p MainPID --value $SVC); CWD=$(readlink -f /proc/$NEWPID/cwd 2>/dev/null)
[ "$CWD" = "$BACKEND" ] && g "  ✓ 进程 cwd=$BACKEND（git树）" || { r "  ✗ 进程 cwd=$CWD ≠ $BACKEND"; FAIL=1; }

# 关键②：served 前端 == 磁盘前端（杜绝旧包）
SERVED=$(curl -s $BASE/web/ | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1)
[ "$SERVED" = "$DISK_JS" ] && g "  ✓ 前端 served=$SERVED 与磁盘一致" || { r "  ✗ served=$SERVED ≠ 磁盘=$DISK_JS"; FAIL=1; }

# 关键③：深度自检（需管理员密码）
if [ -n "${DATACHAT_ADMIN_PASS:-}" ]; then
  TK=$(curl -s -X POST "$BASE/api/login" -H 'Content-Type: application/json' \
       -d "{\"username\":\"${DATACHAT_ADMIN_USER:-admin@feihe.com}\",\"password\":\"$DATACHAT_ADMIN_PASS\"}" \
       | "$PYBIN" -c 'import sys,json;print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)
  if [ -n "$TK" ]; then
    g "  ✓ 管理员登录"
    SQ=$(curl -s "$BASE/api/smartq/status" -H "Authorization: Bearer $TK" \
         | "$PYBIN" -c 'import sys,json;print(json.load(sys.stdin).get("ready"))' 2>/dev/null || true)
    [ "$SQ" = "True" ] && g "  ✓ SmartQ ready" || y "  ! SmartQ 未就绪（如需小Q：在 $REPO/.env 配 SMARTQ_* 后重跑）"
    ST=$(curl -s -N --max-time 100 -X POST "$BASE/api/chat/stream" -H "Authorization: Bearer $TK" \
         -H 'Content-Type: application/json' -d '{"question":"本月各大区销售额排名","smartq_cube_ids":[]}' 2>/dev/null | tr -d '\r' || true)
    if   printf '%s' "$ST" | grep -q '^event: *done';  then g "  ✓ 流式问数端到端 done"
    elif printf '%s' "$ST" | grep -q '^event: *error'; then r "  ✗ 流式问数 error：$(printf '%s' "$ST"|grep -A1 '^event: *error'|grep '^data:'|head -1)"; FAIL=1
    else r "  ✗ 流式问数无 done/error（看 journalctl -u $SVC）"; FAIL=1; fi
  else
    y "  ! 管理员登录失败，跳过深度自检（检查 DATACHAT_ADMIN_PASS）"
  fi
else
  y "  · 未传 DATACHAT_ADMIN_PASS → 跳过登录类深度自检（服务/前端检查已完成）"
fi

echo
if [ "$FAIL" = 0 ]; then
  g "━━ ✅ 部署成功，自检全过。HEAD=$(git rev-parse --short HEAD)  前端=$DISK_JS ━━"
  y "提示：浏览器首访请 Ctrl+Shift+R 强刷一次，清掉本地旧前端缓存。"
else
  die "部署完成但有自检未过（见上方 ✗），请按提示处理"
fi
