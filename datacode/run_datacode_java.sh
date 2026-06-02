#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/config/datacode.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/config/datacode.env"
  set +a
fi

export DATACODE_SERVER_PORT="${DATACODE_SERVER_PORT:-18082}"
CP_FILE="$ROOT_DIR/target/datacode-classpath.txt"

if [[ ! -s "$CP_FILE" ]]; then
  echo "缺少 classpath 文件：$CP_FILE，请先执行 start_datacode_java.sh。"
  exit 1
fi

exec java -cp "$ROOT_DIR/target/classes:$(cat "$CP_FILE")" \
  com.feihe.datacode.DataCodeApplication
