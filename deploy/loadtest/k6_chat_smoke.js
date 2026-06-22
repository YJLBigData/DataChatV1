// DataChatV1 容量压测 —— 容量方案 P0-8 / 第 8 节。
// 目标：证明单 worker + 线程池32 + 并发闸30 + DB池35 能扛住 30 人高峰，且过载时
//       优雅泄洪（429）而非 5xx/超时。
//
// 运行：
//   k6 run deploy/loadtest/k6_chat_smoke.js \
//     -e BASE_URL=http://127.0.0.1:8001 \
//     -e DATACHAT_USER=admin -e DATACHAT_PASS='你的密码'
//
// 多用户（更真实，规避 slowapi 每 token 30/min 限流掩盖容量信号）：
//   -e USERS_FILE=deploy/loadtest/users.json   // [{"username":"u1","password":"p1"}, ...]
//
// 重要：/api/chat 默认限流 30/min/用户。单账号高并发会先触发**限流**而非**容量闸**，
//       让 429 看起来偏多。要么用 ≥16 个账号的 USERS_FILE，要么压测窗口临时调高限流。
//
// 验收（见下方 thresholds + 文末）：5xx 错误率 < 1%；p95 < 25s；429 仅在突发期出现。

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8001';
const PROVIDER = __ENV.LLM_PROVIDER || '';            // 留空=服务端默认(线上 feihe)
const QUESTION = __ENV.QUESTION || '查询昨天各平台订单量，并按订单量倒序展示';

const server5xx = new Rate('server_5xx');             // 只统计 5xx，作为硬验收
const rejected429 = new Counter('rejected_429');      // 泄洪计数（突发期可接受）

const users = new SharedArray('users', function () {
  if (__ENV.USERS_FILE) {
    try { return JSON.parse(open(__ENV.USERS_FILE)); } catch (e) { /* fall through to single-user */ }
  }
  // 安全：脚本内**绝不**内置任何默认账号/密码。必须由运行者显式提供，缺失即退出。
  const username = __ENV.DATACHAT_USER;
  const password = __ENV.DATACHAT_PASS;
  if (!username || !password) {
    throw new Error(
      '缺少压测账号：请用 -e DATACHAT_USER=... -e DATACHAT_PASS=... 提供，' +
      '或 -e USERS_FILE=deploy/loadtest/users.json 提供多账号（脚本不内置默认密码）。',
    );
  }
  return [{ username, password }];
});

export const options = {
  scenarios: {
    // 稳态：1 req/s 持续 30 分钟（≈ 30 人高峰的均值上沿）
    steady: {
      executor: 'constant-arrival-rate',
      rate: 1, timeUnit: '1s', duration: '30m',
      preAllocatedVUs: 20, maxVUs: 60,
    },
    // 突发：5 分钟内压到 8 并发，验证并发闸泄洪而非雪崩
    burst: {
      executor: 'ramping-vus', startVUs: 0,
      stages: [
        { duration: '2m', target: 8 },
        { duration: '5m', target: 8 },
        { duration: '2m', target: 0 },
      ],
      startTime: '5m',
    },
  },
  thresholds: {
    server_5xx: ['rate<0.01'],                 // 5xx < 1%（硬指标）
    http_req_duration: ['p(95)<25000'],        // p95 < 25s（LLM 主导）
  },
};

function loginToken(u) {
  const res = http.post(`${BASE_URL}/api/login`,
    JSON.stringify({ username: u.username, password: u.password }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '15s' });
  if (res.status !== 200) {
    throw new Error(`login failed for ${u.username}: HTTP ${res.status} ${res.body}`);
  }
  return res.json('token');
}

// 每个 VU 用一个（按 VU 轮转的）账号，token 缓存在 VU 内，避免每请求都登录。
let _vuToken = null;
function vuToken() {
  if (_vuToken) return _vuToken;
  const u = users[(__VU - 1) % users.length];
  _vuToken = loginToken(u);
  return _vuToken;
}

export default function () {
  const token = vuToken();
  const body = { question: QUESTION };
  if (PROVIDER) body.llm_provider = PROVIDER;

  const res = http.post(`${BASE_URL}/api/chat`, JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    timeout: '120s',
  });

  server5xx.add(res.status >= 500);
  if (res.status === 429) rejected429.add(1);

  check(res, {
    'not 5xx': (r) => r.status < 500,
    '200 or 429': (r) => r.status === 200 || r.status === 429,
  });

  sleep(1);
}

// 文末验收口径（人工对照 /metrics）：
//   · server_5xx < 1%、p(95) < 25s
//   · chat_inflight 不长期顶满 30；chat_rejected_total 仅突发期增长
//   · db_pool_timeout_total == 0；llm_timeout_total 占比 < 1%
//   · redis used_memory 不逼近 768MB 上限（evicted_keys 短时可接受）
