#!/usr/bin/env bash
# DataChatV1 产物清理 —— 容量方案 P0-9：防 Excel 导出 / 报告 / 图表长期堆积占满磁盘。
# 安装到 crontab（每天 03:00）：
#   0 3 * * * /opt/datachatv1/deploy/scripts/cleanup_artifacts.sh >> /var/log/datachatv1_cleanup.log 2>&1
set -euo pipefail

# 部署根（按实际调整）。仓库目录结构：<root>/backend/reports/{exports,generated,chart_snapshots}、<root>/backend/logs/charts_v1
ROOT="${DATACHATV1_ROOT:-/opt/datachatv1}"
BACKEND="$ROOT/backend"

EXPORT_RETENTION_DAYS="${EXPORT_RETENTION_DAYS:-7}"
REPORT_RETENTION_DAYS="${REPORT_RETENTION_DAYS:-7}"
CHART_RETENTION_DAYS="${CHART_RETENTION_DAYS:-7}"

prune() {  # <dir> <days>
  local dir="$1" days="$2"
  [ -d "$dir" ] || return 0
  find "$dir" -type f -mtime "+$days" -delete 2>/dev/null || true
  echo "  pruned >$days d: $dir"
}

echo "== $(date '+%F %T') DataChatV1 产物清理 =="
# 导出 Excel（物理文件名 exp_<jobid>.xlsx；DATACHAT_EXPORT_DIR 默认就是这里）
prune "${DATACHAT_EXPORT_DIR:-$BACKEND/reports/exports}" "$EXPORT_RETENTION_DAYS"
# 生成的报告（DOCX/HTML）与图表快照
prune "$BACKEND/reports/generated"       "$REPORT_RETENTION_DAYS"
prune "$BACKEND/reports/chart_snapshots" "$CHART_RETENTION_DAYS"
prune "$BACKEND/logs/charts_v1"          "$CHART_RETENTION_DAYS"

# 磁盘水位告警（>=80% 警告，>=90% 危险）
USE=$(df -P "$BACKEND" | awk 'NR==2{gsub(/%/,"",$5); print $5}')
echo "  disk usage: ${USE}%"
if [ "${USE:-0}" -ge 90 ]; then
  echo "  [DANGER] 磁盘使用率 ${USE}% >= 90%，请立即清理或扩容！"
elif [ "${USE:-0}" -ge 80 ]; then
  echo "  [WARN] 磁盘使用率 ${USE}% >= 80%。"
fi
