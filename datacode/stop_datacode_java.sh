#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT_DIR/logs/datacode-java.pid"
DATACODE_SERVER_PORT="${DATACODE_SERVER_PORT:-18082}"
LAUNCH_LABEL="com.feihe.datacode.java"
PLIST_FILE="$ROOT_DIR/logs/${LAUNCH_LABEL}.plist"

stop_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  local children
  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  kill "$pid" >/dev/null 2>&1 || true
  for child in $children; do
    kill "$child" >/dev/null 2>&1 || true
  done

  for _ in {1..30}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  kill -9 "$pid" >/dev/null 2>&1 || true
  for child in $children; do
    kill -9 "$child" >/dev/null 2>&1 || true
  done
}

STOPPED=0
if command -v launchctl >/dev/null 2>&1; then
  if launchctl print "gui/$(id -u)/${LAUNCH_LABEL}" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/${LAUNCH_LABEL}" >/dev/null 2>&1 \
      || launchctl bootout "gui/$(id -u)" "$PLIST_FILE" >/dev/null 2>&1 \
      || true
    STOPPED=1
  fi
fi

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  if [[ -n "$PID" ]] && kill -0 "$PID" >/dev/null 2>&1; then
    stop_pid "$PID"
    STOPPED=1
  fi
  rm -f "$PID_FILE"
fi

PORT_PIDS="$(lsof -tiTCP:"$DATACODE_SERVER_PORT" -sTCP:LISTEN 2>/dev/null || true)"
for port_pid in $PORT_PIDS; do
  if ps -p "$port_pid" -o command= | grep -q 'com.feihe.datacode.DataCodeApplication'; then
    stop_pid "$port_pid"
    STOPPED=1
  fi
done

if [[ "$STOPPED" == "1" ]]; then
  echo "DataCode Java 已关闭。"
else
  echo "未发现运行中的 DataCode Java 服务。"
fi
