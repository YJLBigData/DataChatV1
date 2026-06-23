#!/usr/bin/env bash
# DataChatV1 一键发布（本地）：前端类型检查 → 前端构建 → 后端测试 → 安全推送。
#
# 为什么需要它：服务器(CentOS7) **不构建前端**，只 git pull 现成的 backend/web 构建产物；
# 若忘了重建前端就 push，服务器永远是旧界面（这正是之前"数据反问没改过来"的根因）。
# 本脚本把"重建前端 + 跑测试 + 安全推送"焊成一步，保证 push 出去的一定是最新且通过测试的代码。
#
# 用法：
#   bash scripts/release.sh "0623版本更新: 修复xxx"
# 可选开关：
#   SKIP_TESTS=1   跳过后端测试（仅紧急热修时用）
#   SKIP_BUILD=1   跳过前端构建（仅当本次完全没动前端时用）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MSG="${1:-chore: release DataChatV1}"

cyan()  { printf '\033[36m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
gray()  { printf '\033[90m%s\033[0m\n' "$*"; }

PYBIN="$ROOT/backend/.venv/bin/python"
[ -x "$PYBIN" ] || { red "找不到 venv：$PYBIN，请先 bash start.sh 或创建虚拟环境"; exit 1; }

echo "═══ DataChatV1 一键发布 ═══"

# ── 1. 前端类型检查（vite build 用 esbuild 不查类型，必须单独 tsc 兜底）──────
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  cyan "[1/4] 前端类型检查 tsc --noEmit…"
  ( cd frontend && npx tsc --noEmit ) || { red "  ✗ 前端类型检查未通过，已中止（未 push）"; exit 1; }
  green "  ✓ 类型检查通过"

  # ── 2. 前端构建 → backend/web（服务器直接吃这个产物）────────────────────
  cyan "[2/4] 前端构建 vite build → backend/web…"
  ( cd frontend && npm run build >/tmp/datachat-release-build.log 2>&1 ) || {
    red "  ✗ 前端构建失败，详见 /tmp/datachat-release-build.log"; tail -n 20 /tmp/datachat-release-build.log; exit 1; }
  built_js="$(grep -o 'index-[A-Za-z0-9_-]*\.js' backend/web/index.html | head -1)"
  green "  ✓ 构建完成，主包：$built_js"
else
  gray "[1-2/4] SKIP_BUILD=1，跳过前端类型检查与构建"
fi

# ── 3. 后端测试（门禁：失败就不发布）────────────────────────────────────
if [ "${SKIP_TESTS:-0}" != "1" ]; then
  cyan "[3/4] 后端测试 pytest（APP_ENV=local）…"
  APP_ENV=local "$PYBIN" -m pytest backend/tests -q >/tmp/datachat-release-tests.log 2>&1 || {
    red "  ✗ 后端测试未通过，已中止（未 push）。末尾日志："; tail -n 25 /tmp/datachat-release-tests.log; exit 1; }
  green "  ✓ $(grep -oE '[0-9]+ passed.*' /tmp/datachat-release-tests.log | tail -1)"
else
  gray "[3/4] SKIP_TESTS=1，跳过后端测试"
fi

# ── 4. 安全推送（复用既有 safe_push：密钥扫描 + git add -A + commit + push）──
cyan "[4/4] 安全推送到 origin/main…"
bash "$ROOT/scripts/safe_push.sh" "$MSG"

echo
green "═══ 本地发布完成：代码与最新前端构建已推送到 origin/main ═══"
cat <<'EOF'

下一步——登录服务器执行（让线上 = 最新）：

  bash /tmp/datachat/redeploy.sh

部署后做一次"诚实自检"（自检全绿仍报错时用它定位真因：前端缓存 / SmartQ未配 / 后端真错）：

  DATACHAT_ADMIN_PASS='管理员密码' bash /opt/datachatv1/scripts/diagnose_server.sh

（首次启用智能小Q：在 /opt/datachatv1/.env 配 SMARTQ_ENABLED=1 与密钥后 systemctl restart datachatv1）
EOF
