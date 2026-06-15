#!/usr/bin/env bash
# 清理运行时产物 —— 减少本地审计/扫描/搜索时的噪声。
#
# 这些目录都已在 .gitignore 中（不入库），但 grep/ripgrep/安全扫描仍会扫到。
# 默认只清理"可再生"的产物：日志、pid、生成的报告、trace。
# SQLite 业务库（auth.db / conversations.db / query_log.db / permissions.db）默认保留，
# 传 --all 才一并删除（会丢失本地用户/会话/审计历史，请谨慎）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"

WIPE_DBS=0
[ "${1:-}" = "--all" ] && WIPE_DBS=1

echo "→ 清理运行时产物（ROOT=$ROOT）"

# pid / 锁
rm -f  "$ROOT"/.pids/*.pid "$BACKEND"/.pids/*.pid 2>/dev/null || true

# 日志
rm -f  "$BACKEND"/logs/*.log "$ROOT"/logs/*.log 2>/dev/null || true

# 生成的报告 docx
rm -f  "$BACKEND"/reports/generated/*.docx "$BACKEND"/reports/v1/generated/*.docx 2>/dev/null || true

# trace
rm -rf "$BACKEND"/traces/* "$BACKEND"/traces_v1/* "$ROOT"/traces/* 2>/dev/null || true

if [ "$WIPE_DBS" = "1" ]; then
  echo "  --all：同时删除本地 SQLite 库（用户/会话/审计/权限）"
  rm -f "$BACKEND"/logs/*.db "$BACKEND"/logs/*.db-wal "$BACKEND"/logs/*.db-shm 2>/dev/null || true
fi

echo "✓ 完成。保留 SQLite 业务库（如需一并清除：$0 --all）"
