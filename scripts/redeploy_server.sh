#!/usr/bin/env bash
# DataChatV1 一键更新部署（解决审计 问题1：运行态≠源码 / 旧进程泄露诊断）
#
# 用法（在服务器上，root 执行即可）：
#   bash datachatv1_redeploy.sh                  # 拉取并重启到 git 最新
#   BRANCH=main bash datachatv1_redeploy.sh      # 指定分支（合并后用 main）
#   DATACHAT_DIR=/opt/datachatv1/app bash ...    # 指定仓库目录
#   DRY_RUN=1 bash datachatv1_redeploy.sh        # 只演示，不动服务
#
# 在 Mac 上一键远程执行（可选）：先设好免密或会提示输密码
#   DATACHAT_SSH=root@你的服务器IP bash ~/Desktop/datachatv1_redeploy.sh
#
# 该脚本保证：① 强制对齐 git HEAD（不再出现“运行旧代码”）② 清理 8001 旧进程
# ③ .venv 残缺自愈 ④ 重启后校验 /api/health 已脱敏、/api/admin/diagnostics 需鉴权。
set -euo pipefail

BRANCH="${BRANCH:-claude/angry-jang-310070}"
DATACHAT_DIR="${DATACHAT_DIR:-/opt/datachatv1/app}"
PORT="${PORT:-8001}"
BASE="http://127.0.0.1:${PORT}"
DRY_RUN="${DRY_RUN:-0}"

c(){ printf "\033[36m%s\033[0m\n" "$*"; }
g(){ printf "\033[32m%s\033[0m\n" "$*"; }
y(){ printf "\033[33m%s\033[0m\n" "$*"; }
r(){ printf "\033[31m%s\033[0m\n" "$*"; }
run(){ if [ "$DRY_RUN" = "1" ]; then y "  [dry-run] $*"; else eval "$*"; fi; }

# ── 0. 可选：从 Mac 远程到服务器执行自身 ─────────────────────────────
if [ -n "${DATACHAT_SSH:-}" ] && [ "${DATACHAT_REMOTE:-0}" != "1" ]; then
  c "═══ 远程部署到 ${DATACHAT_SSH} ═══"
  scp "$0" "${DATACHAT_SSH}:/tmp/datachatv1_redeploy.sh"
  exec ssh -t "${DATACHAT_SSH}" \
    "DATACHAT_REMOTE=1 BRANCH='${BRANCH}' DATACHAT_DIR='${DATACHAT_DIR}' PORT='${PORT}' DRY_RUN='${DRY_RUN}' bash /tmp/datachatv1_redeploy.sh"
fi

c "═══ DataChatV1 一键更新部署 ═══"
[ "$DRY_RUN" = "1" ] && y "  (DRY_RUN：仅演示，不修改任何服务)"

# ── 1. 定位仓库（兼容普通克隆与 worktree：.git 可能是目录或文件）──
is_repo(){ git -C "$1" rev-parse --is-inside-work-tree >/dev/null 2>&1; }
if ! is_repo "$DATACHAT_DIR"; then
  SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
  if is_repo "$SELF_DIR/.."; then
    DATACHAT_DIR="$(cd "$SELF_DIR/.." && pwd)"
  else
    r "  找不到 git 仓库：$DATACHAT_DIR（用 DATACHAT_DIR=/path 指定）"; exit 2
  fi
fi
cd "$DATACHAT_DIR"
g "  仓库目录: $DATACHAT_DIR"

# ── 2. 强制对齐 git HEAD（杜绝“运行旧代码”）────────────────────────
OLD_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
run "git fetch --prune origin"
# 有未提交改动先 stash（可恢复，不丢工作）；.env / logs 已 gitignore 不受影响
if ! git diff --quiet || ! git diff --cached --quiet; then
  y "  检测到本地改动，已 git stash 备份（git stash list 可查）"
  run "git stash push -u -m redeploy-$(date +%Y%m%d%H%M%S)"
fi
run "git checkout -q '$BRANCH'"
run "git reset --hard 'origin/$BRANCH'"
NEW_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
g "  代码已对齐: $OLD_COMMIT → $NEW_COMMIT (分支 $BRANCH)"

# ── 3. 停旧服务 + 清理 8001 残留进程 ───────────────────────────────
if [ -x "./stop.sh" ]; then
  run "./stop.sh || true"
fi
LEFT="$( (command -v lsof >/dev/null && lsof -ti tcp:$PORT) 2>/dev/null || true)"
if [ -n "$LEFT" ]; then
  y "  强清 $PORT 端口残留进程: $LEFT"
  run "echo '$LEFT' | xargs -r kill -9 || true"
fi
run "pkill -f 'uvicorn app.main:app' 2>/dev/null || true"

# ── 4. .venv 残缺自愈（start.sh 也会自愈，这里多一道保险）──────────
if [ -e "backend/.venv" ] && [ ! -x "backend/.venv/bin/python" ]; then
  y "  检测到残缺 .venv，删除以触发重建"
  run "rm -rf backend/.venv"
fi

# ── 5. 启动（start.sh 负责建 venv/装依赖/初始化/拉起 uvicorn）──────
if [ ! -x "./start.sh" ]; then r "  缺少 start.sh"; exit 3; fi
run "./start.sh"

if [ "$DRY_RUN" = "1" ]; then c "═══ DRY_RUN 结束 ═══"; exit 0; fi

# ── 6. 等待并校验健康（部署验收）──────────────────────────────────
c "  等待服务就绪…"
ok=0
for _ in $(seq 1 30); do
  if curl -fsS "$BASE/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" = "1" ] || { r "  服务未在 60s 内就绪，检查 logs/backend.log"; exit 4; }

HJSON="$(curl -fsS "$BASE/api/health" 2>/dev/null || echo '{}')"
g "  /api/health → $HJSON"
LEAK=0
for bad in '"host"' '"database"' 'redis://' 'aliyuncs' '"provider"' '"model"' '"llm"' '"semantic"'; do
  if printf '%s' "$HJSON" | grep -qi "$bad"; then r "  ✗ /api/health 仍泄露: $bad"; LEAK=1; fi
done
[ "$LEAK" = "0" ] && g "  ✓ /api/health 已脱敏（无 DB/Redis/LLM 诊断）"

DCODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/admin/diagnostics" || echo 000)"
if [ "$DCODE" = "401" ] || [ "$DCODE" = "403" ]; then
  g "  ✓ /api/admin/diagnostics 未鉴权被拒（HTTP $DCODE）"
else
  r "  ✗ /api/admin/diagnostics 未鉴权返回 HTTP $DCODE（应为 401/403）"; LEAK=1
fi

# ── 7. 提示一次性管理员口令位置（问题2）───────────────────────────
PWF="backend/logs/INITIAL_ADMIN_PASSWORD.txt"
if [ -f "$PWF" ]; then
  y "  首次部署生成的一次性管理员口令在: $DATACHAT_DIR/$PWF"
  y "  （登录后请立即在后台修改，并可删除该文件）"
fi

echo ""
if [ "$LEAK" = "0" ]; then
  c "═══ 部署成功：运行 commit=$NEW_COMMIT，健康检查通过 ═══"
else
  r "═══ 部署完成但校验未全过，请按上面 ✗ 项排查 ═══"; exit 5
fi
