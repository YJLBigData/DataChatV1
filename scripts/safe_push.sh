#!/usr/bin/env bash
# 一键安全上传 Git：先扫描敏感信息，干净才提交并 push。
# 用法：  bash scripts/safe_push.sh ["提交说明"]
# 任何疑似密钥/密码/真实连接串命中 → 直接中止，绝不 push。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MSG="${1:-chore: sync DataChatV1 (context fix / security / deploy)}"

echo "[safe_push] 1) 扫描将要进入仓库的文件…"
# 本脚本不再内含任何真实密钥/账号字面量，因此【不再排除自身】，可自检。
FILES="$( { git ls-files; git ls-files --others --exclude-standard; } | sort -u \
  | grep -vE '\.(png|jpg|jpeg|gif|ico|woff2?|ttf|map|lock)$' || true )"
[ -n "$FILES" ] || { echo "[safe_push] 无文件，跳过"; exit 0; }

# 敏感模式：通用规则（AK / 私钥 / token / 带密码连接串 / 明文密钥赋值 / 云数据库 Endpoint）。
# 不再写死任何真实密码/主机/账号；如需拦截项目专属真实值，放本地私有 denylist（见下，已 gitignore）。
PATTERN='AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|[A-Za-z0-9.-]+\.(rds|ads)\.aliyuncs\.com|mysql\+pymysql://[^"'\'' ]*:[^"'\''@ ]+@|(AES_KEY|JWT_SECRET|DB_PASSWORD|MYSQL_PASSWORD|DASHSCOPE_API_KEY|FEISHU_APP_SECRET)=[^ "'\''#]{6,}'

# 本地私有 denylist（每行一个正则；项目专属真实密码/主机/账号写这里，绝不入库）。
DENYLIST="$ROOT/scripts/.secret_denylist"
if [ -f "$DENYLIST" ]; then
  EXTRA="$(grep -vE '^[[:space:]]*(#|$)' "$DENYLIST" | paste -sd'|' - 2>/dev/null || true)"
  [ -n "$EXTRA" ] && PATTERN="${PATTERN}|${EXTRA}"
  echo "[safe_push]   已加载本地私有 denylist（未入库）"
fi

# 允许：占位符 / 环境变量取值 / 代码里的 URL 模板
ALLOW='PLEASE_REPLACE|<your|example|os\.environ|getenv|f"mysql\+pymysql://\{|\$\{|=\s*$|=""|='\'''\''|=xxx|xxxxxx|REPLACE|CHANGE_?ME|your_|占位|示例'

# 命中行再排除：纯注释行（#  //  *  --  ;）——文档/示例不算泄漏
HITS="$(printf '%s\n' "$FILES" | xargs -I{} grep -InHE "$PATTERN" {} 2>/dev/null \
        | grep -vE "$ALLOW" \
        | grep -vE ':[0-9]+:[[:space:]]*(#|//|\*|--|;)' || true)"
if [ -n "$HITS" ]; then
  echo "[safe_push] ✗ 检测到疑似敏感信息，已中止（未 push）："
  echo "$HITS"
  exit 1
fi
echo "[safe_push]   ✓ 未发现明文密钥/密码/真实连接串"

echo "[safe_push] 2) 复核被忽略文件确实未跟踪…"
for f in backend/.env config/runtime.local.env backend/config/runtime.local.env .pids/uvicorn.pid; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    echo "[safe_push] ✗ $f 被 Git 跟踪，已中止。请先： git rm --cached \"$f\""; exit 1
  fi
done
echo "[safe_push]   ✓ .env / runtime / pid 均未入库"

echo "[safe_push] 3) 提交并推送 origin/main…"
git add -A
if git diff --cached --quiet; then
  echo "[safe_push]   无改动可提交"; exit 0
fi
git commit -m "$MSG" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git push origin HEAD:main
echo "[safe_push] ✓ 已安全推送到 origin main"
