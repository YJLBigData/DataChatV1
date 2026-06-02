// ===== DataChat SSO 集成 =====
// 1) 通过 <script src> 自动推断 DataCode 上下文 (默认 /datacode)，
//    保证 fetch("/api/...") 命中 Nginx 反代到 18082 而不是 DataChatV1。
// 2) 复用 DataChatV1 在 localStorage 里写下的同名 token：datachat.token。
// 3) 没 token 直接引导回 DataChat 登录入口；不在 DataCode 维护本地登录态。
const DATACHAT_TOKEN_KEY = "datachat.token";
const DATACODE_BASE = (() => {
  try {
    const src = document.currentScript ? document.currentScript.src : "";
    if (src) {
      const url = new URL(src, window.location.origin);
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length > 1 && parts[parts.length - 2] === "js" && parts[parts.length - 1] === "app.js") {
        const base = parts.slice(0, parts.length - 2).join("/");
        return base ? "/" + base : "";
      }
    }
  } catch (e) {
    /* fall through */
  }
  return window.location.pathname.startsWith("/datacode") ? "/datacode" : "";
})();
const DATACHAT_LOGIN_URL = "/web/#/chat";

function getDataChatToken() {
  try { return localStorage.getItem(DATACHAT_TOKEN_KEY) || ""; }
  catch (e) { return ""; }
}

function redirectToDataChatLogin() {
  window.location.href = DATACHAT_LOGIN_URL;
}

const state = {
  user: null,
  bootstrap: null,
  page: "code",
  login: { loading: false, error: "" },
  code: {
    requirements: [],
    selectedIds: new Set(),
    promptMarkdown: "请根据需求生成严谨、可执行的 Dataphin SQL，字段口径必须清晰，默认输出建表语句和 INSERT OVERWRITE 写入语句。",
    notes: "",
    result: null,
    loading: false,
    uploadLoading: false,
    error: ""
  },
  excelTool: {
    prompt: "请把这个 Excel 整理成字段清晰、表头标准、可直接交付的标准 Excel 文件。",
    loading: false,
    error: "",
    result: null
  },
  dataphin: {
    tab: "query",
    config: null,
    loading: false,
    error: "",
    result: null,
    querySql: "select * from firmus_dataphin_prd_ads.ads_dataphin_vdm_node_detail where ds = '${bizdate}' order by gmt_modified desc limit 5",
    projectName: "firmus_dataphin_prd_ads",
    keyword: "",
    tableName: "",
    nodeId: "",
    direction: "both",
    limit: 100,
    bizdate: ""
  },
  logs: { items: [], total: 0, detail: null, loading: false, error: "" },
  users: { items: [], loading: false, error: "", form: { username: "", displayName: "", role: "user", initialPassword: "" } },
  settings: { loading: false, saving: false, error: "", modelName: "", apiKey: "", apiKeyMasked: "", apiKeyConfigured: false }
};

const app = document.getElementById("app");

function icon(name, size = 16) {
  return `<i class="ico" data-lucide="${name}" style="width:${size}px;height:${size}px"></i>`;
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  // 所有 /api/* 都加 DataCode 上下文前缀，避免命中 DataChatV1 自己的 /api/*。
  const url = path.startsWith("/") ? (DATACODE_BASE + path) : path;
  const headers = { ...(options.headers || {}) };
  // SSO：把 DataChat token 透传到 DataCode 后端，由 Filter 调 /api/me 校验。
  const tk = getDataChatToken();
  if (tk) headers["Authorization"] = "Bearer " + tk;
  const init = { credentials: "include", ...options, headers };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...headers };
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(url, init);
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    if (res.status === 401) {
      // DataChat token 失效 → 让上层引导回 DataChat 登录页。
      state.user = null;
    }
    throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  }
  return data;
}

function markdownToHtml(markdown) {
  const safe = escapeHtml(markdown || "");
  const lines = safe.split("\n");
  let inCode = false;
  return lines.map((line) => {
    if (line.trim().startsWith("```")) {
      inCode = !inCode;
      return inCode ? "<pre>" : "</pre>";
    }
    if (inCode) return line;
    if (line.startsWith("### ")) return `<h3>${line.slice(4)}</h3>`;
    if (line.startsWith("## ")) return `<h2>${line.slice(3)}</h2>`;
    if (line.startsWith("# ")) return `<h1>${line.slice(2)}</h1>`;
    if (line.startsWith("- ")) return `<div>• ${line.slice(2)}</div>`;
    return line ? `<p>${line}</p>` : "<br>";
  }).join("");
}

function pageTitle() {
  if (state.page === "excel") return ["Excel 标准化", "上传乱格式 Excel，按提示词整理成标准文件"];
  if (state.page === "dataphin") return ["Dataphin 联动", "管理员可直接查数、查任务代码、查表级与任务血缘"];
  if (state.page === "logs") return ["运行日志", "追踪每次生成请求、模型调用与 SQL 校验结果"];
  if (state.page === "users") return ["用户管理", "管理员维护普通用户与权限"];
  if (state.page === "settings") return ["系统设置", "切换百炼模型 AK 和模型名称"];
  return ["代码生成", "上传需求文件，自动查询源表结构和样例数据后生成 Dataphin SQL"];
}

function render() {
  if (!state.user) {
    renderLogin();
    return;
  }
  const [title, subtitle] = pageTitle();
  const access = state.bootstrap?.ui_access || {};
  app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">DC</div>
          <div>
            <div class="brand-title">智能写代码</div>
            <div class="brand-subtitle">Dataphin Code</div>
          </div>
        </div>
        <nav class="nav-group">
          <div class="nav-group-title">开发工作台</div>
          ${navButton("code", "代码生成", "file-code-2", true)}
          ${navButton("excel", "Excel 标准化", "file-spreadsheet", true)}
          ${navButton("dataphin", "Dataphin 联动", "database", Boolean(access.show_dataphin_tab))}
        </nav>
        <nav class="nav-group">
          <div class="nav-group-title">运维治理</div>
          ${navButton("logs", "运行日志", "scroll-text", Boolean(access.show_logs_tab))}
          ${navButton("users", "用户管理", "users", Boolean(access.show_users_tab))}
          ${navButton("settings", "系统设置", "settings", Boolean(access.show_settings))}
        </nav>
        <div class="sidebar-user">
          <div class="sidebar-user-name">
            ${icon("user-circle", 18)}
            <span>${escapeHtml(state.user.display_name || state.user.username)}</span>
          </div>
          <button class="btn sm" data-action="logout" title="退出登录">${icon("log-out", 13)}退出</button>
        </div>
      </aside>
      <section class="main">
        <header class="topbar">
          <div class="title-block">
            <h1>${title}</h1>
            <p>${subtitle}</p>
          </div>
          <div class="topbar-actions">
            <span class="pill pill-blue">${icon("shield-check", 14)}${escapeHtml(state.user.role)}</span>
            <span class="pill pill-green">${icon("cpu", 14)}${escapeHtml(state.bootstrap?.providers?.[0]?.model || "qwen")}</span>
          </div>
        </header>
        <main class="page">${renderPage()}</main>
      </section>
    </div>`;
  bindShell();
  bindPage();
  refreshIcons();
}

function navButton(page, label, icon, visible) {
  if (!visible) return "";
  return `<button class="nav-link ${state.page === page ? "active" : ""}" data-page="${page}"><i class="ico" data-lucide="${icon}"></i><span>${label}</span></button>`;
}

function renderLogin() {
  const hasToken = !!getDataChatToken();
  app.innerHTML = `
    <div class="login-screen">
      <section class="login-card">
        <div class="login-brand">
          <div class="brand-logo">DC</div>
          <div>
            <h1 class="login-title">DataCode · 智能写代码</h1>
            <p class="login-meta">已接入 DataChat 统一登录（SSO）</p>
          </div>
        </div>
        <div class="panel-body stack">
          ${state.login.error ? `<div class="message error">${escapeHtml(state.login.error)}</div>` : ""}
          <div class="message">${icon("info", 14)}DataCode 不再单独管理账号密码，请使用 DataChat 账号登录。</div>
          <button id="login-submit" class="btn btn-primary" ${state.login.loading ? "disabled" : ""}>
            ${icon("log-in", 16)}${hasToken ? "已检测到 DataChat 登录态，进入 DataCode" : "前往 DataChat 登录"}
          </button>
        </div>
      </section>
    </div>`;
  document.getElementById("login-submit").addEventListener("click", login);
  refreshIcons();
}

function renderPage() {
  if (state.page === "excel") return renderExcelStandardizer();
  if (state.page === "dataphin") return renderDataphin();
  if (state.page === "logs") return renderLogs();
  if (state.page === "users") return renderUsers();
  if (state.page === "settings") return renderSettings();
  return renderCode();
}

function renderCode() {
  const validation = state.code.result?.validation;
  const statusClass = validation?.valid ? "success" : state.code.result ? "danger" : "";
  return `
    ${renderCodeOverview()}
    <div class="grid-two code-workbench">
      <section class="panel input-panel">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">输入区</h2>
            <p class="panel-subtitle">Excel 多 Sheet 会自动拆成独立需求</p>
          </div>
          <button class="button small" data-action="clear-code">清空</button>
        </div>
        <div class="panel-body stack">
          ${state.code.error ? `<div class="message error">${escapeHtml(state.code.error)}</div>` : ""}
          <div class="upload-box">
            <div class="row wrap">
              <input id="requirement-files" type="file" multiple accept=".xlsx,.xlsm,.xls,.csv" />
              <button class="button primary" data-action="upload" ${state.code.uploadLoading ? "disabled" : ""}>${icon("upload-cloud", 16)}${state.code.uploadLoading ? "解析中..." : "上传并解析"}</button>
            </div>
            <div class="requirement-list">${renderRequirementList()}</div>
          </div>
          <div class="field">
            <label>模型提示词（Markdown）</label>
            <div class="split-editor">
              <textarea id="promptMarkdown" class="textarea">${escapeHtml(state.code.promptMarkdown)}</textarea>
              <div class="markdown-box">${markdownToHtml(state.code.promptMarkdown)}</div>
            </div>
          </div>
          <div class="message">${icon("database", 14)}源表结构和样例数据会根据需求里的来源表自动查询 MaxCompute；查不到时会返回具体表名。</div>
          <div class="field">
            <label>备注</label>
            <textarea id="notes" class="textarea" placeholder="补充口径、目标表命名、特殊过滤条件">${escapeHtml(state.code.notes)}</textarea>
          </div>
          <div class="row wrap">
            <button class="button primary" data-action="generate" ${state.code.loading ? "disabled" : ""}>${icon("sparkles", 16)}${state.code.loading ? "生成中..." : "生成 Dataphin SQL"}</button>
            <button class="button" data-action="validate-local">${icon("check-circle-2", 16)}校验当前结果</button>
            ${state.code.result ? `<span class="status ${statusClass}">${validation?.valid ? "校验通过" : "校验未通过"}</span>` : ""}
          </div>
        </div>
      </section>
      <section class="panel result-panel">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">生成结果</h2>
            <p class="panel-subtitle">${state.code.result ? `Trace: ${escapeHtml(state.code.result.trace_id)}` : "等待生成"}</p>
          </div>
          <div class="row">
            <button class="button small" data-action="copy-sql" ${state.code.result?.sql ? "" : "disabled"}>${icon("copy", 14)}复制 SQL</button>
          </div>
        </div>
        ${renderValidation()}
        <pre class="code-output result-code-output">${escapeHtml(state.code.result?.sql || "-- 生成后的 Dataphin SQL 会显示在这里")}</pre>
      </section>
    </div>`;
}

function renderExcelStandardizer() {
  return `<section class="panel excel-page-panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">Excel 标准化</h2>
        <p class="panel-subtitle">上传一个 Excel，模型按提示词整理后生成标准 xlsx</p>
      </div>
    </div>
    <div class="panel-body standardize-page-body">
      ${state.excelTool.error ? `<div class="message error">${escapeHtml(state.excelTool.error)}</div>` : ""}
      <div class="standardize-layout">
        <div class="standardize-form stack">
          <div class="field">
            <label>待整理 Excel</label>
            <input id="standardize-file" class="input file-input" type="file" accept=".xlsx,.xlsm,.xls" />
          </div>
          <div class="field">
            <label>整理提示词</label>
            <textarea id="standardizePrompt" class="textarea standardize-textarea" placeholder="例如：按字段英文名、字段中文名、口径、来源表、来源字段整理">${escapeHtml(state.excelTool.prompt)}</textarea>
          </div>
          <div class="row wrap">
            <button class="button primary standardize-submit" data-action="standardize-excel" ${state.excelTool.loading ? "disabled" : ""}>${icon("file-output", 16)}${state.excelTool.loading ? "整理中..." : "生成标准 Excel"}</button>
          </div>
        </div>
        <div class="standardize-result">
          <div class="standardize-result-title">${icon(state.excelTool.result ? "check-circle-2" : "file-output", 18)}${state.excelTool.result ? "已生成标准 Excel" : "等待生成"}</div>
          <div class="standardize-result-desc">${state.excelTool.result ? "文件已生成，可以直接下载。" : "上传 Excel 并输入整理要求后，系统会生成一个标准 xlsx 文件。"}</div>
          ${state.excelTool.result ? `<div class="download-card">
            <div>
              <div class="download-title">${escapeHtml(state.excelTool.result.file_name || "标准化结果.xlsx")}</div>
              <div class="download-meta">Sheet 数：${escapeHtml(state.excelTool.result.sheet_count ?? "")}</div>
            </div>
            <a class="button small" href="${escapeHtml(state.excelTool.result.download_url)}" target="_blank">${icon("download", 14)}下载</a>
          </div>` : ""}
        </div>
      </div>
    </div>
  </section>`;
}

function renderCodeOverview() {
  const selectedCount = state.code.selectedIds.size;
  const requirementCount = state.code.requirements.length;
  const validation = state.code.result?.validation;
  const validationLabel = validation ? (validation.valid ? "校验通过" : "待修正") : "未生成";
  const validationTone = validation?.valid ? "green" : state.code.result ? "orange" : "blue";
  return `<div class="metric-grid workbench-metrics">
    <div class="metric-card kpi-tone-blue">
      <div class="metric-label">${icon("file-spreadsheet", 15)}需求文件</div>
      <div class="metric-value">${requirementCount}</div>
      <div class="metric-hint">已拆分 Sheet / CSV</div>
    </div>
    <div class="metric-card kpi-tone-green">
      <div class="metric-label">${icon("list-checks", 15)}选中需求</div>
      <div class="metric-value">${selectedCount}</div>
      <div class="metric-hint">参与本次生成</div>
    </div>
    <div class="metric-card kpi-tone-purple">
      <div class="metric-label">${icon("bot", 15)}模型</div>
      <div class="metric-value small">${escapeHtml(state.bootstrap?.providers?.[0]?.model || "qwen")}</div>
      <div class="metric-hint">Dataphin SQL 生成</div>
    </div>
    <div class="metric-card kpi-tone-${validationTone}">
      <div class="metric-label">${icon("shield-check", 15)}SQL 校验</div>
      <div class="metric-value small">${validationLabel}</div>
      <div class="metric-hint">语法与写入逻辑检查</div>
    </div>
  </div>`;
}

function renderRequirementList() {
  if (!state.code.requirements.length) {
    return `<div class="empty">尚未上传需求文件</div>`;
  }
  return state.code.requirements.map((item) => {
    const id = item.requirement_id;
    const title = item.table_cn_name || item.table_en_name || item.sheet_name || "需求";
    const checked = state.code.selectedIds.has(id) ? "checked" : "";
    return `<label class="requirement-item">
      <input type="checkbox" data-req-id="${escapeHtml(id)}" ${checked} />
      <span><strong>${escapeHtml(title)}</strong><span>${escapeHtml(item.source_file)} / ${escapeHtml(item.sheet_name)} / 字段 ${item.fields?.length || 0}</span></span>
    </label>`;
  }).join("");
}

function renderValidation() {
  const validation = state.code.result?.validation;
  if (!validation) return "";
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];
  if (!errors.length && !warnings.length) {
    return `<div class="panel-body"><span class="status success">无校验问题</span></div>`;
  }
  return `<div class="panel-body stack">
    ${errors.map((x) => `<div class="message error">${escapeHtml(x)}</div>`).join("")}
    ${warnings.map((x) => `<div class="message">${escapeHtml(x)}</div>`).join("")}
  </div>`;
}

function renderDataphin() {
  return `<section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">Dataphin / MaxCompute 查询</h2>
        <p class="panel-subtitle">只允许只读 SQL；普通用户不会显示此页面</p>
      </div>
      <div class="tabs">
        ${tab("query", "直接查询")}
        ${tab("tasks", "任务代码")}
        ${tab("table", "表血缘")}
        ${tab("task", "任务血缘")}
      </div>
    </div>
    <div class="panel-body stack">
      ${state.dataphin.error ? `<div class="message error">${escapeHtml(state.dataphin.error)}</div>` : ""}
      ${renderDataphinForm()}
      ${renderQueryResult(state.dataphin.result)}
    </div>
  </section>`;
}

function tab(id, label) {
  return `<button class="tab ${state.dataphin.tab === id ? "active" : ""}" data-dp-tab="${id}">${label}</button>`;
}

function renderDataphinForm() {
  const projectOptions = (state.dataphin.config?.projects || ["firmus_dataphin_prd_ads"]).map((project) => (
    `<option value="${escapeHtml(project)}" ${state.dataphin.projectName === project ? "selected" : ""}>${escapeHtml(project)}</option>`
  )).join("");
  const common = `
    <div class="row wrap">
      <div class="field" style="min-width:260px;flex:1">
        <label>项目空间</label>
        <select id="dp-project" class="select">${projectOptions}</select>
      </div>
      <div class="field" style="width:140px">
        <label>返回行数</label>
        <input id="dp-limit" class="input" type="number" min="1" max="1000" value="${escapeHtml(state.dataphin.limit)}" />
      </div>
      <div class="field" style="width:160px">
        <label>bizdate</label>
        <input id="dp-bizdate" class="input" placeholder="${escapeHtml(state.dataphin.config?.bizdate || "")}" value="${escapeHtml(state.dataphin.bizdate)}" />
      </div>
    </div>`;
  if (state.dataphin.tab === "query") {
    return `${common}
      <div class="field">
        <label>只读 SQL</label>
        <textarea id="dp-query-sql" class="textarea mono" style="min-height:150px">${escapeHtml(state.dataphin.querySql)}</textarea>
      </div>
      <button class="button primary" data-action="dp-run" ${state.dataphin.loading ? "disabled" : ""}>${state.dataphin.loading ? "查询中..." : "执行查询"}</button>`;
  }
  if (state.dataphin.tab === "tasks") {
    return `${common}
      <div class="row wrap">
        <div class="field" style="min-width:280px;flex:1">
          <label>关键词（表名 / 节点名 / SQL 内容）</label>
          <input id="dp-keyword" class="input" value="${escapeHtml(state.dataphin.keyword)}" />
        </div>
      </div>
      <button class="button primary" data-action="dp-run" ${state.dataphin.loading ? "disabled" : ""}>查询任务</button>`;
  }
  if (state.dataphin.tab === "table") {
    return `${common}
      <div class="row wrap">
        <div class="field" style="min-width:280px;flex:1">
          <label>表名</label>
          <input id="dp-table" class="input" placeholder="ads_xxx_df" value="${escapeHtml(state.dataphin.tableName)}" />
        </div>
        ${directionSelect()}
      </div>
      <button class="button primary" data-action="dp-run" ${state.dataphin.loading ? "disabled" : ""}>查询表血缘</button>`;
  }
  return `${common}
    <div class="row wrap">
      <div class="field" style="min-width:280px;flex:1">
        <label>节点 ID</label>
        <input id="dp-node" class="input" placeholder="n_7853311932260614144" value="${escapeHtml(state.dataphin.nodeId)}" />
      </div>
      ${directionSelect()}
    </div>
    <button class="button primary" data-action="dp-run" ${state.dataphin.loading ? "disabled" : ""}>查询任务血缘</button>`;
}

function directionSelect() {
  return `<div class="field" style="width:160px">
    <label>方向</label>
    <select id="dp-direction" class="select">
      <option value="both" ${state.dataphin.direction === "both" ? "selected" : ""}>上下游</option>
      <option value="upstream" ${state.dataphin.direction === "upstream" ? "selected" : ""}>上游</option>
      <option value="downstream" ${state.dataphin.direction === "downstream" ? "selected" : ""}>下游</option>
    </select>
  </div>`;
}

function renderQueryResult(result) {
  if (!result) return `<div class="empty">暂无查询结果</div>`;
  const columns = result.columns || [];
  const rows = result.rows || [];
  return `<div class="stack">
    <div class="row wrap">
      <span class="status success">返回 ${rows.length} 行</span>
      <span class="status">${escapeHtml(result.project || "")}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>${columns.map((c) => `<th>${escapeHtml(c.name)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows.map((row) => `<tr>${row.map((cell) => `<td title="${escapeHtml(cell ?? "")}">${escapeHtml(cell ?? "")}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
    <pre class="markdown-box">${escapeHtml(result.executed_sql || "")}</pre>
  </div>`;
}

function renderLogs() {
  return `<section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">生成日志</h2>
        <p class="panel-subtitle">共 ${state.logs.total || 0} 条</p>
      </div>
      <button class="button small" data-action="reload-logs">刷新</button>
    </div>
    <div class="panel-body stack">
      ${state.logs.error ? `<div class="message error">${escapeHtml(state.logs.error)}</div>` : ""}
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>用户</th><th>需求</th><th>状态</th><th>模型</th><th>耗时</th><th>操作</th></tr></thead>
          <tbody>${state.logs.items.map((item) => `<tr>
            <td>${escapeHtml(item.created_at)}</td>
            <td>${escapeHtml(item.user_id)}</td>
            <td>${escapeHtml(item.metric || item.raw_input || "")}</td>
            <td>${escapeHtml(item.status)}</td>
            <td>${escapeHtml(item.model_name || "")}</td>
            <td>${escapeHtml(item.elapsed_ms ?? "")}</td>
            <td><button class="button small" data-log="${escapeHtml(item.trace_id)}">查看</button></td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
      ${state.logs.detail ? `<pre class="code-output">${escapeHtml(JSON.stringify(state.logs.detail, null, 2))}</pre>` : ""}
    </div>
  </section>`;
}

function renderUsers() {
  return `<section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">用户管理</h2>
        <p class="panel-subtitle">新增用户默认需要修改密码</p>
      </div>
      <button class="button small" data-action="reload-users">刷新</button>
    </div>
    <div class="panel-body stack">
      ${state.users.error ? `<div class="message error">${escapeHtml(state.users.error)}</div>` : ""}
      <div class="row wrap">
        <input id="new-username" class="input" style="width:180px" placeholder="用户名" value="${escapeHtml(state.users.form.username)}" />
        <input id="new-display" class="input" style="width:180px" placeholder="显示名称" value="${escapeHtml(state.users.form.displayName)}" />
        <select id="new-role" class="select" style="width:150px">
          <option value="user" ${state.users.form.role === "user" ? "selected" : ""}>普通用户</option>
          <option value="admin" ${state.users.form.role === "admin" ? "selected" : ""}>管理员</option>
          <option value="super_admin" ${state.users.form.role === "super_admin" ? "selected" : ""}>超级管理员</option>
        </select>
        <input id="new-password" class="input" style="width:180px" placeholder="初始密码，可空" value="${escapeHtml(state.users.form.initialPassword)}" />
        <button class="button primary" data-action="create-user">新增</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>用户名</th><th>显示名称</th><th>角色</th><th>创建时间</th><th>最后登录</th><th>操作</th></tr></thead>
          <tbody>${state.users.items.map((user) => `<tr>
            <td>${escapeHtml(user.user_id)}</td>
            <td>${escapeHtml(user.username)}</td>
            <td>${escapeHtml(user.display_name)}</td>
            <td>${escapeHtml(user.role)}</td>
            <td>${escapeHtml(user.created_at)}</td>
            <td>${escapeHtml(user.last_login || "")}</td>
            <td class="row">
              <button class="button small" data-reset="${escapeHtml(user.user_id)}">重置</button>
              <button class="button small danger" data-delete="${escapeHtml(user.user_id)}">禁用</button>
            </td>
          </tr>`).join("")}</tbody>
        </table>
      </div>
    </div>
  </section>`;
}

function renderSettings() {
  return `<section class="panel">
    <div class="panel-header">
      <div>
        <h2 class="panel-title">模型设置</h2>
        <p class="panel-subtitle">保存后立即影响后续 SQL 生成和 Excel 标准化</p>
      </div>
      <button class="button small" data-action="reload-settings">刷新</button>
    </div>
    <div class="panel-body stack settings-form">
      ${state.settings.error ? `<div class="message error">${escapeHtml(state.settings.error)}</div>` : ""}
      <div class="settings-grid">
        <div class="field">
          <label>百炼模型名称</label>
          <input id="settings-model-name" class="input" placeholder="qwen3.6-max-preview" value="${escapeHtml(state.settings.modelName)}" />
        </div>
        <div class="field">
          <label>百炼模型 AK</label>
          <input id="settings-api-key" class="input" type="password" placeholder="${state.settings.apiKeyConfigured ? `当前：${escapeHtml(state.settings.apiKeyMasked)}，留空不修改` : "请输入 sk-..."}" value="${escapeHtml(state.settings.apiKey)}" />
        </div>
      </div>
      <div class="row wrap">
        <button class="button primary" data-action="save-settings" ${state.settings.saving ? "disabled" : ""}>${icon("save", 16)}${state.settings.saving ? "保存中..." : "保存设置"}</button>
        <span class="status">${escapeHtml(state.bootstrap?.providers?.[0]?.model || "")}</span>
      </div>
    </div>
  </section>`;
}

function bindShell() {
  document.querySelectorAll("[data-page]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.page = btn.dataset.page;
      render();
      await loadPageData();
    });
  });
  document.querySelector("[data-action='logout']")?.addEventListener("click", logout);
}

function bindPage() {
  if (state.page === "code") bindCode();
  if (state.page === "excel") bindExcel();
  if (state.page === "dataphin") bindDataphin();
  if (state.page === "logs") bindLogs();
  if (state.page === "users") bindUsers();
  if (state.page === "settings") bindSettings();
}

function bindCode() {
  ["promptMarkdown", "notes"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", (e) => {
      state.code[id] = e.target.value;
    });
  });
  document.querySelectorAll("[data-req-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", (e) => {
      const id = e.target.dataset.reqId;
      if (e.target.checked) state.code.selectedIds.add(id);
      else state.code.selectedIds.delete(id);
    });
  });
  document.querySelector("[data-action='upload']")?.addEventListener("click", uploadRequirements);
  document.querySelector("[data-action='generate']")?.addEventListener("click", generateSql);
  document.querySelector("[data-action='copy-sql']")?.addEventListener("click", () => navigator.clipboard.writeText(state.code.result?.sql || ""));
  document.querySelector("[data-action='clear-code']")?.addEventListener("click", () => {
    state.code.result = null;
    state.code.error = "";
    render();
  });
  document.querySelector("[data-action='validate-local']")?.addEventListener("click", validateCurrentSql);
}

function bindExcel() {
  document.getElementById("standardizePrompt")?.addEventListener("input", (e) => state.excelTool.prompt = e.target.value);
  document.querySelector("[data-action='standardize-excel']")?.addEventListener("click", standardizeExcel);
}

function bindDataphin() {
  document.querySelectorAll("[data-dp-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.dataphin.tab = btn.dataset.dpTab;
      state.dataphin.result = null;
      render();
    });
  });
  ["dp-project", "dp-limit", "dp-bizdate", "dp-query-sql", "dp-keyword", "dp-table", "dp-node", "dp-direction"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", syncDataphinForm);
    document.getElementById(id)?.addEventListener("change", syncDataphinForm);
  });
  document.querySelector("[data-action='dp-run']")?.addEventListener("click", runDataphin);
}

function bindLogs() {
  document.querySelector("[data-action='reload-logs']")?.addEventListener("click", loadLogs);
  document.querySelectorAll("[data-log]").forEach((btn) => btn.addEventListener("click", () => loadLogDetail(btn.dataset.log)));
}

function bindUsers() {
  ["new-username", "new-display", "new-role", "new-password"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", syncUserForm);
    document.getElementById(id)?.addEventListener("change", syncUserForm);
  });
  document.querySelector("[data-action='reload-users']")?.addEventListener("click", loadUsers);
  document.querySelector("[data-action='create-user']")?.addEventListener("click", createUser);
  document.querySelectorAll("[data-reset]").forEach((btn) => btn.addEventListener("click", () => resetPassword(btn.dataset.reset)));
  document.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteUser(btn.dataset.delete)));
}

function bindSettings() {
  document.getElementById("settings-model-name")?.addEventListener("input", (e) => state.settings.modelName = e.target.value);
  document.getElementById("settings-api-key")?.addEventListener("input", (e) => state.settings.apiKey = e.target.value);
  document.querySelector("[data-action='reload-settings']")?.addEventListener("click", loadSettings);
  document.querySelector("[data-action='save-settings']")?.addEventListener("click", saveSettings);
}

async function login() {
  state.login.loading = true;
  state.login.error = "";
  render();
  const token = getDataChatToken();
  if (!token) {
    // 没有 DataChat token，直接送回 DataChat 登录页。
    redirectToDataChatLogin();
    return;
  }
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.bootstrap = await api("/api/runtime/bootstrap");
    render();
  } catch (error) {
    state.login.error = (error && error.message) || "DataChat 鉴权失败，请重新登录";
    state.user = null;
    render();
  } finally {
    state.login.loading = false;
  }
}

async function logout() {
  // SSO 模式下不在本地“注销 DataChat”，只清掉 DataCode 当前页面会话并送回 DataChat。
  state.user = null;
  state.bootstrap = null;
  state.page = "code";
  redirectToDataChatLogin();
}

async function uploadRequirements() {
  const input = document.getElementById("requirement-files");
  if (!input.files.length) {
    state.code.error = "请选择 Excel 或 CSV 文件";
    render();
    return;
  }
  state.code.uploadLoading = true;
  state.code.error = "";
  render();
  try {
    const form = new FormData();
    Array.from(input.files).forEach((file) => form.append("files", file));
    const payload = await api("/api/code/upload-requirements", { method: "POST", body: form });
    state.code.requirements = payload.requirements || [];
    state.code.selectedIds = new Set(state.code.requirements.map((item) => item.requirement_id));
  } catch (error) {
    state.code.error = error.message;
  } finally {
    state.code.uploadLoading = false;
    render();
  }
}

async function generateSql() {
  syncCodeFields();
  const selected = state.code.requirements.filter((item) => state.code.selectedIds.has(item.requirement_id));
  state.code.loading = true;
  state.code.error = "";
  render();
  try {
    state.code.result = await api("/api/code/generate", {
      method: "POST",
      body: {
        prompt_markdown: state.code.promptMarkdown,
        notes: state.code.notes,
        requirements: selected
      }
    });
  } catch (error) {
    state.code.error = error.message;
  } finally {
    state.code.loading = false;
    render();
  }
}

async function validateCurrentSql() {
  if (!state.code.result?.sql) return;
  try {
    const validation = await api("/api/code/validate", { method: "POST", body: { sql: state.code.result.sql } });
    state.code.result.validation = validation;
    render();
  } catch (error) {
    state.code.error = error.message;
    render();
  }
}

function syncCodeFields() {
  ["promptMarkdown", "notes"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) state.code[id] = el.value;
  });
}

function syncExcelFields() {
  const standardizePrompt = document.getElementById("standardizePrompt");
  if (standardizePrompt) state.excelTool.prompt = standardizePrompt.value;
}

async function standardizeExcel() {
  syncExcelFields();
  const input = document.getElementById("standardize-file");
  if (!input?.files?.length) {
    state.excelTool.error = "请选择需要标准化的 Excel 文件";
    render();
    return;
  }
  state.excelTool.loading = true;
  state.excelTool.error = "";
  state.excelTool.result = null;
  render();
  try {
    const form = new FormData();
    form.append("file", input.files[0]);
    form.append("prompt", state.excelTool.prompt || "");
    state.excelTool.result = await api("/api/code/standardize-excel", { method: "POST", body: form });
  } catch (error) {
    state.excelTool.error = error.message;
  } finally {
    state.excelTool.loading = false;
    render();
  }
}

function syncDataphinForm() {
  state.dataphin.projectName = document.getElementById("dp-project")?.value || state.dataphin.projectName;
  state.dataphin.limit = Number(document.getElementById("dp-limit")?.value || state.dataphin.limit);
  state.dataphin.bizdate = document.getElementById("dp-bizdate")?.value || "";
  state.dataphin.querySql = document.getElementById("dp-query-sql")?.value || state.dataphin.querySql;
  state.dataphin.keyword = document.getElementById("dp-keyword")?.value || "";
  state.dataphin.tableName = document.getElementById("dp-table")?.value || "";
  state.dataphin.nodeId = document.getElementById("dp-node")?.value || "";
  state.dataphin.direction = document.getElementById("dp-direction")?.value || state.dataphin.direction;
}

async function runDataphin() {
  syncDataphinForm();
  state.dataphin.loading = true;
  state.dataphin.error = "";
  state.dataphin.result = null;
  render();
  try {
    const common = { project_name: state.dataphin.projectName, limit: state.dataphin.limit, bizdate: state.dataphin.bizdate || null };
    if (state.dataphin.tab === "query") {
      state.dataphin.result = await api("/api/dataphin/query", { method: "POST", body: { ...common, sql: state.dataphin.querySql } });
    } else if (state.dataphin.tab === "tasks") {
      state.dataphin.result = await api("/api/dataphin/tasks", { method: "POST", body: { ...common, keyword: state.dataphin.keyword } });
    } else if (state.dataphin.tab === "table") {
      state.dataphin.result = await api("/api/dataphin/table-lineage", { method: "POST", body: { ...common, table_name: state.dataphin.tableName, direction: state.dataphin.direction } });
    } else {
      state.dataphin.result = await api("/api/dataphin/task-lineage", { method: "POST", body: { ...common, node_id: state.dataphin.nodeId, direction: state.dataphin.direction } });
    }
  } catch (error) {
    state.dataphin.error = error.message;
  } finally {
    state.dataphin.loading = false;
    render();
  }
}

async function loadLogs() {
  state.logs.loading = true;
  state.logs.error = "";
  render();
  try {
    const payload = await api("/api/logs?limit=80&offset=0");
    state.logs.items = payload.items || [];
    state.logs.total = payload.total || 0;
  } catch (error) {
    state.logs.error = error.message;
  } finally {
    state.logs.loading = false;
    render();
  }
}

async function loadLogDetail(traceId) {
  try {
    const detail = await api(`/api/logs/${encodeURIComponent(traceId)}`);
    const llm = await api(`/api/logs/${encodeURIComponent(traceId)}/llm`);
    state.logs.detail = { ...detail.item, invocations: llm.items };
    render();
  } catch (error) {
    state.logs.error = error.message;
    render();
  }
}

function syncUserForm() {
  state.users.form.username = document.getElementById("new-username")?.value || "";
  state.users.form.displayName = document.getElementById("new-display")?.value || "";
  state.users.form.role = document.getElementById("new-role")?.value || "user";
  state.users.form.initialPassword = document.getElementById("new-password")?.value || "";
}

async function loadUsers() {
  state.users.loading = true;
  state.users.error = "";
  render();
  try {
    const payload = await api("/api/users");
    state.users.items = payload.users || [];
  } catch (error) {
    state.users.error = error.message;
  } finally {
    state.users.loading = false;
    render();
  }
}

async function createUser() {
  syncUserForm();
  try {
    const payload = await api("/api/users", { method: "POST", body: {
      username: state.users.form.username,
      display_name: state.users.form.displayName,
      role: state.users.form.role,
      initial_password: state.users.form.initialPassword || null
    }});
    alert(`用户已创建，初始密码：${payload.initial_password}`);
    state.users.form = { username: "", displayName: "", role: "user", initialPassword: "" };
    await loadUsers();
  } catch (error) {
    state.users.error = error.message;
    render();
  }
}

async function resetPassword(userId) {
  if (!confirm("确认重置该用户密码为 123456？")) return;
  try {
    const payload = await api(`/api/users/${encodeURIComponent(userId)}/reset-password`, { method: "POST" });
    alert(`新密码：${payload.new_password}`);
  } catch (error) {
    state.users.error = error.message;
    render();
  }
}

async function deleteUser(userId) {
  if (!confirm("确认禁用该用户？")) return;
  try {
    await api(`/api/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
    await loadUsers();
  } catch (error) {
    state.users.error = error.message;
    render();
  }
}

async function loadSettings() {
  state.settings.loading = true;
  state.settings.error = "";
  render();
  try {
    const payload = await api("/api/settings/model");
    state.settings.modelName = payload.model_name || "";
    state.settings.apiKey = "";
    state.settings.apiKeyMasked = payload.api_key_masked || "";
    state.settings.apiKeyConfigured = Boolean(payload.api_key_configured);
  } catch (error) {
    state.settings.error = error.message;
  } finally {
    state.settings.loading = false;
    render();
  }
}

async function saveSettings() {
  state.settings.saving = true;
  state.settings.error = "";
  render();
  try {
    const payload = await api("/api/settings/model", {
      method: "POST",
      body: {
        model_name: state.settings.modelName,
        api_key: state.settings.apiKey || ""
      }
    });
    state.settings.modelName = payload.model_name || "";
    state.settings.apiKey = "";
    state.settings.apiKeyMasked = payload.api_key_masked || "";
    state.settings.apiKeyConfigured = Boolean(payload.api_key_configured);
    state.bootstrap = await api("/api/runtime/bootstrap");
  } catch (error) {
    state.settings.error = error.message;
  } finally {
    state.settings.saving = false;
    render();
  }
}

async function loadPageData() {
  if (state.page === "dataphin" && !state.dataphin.config) {
    try {
      state.dataphin.config = await api("/api/dataphin/config");
      state.dataphin.projectName = state.dataphin.config.default_project || state.dataphin.projectName;
      render();
    } catch (error) {
      state.dataphin.error = error.message;
      render();
    }
  }
  if (state.page === "logs" && !state.logs.items.length) await loadLogs();
  if (state.page === "users" && !state.users.items.length) await loadUsers();
  if (state.page === "settings" && !state.settings.modelName) await loadSettings();
}

(async function boot() {
  const token = getDataChatToken();
  if (!token) {
    state.user = null;
    render();
    return;
  }
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.bootstrap = await api("/api/runtime/bootstrap");
  } catch (e) {
    state.user = null;
  }
  render();
})();
