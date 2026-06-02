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
PID_FILE="$ROOT_DIR/logs/datacode-java.pid"
LOG_FILE="$ROOT_DIR/logs/datacode-java.log"
BUILD_LOG="$ROOT_DIR/logs/datacode-java-build.log"
CP_FILE="$ROOT_DIR/target/datacode-classpath.txt"
LAUNCH_LABEL="com.feihe.datacode.java"
PLIST_FILE="$ROOT_DIR/logs/${LAUNCH_LABEL}.plist"

mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/storage/uploads"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    echo "DataCode Java 已在运行，PID=${OLD_PID}，访问地址：http://127.0.0.1:${DATACODE_SERVER_PORT}"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

if lsof -iTCP:"$DATACODE_SERVER_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 ${DATACODE_SERVER_PORT} 已被占用，请修改 DATACODE_SERVER_PORT 后再启动。"
  exit 1
fi

if [[ -z "${DATACODE_LLM_API_KEY:-${DASHSCOPE_API_KEY:-${QWEN_API_KEY:-}}}" ]]; then
  echo "提示：未配置 DATACODE_LLM_API_KEY / DASHSCOPE_API_KEY / QWEN_API_KEY，代码生成功能会在调用模型时报错。"
fi

echo "正在构建 DataCode Java..."
if ! mvn -q -DskipTests compile dependency:build-classpath -Dmdep.outputFile="$CP_FILE" > "$BUILD_LOG" 2>&1; then
  echo "DataCode Java 构建失败，最近日志如下："
  tail -n 80 "$BUILD_LOG"
  exit 1
fi

if [[ ! -s "$CP_FILE" ]]; then
  echo "未生成依赖 classpath 文件：$CP_FILE"
  exit 1
fi

start_with_launchctl() {
  cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCH_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT_DIR}/run_datacode_java.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>StandardOutPath</key>
  <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_FILE}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
EOF

  launchctl bootout "gui/$(id -u)/${LAUNCH_LABEL}" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "$PLIST_FILE" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_FILE"
  launchctl kickstart -k "gui/$(id -u)/${LAUNCH_LABEL}" >/dev/null 2>&1 || true

  local pid=""
  for _ in {1..30}; do
    pid="$(launchctl print "gui/$(id -u)/${LAUNCH_LABEL}" 2>/dev/null | awk -F'= ' '/pid =/{print $2; exit}')"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "$pid"
      return 0
    fi
    sleep 1
  done
  return 1
}

if command -v launchctl >/dev/null 2>&1 && [[ "${DATACODE_USE_NOHUP:-0}" != "1" ]]; then
  PID="$(start_with_launchctl)"
else
  nohup "$ROOT_DIR/run_datacode_java.sh" > "$LOG_FILE" 2>&1 < /dev/null &
  PID="$!"
fi

echo "$PID" > "$PID_FILE"
echo "DataCode Java 启动中，PID=${PID}"
echo "访问地址：http://127.0.0.1:${DATACODE_SERVER_PORT}"
echo "日志文件：$LOG_FILE"
echo "构建日志：$BUILD_LOG"
