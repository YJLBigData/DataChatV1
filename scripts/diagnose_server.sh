#!/usr/bin/env bash
# DataChatV1 服务端"为什么前端报错"定位器。
#
# 背景：redeploy.sh 的自检只验证 /api/chat 普通问数 + 路由是否挂载（401），
# 但它**不会**验证：① 浏览器拿到的前端是不是最新构建；② 智能小Q(SmartQ)是否真的就绪；
# ③ 流式问数 /api/chat/stream（浏览器走的就是它）能不能端到端跑通。
# 这三项正是"自检全绿但页面照样报错"的盲区，本脚本逐一打实。
#
# 用法（在服务器上，服务已起）：
#   DATACHAT_ADMIN_PASS='你的管理员密码' bash scripts/diagnose_server.sh
# 可选覆盖：
#   BASE_URL（默认 http://127.0.0.1:8001）  WEB_DIR（默认 /opt/datachatv1/backend/web）
#   DATACHAT_ADMIN_USER（默认 admin@feihe.com）
#
# 只读：不改任何配置/数据，只发只读请求并打印结论。
set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
WEB_DIR="${WEB_DIR:-/opt/datachatv1/backend/web}"
ADMIN_USER="${DATACHAT_ADMIN_USER:-admin@feihe.com}"
ADMIN_PASS="${DATACHAT_ADMIN_PASS:-}"
PY="$(command -v python3 || command -v python || true)"

c_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()   { printf '\033[32m%s\033[0m\n' "$*"; }
c_yel()   { printf '\033[33m%s\033[0m\n' "$*"; }
c_cyn()   { printf '\033[36m%s\033[0m\n' "$*"; }
hr()      { printf '%s\n' "────────────────────────────────────────────────────"; }

jget() { # jget <json> <python表达式 d=dict>  —— 取不到回空串，绝不抛错
  [ -n "$PY" ] || { echo ""; return; }
  printf '%s' "$1" | "$PY" -c "import sys,json
try:
    d=json.load(sys.stdin)
    print(eval(sys.argv[1]))
except Exception:
    print('')" "$2" 2>/dev/null
}

FAIL=0

c_cyn "DataChatV1 服务端诊断  base=$BASE_URL"
hr

# ── 1. served 前端是否最新 ───────────────────────────────────────────────
c_cyn "① 浏览器拿到的前端是否为最新构建"
served_html="$(curl -s --max-time 8 "$BASE_URL/web/" || true)"
served_js="$(printf '%s' "$served_html" | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1)"
if [ -z "$served_js" ]; then
  c_red "  ✗ 取不到 /web/ 首页（服务没起？反代没通？）"; FAIL=1
else
  echo "  · /web/ 引用的主包：$served_js"
  disk_js="$(ls -1 "$WEB_DIR"/static/smartq/index-*.js 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
  echo "  · 磁盘上的主包：${disk_js:-（找不到 $WEB_DIR）}"
  if printf '%s' "$disk_js" | grep -q "$served_js"; then
    label="$(grep -o '数据范围' "$WEB_DIR"/static/smartq/"$served_js" 2>/dev/null | wc -l | tr -d ' ')"
    old_label="$(grep -o '数据反问' "$WEB_DIR"/static/smartq/"$served_js" 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${old_label:-0}" != "0" ]; then
      c_red "  ✗ 磁盘前端是旧版（含『数据反问』）—— git 没拉到最新前端构建"; FAIL=1
    else
      c_grn "  ✓ 磁盘前端为新版（『数据范围』×$label）。若页面仍显示旧版=浏览器缓存，请 Ctrl+Shift+R 强刷"
    fi
  else
    c_red "  ✗ /web/ 引用的包与磁盘不一致 —— 服务进程加载的是旧目录，建议重启 datachatv1"; FAIL=1
  fi
fi
hr

# ── 2. 管理员登录 ────────────────────────────────────────────────────────
c_cyn "② 管理员登录取 token"
if [ -z "$ADMIN_PASS" ]; then
  c_yel "  · 未提供 DATACHAT_ADMIN_PASS，跳过②③（仅做了前端检查）。"
  c_yel "    重跑：DATACHAT_ADMIN_PASS='密码' bash scripts/diagnose_server.sh"
  hr; exit $FAIL
fi
login_resp="$(curl -s --max-time 10 -X POST "$BASE_URL/api/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" || true)"
TOKEN="$(jget "$login_resp" 'd.get("token","")')"
if [ -z "$TOKEN" ]; then
  c_red "  ✗ 登录失败：$(printf '%s' "$login_resp" | head -c 200)"; FAIL=1; hr; exit $FAIL
fi
c_grn "  ✓ 登录成功（$ADMIN_USER）"
hr

# ── 3. SmartQ 是否真的就绪 + 能否列出数据集 ──────────────────────────────
c_cyn "③ 智能小Q(SmartQ) 就绪与数据集（解释『智无可用数据集』）"
diag="$(curl -s --max-time 10 "$BASE_URL/api/smartq/diagnostics" -H "Authorization: Bearer $TOKEN" || true)"
en="$(jget "$diag" 'd.get("enabled")')"
cfg="$(jget "$diag" 'd.get("configured")')"
ready="$(jget "$diag" 'd.get("ready")')"
dom="$(jget "$diag" 'd.get("server_domain")')"
echo "  · enabled=$en  configured=$cfg  ready=$ready  domain=$dom"
if [ "$ready" != "True" ]; then
  c_red "  ✗ SmartQ 未就绪 → 这就是『智无可用数据集』的原因。"
  if [ "$en" != "True" ]; then
    c_yel "    原因：SMARTQ_ENABLED 未开。请在 /opt/datachatv1/.env 设 SMARTQ_ENABLED=1"
  fi
  if [ "$cfg" != "True" ]; then
    c_yel "    原因：密钥/域名不全。请在 /opt/datachatv1/.env 补 SMARTQ_API_KEY / SMARTQ_API_SECRET / SMARTQ_SERVER_DOMAIN"
  fi
  c_yel "    改完执行：systemctl restart datachatv1"
  FAIL=1
else
  ds="$(curl -s --max-time 20 "$BASE_URL/api/smartq/datasets" -H "Authorization: Bearer $TOKEN" || true)"
  ok="$(jget "$ds" 'd.get("ok")')"
  n="$(jget "$ds" 'len(d.get("items") or [])')"
  if [ "$ok" = "True" ] && [ "${n:-0}" != "0" ]; then
    c_grn "  ✓ SmartQ 就绪且可列出 $n 个数据集（网关连通正常）"
  else
    c_red "  ✗ SmartQ 就绪但列数据集失败/为空：$(printf '%s' "$ds" | head -c 200)"
    c_yel "    多半是服务器到 $dom 不通（防火墙/无代理/内网解析），或该账号未授权数据集。"
    FAIL=1
  fi
fi
hr

# ── 4. 流式问数端到端（浏览器真正走的路径）──────────────────────────────
c_cyn "④ 流式问数 /api/chat/stream（普通飞鹤库 NL2SQL，cube_ids=[]）"
stream="$(curl -s -N --max-time 100 -X POST "$BASE_URL/api/chat/stream" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"question":"本月各大区销售额排名","smartq_cube_ids":[]}' 2>/dev/null || true)"
http_only="$(printf '%s' "$stream" | tr -d '\r')"
if printf '%s' "$http_only" | grep -q '^event: *done'; then
  c_grn "  ✓ 流式问数端到端成功（done 事件已返回）"
elif printf '%s' "$http_only" | grep -q '^event: *error'; then
  errline="$(printf '%s' "$http_only" | grep -A1 '^event: *error' | grep '^data:' | head -1 | sed 's/^data://')"
  c_red "  ✗ 后端返回 error 事件：$errline"
  c_yel "    这是后端业务/模型/SQL 的真实失败信息（按它定位，而不是前端那句笼统提示）。"
  FAIL=1
else
  c_red "  ✗ 流没有正常结束（既无 done 也无 error）。原始片段："
  printf '%s\n' "$http_only" | head -c 400
  echo
  c_yel "    若整段为空/HTML：多半是 nginx 把 SSE 缓冲断了，或 401/限流。可直连 8001 再试本脚本（BASE_URL=http://127.0.0.1:8001）。"
  FAIL=1
fi
hr

if [ "$FAIL" = "0" ]; then
  c_grn "结论：服务端三项盲区全部通过。页面仍报错=浏览器缓存旧前端，请 Ctrl+Shift+R 强刷。"
else
  c_red "结论：上面标 ✗ 的就是页面报错的真因，按其黄色提示处理。"
fi
exit $FAIL
