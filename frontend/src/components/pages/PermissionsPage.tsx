import { useEffect, useState } from "react";

import { api } from "../../api";
import type { PermissionsAllItem, SemanticOverview } from "../../types";

/**
 * 数据权限 — 三层：行级（维度白名单） + 表级（白名单表） + 字段级（每表白名单列）。
 * 编辑用 Drawer，三个 Tab 切换。
 *
 * UX 原则：
 *   · 用 tag-chips 表示 "已选中"，可点叉删除
 *   · 表权限多选：列出语义层所有表
 *   · 字段权限：选了表才出现该表的字段多选
 *   · 行级：每个维度独立的文本输入框（逗号分隔）
 */
export function PermissionsPage() {
  const [items, setItems] = useState<PermissionsAllItem[]>([]);
  const [semantic, setSemantic] = useState<SemanticOverview | null>(null);
  const [tableColumns, setTableColumns] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<PermissionsAllItem | null>(null);
  const [tab, setTab] = useState<"row" | "table" | "column">("row");
  const [draftRow, setDraftRow] = useState<Record<string, string>>({});
  const [draftTables, setDraftTables] = useState<string[]>([]);
  const [draftColumns, setDraftColumns] = useState<Record<string, string[]>>({});
  const [draftDeny, setDraftDeny] = useState(false);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [perms, sem] = await Promise.all([api.listPermissions(), api.semanticOverview()]);
      setItems(perms.items || []);
      setSemantic(sem);
      // 抓表的字段定义（从 semanticEntities tables）
      try {
        const t = await api.semanticEntities("tables");
        const cols: Record<string, string[]> = {};
        for (const [name, body] of Object.entries(t.items || {})) {
          const dims: string[] = (body as any)?.primary_dimensions || [];
          const meas: string[] = (body as any)?.measures || [];
          cols[name] = [...dims, ...meas];
        }
        setTableColumns(cols);
      } catch {
        /* ignore */
      }
    } finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  function open(it: PermissionsAllItem) {
    setActive(it);
    const dr: Record<string, string> = {};
    for (const [k, v] of Object.entries(it.row_rules || {})) dr[k] = (v || []).join(", ");
    setDraftRow(dr);
    setDraftTables(it.allowed_tables || []);
    setDraftColumns({ ...(it.allowed_columns || {}) });
    setDraftDeny(it.deny_by_default || false);
    setTab("row");
  }

  function toggleTable(t: string) {
    setDraftTables(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);
  }
  function toggleColumn(table: string, col: string) {
    setDraftColumns(prev => {
      const cur = prev[table] || [];
      const next = cur.includes(col) ? cur.filter(x => x !== col) : [...cur, col];
      const out = { ...prev };
      if (next.length === 0) delete out[table]; else out[table] = next;
      return out;
    });
  }

  async function save() {
    if (!active) return;
    setBusy(true);
    try {
      const row_rules: Record<string, string[]> = {};
      for (const [dim, csv] of Object.entries(draftRow)) {
        const vals = csv.split(/[,，;；]/).map(x => x.trim()).filter(Boolean);
        if (vals.length) row_rules[dim] = vals;
      }
      await api.putPermissions(active.user_id, {
        row_rules,
        allowed_tables: draftTables,
        allowed_columns: draftColumns,
        deny_by_default: draftDeny,
      });
      await refresh();
      setActive(null);
    } catch (e: any) { alert("保存失败: " + (e?.message || e)); }
    finally { setBusy(false); }
  }

  if (loading) return <PageLoading text="加载权限配置…" />;
  const dims = (semantic?.dimensions || []);
  const tables = (semantic?.tables || []);

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <PageHeader
        title="数据权限管理"
        desc="三层校验：行级（维度值白名单）+ 表级 + 字段级。管理员不受限。未配置规则时：本地/开发默认开放、生产环境默认拒绝（需显式授权）；也可对单用户启用 deny by default 强制拒绝。"
      />

      <div className="qq-card overflow-hidden">
        <table className="qq-table">
          <thead><tr><th>用户名</th><th>角色</th><th>行级</th><th>表级</th><th>字段级</th><th>默认</th><th>操作</th></tr></thead>
          <tbody>
            {items.map((it) => {
              const rowCount = Object.keys(it.row_rules || {}).length;
              const tableCount = (it.allowed_tables || []).length;
              const colCount = Object.values(it.allowed_columns || {}).reduce((a,b)=>a+b.length,0);
              return (
                <tr key={it.user_id}>
                  <td>{it.username}</td>
                  <td>
                    {it.role === "admin"
                      ? <span className="qq-pill-blue">管理员（不受限）</span>
                      : <span className="qq-pill-grey">普通用户</span>}
                  </td>
                  <td>{rowCount ? <span className="qq-pill-amber">{rowCount} 维度</span> : <span className="text-slate-300">—</span>}</td>
                  <td>{tableCount ? <span className="qq-pill-amber">{tableCount} 表</span> : <span className="text-slate-300">—</span>}</td>
                  <td>{colCount ? <span className="qq-pill-amber">{colCount} 字段</span> : <span className="text-slate-300">—</span>}</td>
                  <td>{it.deny_by_default ? <span className="qq-pill-red">严格</span> : <span className="text-slate-300">开放</span>}</td>
                  <td><button onClick={() => open(it)} className="text-xs text-blue-600 hover:underline">编辑</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {active && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={() => setActive(null)}>
          <div className="qq-card max-h-[85vh] w-[760px] max-w-[92vw] overflow-auto px-5 py-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="text-base font-semibold text-slate-800">编辑：{active.username}</div>
                <div className="text-xs text-slate-400">用户 ID: <span className="font-mono">{active.user_id.slice(0,8)}…</span></div>
              </div>
              <button onClick={() => setActive(null)} className="text-xs text-slate-500 hover:underline">关闭</button>
            </div>

            <div className="mb-3 flex gap-1 rounded-xl border bg-white p-1 text-xs" style={{ borderColor: "#e6ecf6" }}>
              <TabBtn active={tab==="row"}    onClick={()=>setTab("row")}>行级 · 维度白名单</TabBtn>
              <TabBtn active={tab==="table"}  onClick={()=>setTab("table")}>表级 · 允许表</TabBtn>
              <TabBtn active={tab==="column"} onClick={()=>setTab("column")}>字段级 · 允许列</TabBtn>
            </div>

            {tab === "row" && (
              <div className="space-y-2">
                <p className="text-xs text-slate-500">每行一个维度，值用逗号分隔。不填 = 不限制该维度。</p>
                {dims.map((d) => (
                  <div key={d.name} className="grid grid-cols-12 items-start gap-2">
                    <div className="col-span-3 text-xs">
                      <div className="font-medium text-slate-700">{d.label}</div>
                      <div className="font-mono text-[10px] text-slate-400">{d.name}</div>
                    </div>
                    <input
                      value={draftRow[d.name] || ""}
                      onChange={(e) => setDraftRow(p => ({...p, [d.name]: e.target.value}))}
                      placeholder={`样例：${(d.samples || []).slice(0,3).join(", ")}`}
                      className="col-span-9 rounded-lg border bg-white px-3 py-1.5 text-xs"
                      style={{ borderColor: "#e6ecf6" }}
                    />
                  </div>
                ))}
              </div>
            )}

            {tab === "table" && (
              <div className="space-y-2">
                <p className="text-xs text-slate-500">勾选用户能访问的物理表。未选 = 不限制。</p>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                  {tables.map((t) => {
                    const on = draftTables.includes(t.name);
                    return (
                      <label key={t.name} className={`flex cursor-pointer items-start gap-2 rounded-xl border px-3 py-2 ${on ? "border-blue-200 bg-blue-50" : "border-slate-200 bg-white hover:border-slate-300"}`}>
                        <input type="checkbox" checked={on} onChange={() => toggleTable(t.name)} className="mt-1 accent-blue-500" />
                        <div className="text-xs">
                          <div className="font-medium text-slate-700">{t.label}</div>
                          <div className="font-mono text-[10px] text-slate-400">{t.name}</div>
                          {t.grain && <div className="mt-0.5 text-[10px] text-slate-400">粒度：{t.grain}</div>}
                        </div>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            {tab === "column" && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">选了某张表的字段 = 用户只能 SELECT 这些字段。表内字段未选 = 该表无字段限制。</p>
                {tables.map((t) => {
                  const cols = tableColumns[t.name] || [];
                  if (cols.length === 0) return null;
                  return (
                    <div key={t.name} className="rounded-xl border bg-white p-3" style={{ borderColor: "#e6ecf6" }}>
                      <div className="mb-1 text-xs font-medium text-slate-700">
                        {t.label} <span className="ml-1 font-mono text-[10px] text-slate-400">{t.name}</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {cols.map((c) => {
                          const on = (draftColumns[t.name] || []).includes(c);
                          return (
                            <button
                              key={c}
                              onClick={() => toggleColumn(t.name, c)}
                              className={`rounded-md border px-2 py-0.5 text-[11px] ${on ? "border-blue-200 bg-blue-50 text-blue-600" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300"}`}
                            >{c}</button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <label className="mt-4 flex items-center gap-2 text-xs text-slate-500">
              <input type="checkbox" checked={draftDeny} onChange={(e) => setDraftDeny(e.target.checked)} className="accent-blue-500" />
              <span>未配置任何规则时 <b>默认拒绝</b>（严格模式）</span>
            </label>

            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setActive(null)} className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{ borderColor: "#e6ecf6" }}>取消</button>
              <button onClick={save} disabled={busy} className="qq-btn-primary !px-4 !py-1.5 text-xs disabled:opacity-50">
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------- shared mini components ----------
function PageHeader({ title, desc }: { title: string; desc: string }) {
  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
      <p className="text-xs text-slate-400">{desc}</p>
    </div>
  );
}
function PageLoading({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center px-6 py-12 text-sm text-slate-400">
      <span className="qq-loading-dot mr-2" />{text}
    </div>
  );
}
function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`flex-1 rounded-lg px-3 py-1.5 text-center ${active ? "bg-blue-50 text-blue-600" : "text-slate-500 hover:text-slate-700"}`}>
      {children}
    </button>
  );
}
