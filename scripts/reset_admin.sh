#!/usr/bin/env bash
# 一键重置管理员（admin）密码
#   ./scripts/reset_admin.sh                # 交互式
#   ./scripts/reset_admin.sh 新密码          # 直接传参
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
PYBIN="$BACKEND/.venv/bin/python"

if [ ! -x "$PYBIN" ]; then
  echo "× 未找到 $PYBIN，请先运行 ./start.sh 完成初始化"
  exit 1
fi

cd "$BACKEND" && "$PYBIN" scripts/reset_admin.py "$@"
