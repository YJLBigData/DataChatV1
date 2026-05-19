import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";

/**
 * 语义层管理 — 三个 Tab：数据表 / 维度 / 指标。
 *   · 列表 + 搜索 + 新增 / 编辑 / 删除
 *   · "自动分析新表" 按钮：填入物理表名 → LLM 生成 dimensions / metrics 建议 → 用户审核保存
 *   · 编辑使用 YAML/JSON 文本框（小而精，避免把 14 种字段都做成表单）
 *   · 兜底入口：高级用户可全文编辑 semantic.yaml
 */
type Kind = "tables" | "dimensions" | "metrics";

export function SemanticPage() {
  const [tab, setTab] = useState<Kind | "raw">("tables");
  const [tables, setTables] = useState<Record<string, any>>({});
  const [dims, setDims] = useState<Record<string, any>>({});
  const [metrics, setMetrics] = useState<Record<string, any>>({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [edit, setEdit] = useState<{ kind: Kind; name: string; body: any; isNew: boolean } | null>(null);
  const [yamlEdit, setYamlEdit] = useState<{ content: string; path: string } | null>(null);
  const [analyzeOpen, setAnalyzeOpen] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [t, d, m] = await Promise.all([
        api.semanticEntities("tables"),
        api.semanticEntities("dimensions"),
        api.semanticEntities("metrics"),
      ]);
      setTables(t.items || {});
      setDims(d.items || {});
      setMetrics(m.items || {});
    } finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  const current = useMemo(() => {
    if (tab === "tables") return tables;
    if (tab === "dimensions") return dims;
    if (tab === "metrics") return metrics;
    return {};
  }, [tab, tables, dims, metrics]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return Object.entries(current);
    return Object.entries(current).filter(([k, v]) =>
      k.toLowerCase().includes(q) || JSON.stringify(v).toLowerCase().includes(q)
    );
  }, [current, search]);

  async function loadYaml() {
    const r = await api.semanticGet();
    setYamlEdit({ content: r.content, path: r.path });
  }
  async function saveYaml() {
    if (!yamlEdit) return;
    try {
      const r = await api.semanticPut(yamlEdit.content);
      alert(`已保存\n指标 ${r.metrics} · 维度 ${r.dimensions} · 表 ${r.tables}`);
      setYamlEdit(null);
      await refresh();
    } catch (e: any) { alert("保存失败：" + (e?.message || e)); }
  }

  async function saveEntity() {
    if (!edit) return;
    try {
      await api.semanticUpsert(edit.kind, edit.name, edit.body);
      await refresh();
      setEdit(null);
    } catch (e: any) { alert("保存失败：" + (e?.message || e)); }
  }
  async function deleteEntity(kind: Kind, name: string) {
    if (!confirm(`确定删除 ${kind} / ${name} ?`)) return;
    try {
      await api.semanticDelete(kind, name);
      await refresh();
    } catch (e: any) { alert("删除失败：" + (e?.message || e)); }
  }

  if (loading) return <div className="px-6 py-12 text-center text-sm text-slate-400">加载语义层…</div>;

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">知识库 / 语义层</h2>
          <p className="text-xs text-slate-400">业务术语、指标、维度、表的统一语义建模。修改后保存即重建检索索引。</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setAnalyzeOpen(true)} className="rounded-xl border bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-blue-200 hover:text-blue-600" style={{borderColor:"#e6ecf6"}}>
            ⚙ 自动分析新表
          </button>
          <button onClick={loadYaml} className="rounded-xl border bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-blue-200 hover:text-blue-600" style={{borderColor:"#e6ecf6"}}>
            高级 · 编辑 YAML
          </button>
        </div>
      </div>

      {/* tab + search + add */}
      <div className="qq-card flex flex-wrap items-center gap-2 px-4 py-3">
        <div className="flex gap-1 rounded-xl border bg-white p-1 text-xs" style={{ borderColor: "#e6ecf6" }}>
          <TabBtn active={tab==="tables"}     onClick={()=>setTab("tables")}>数据表 {Object.keys(tables).length}</TabBtn>
          <TabBtn active={tab==="dimensions"} onClick={()=>setTab("dimensions")}>维度 {Object.keys(dims).length}</TabBtn>
          <TabBtn active={tab==="metrics"}    onClick={()=>setTab("metrics")}>指标 {Object.keys(metrics).length}</TabBtn>
        </div>
        <input value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="按名称 / 字段 / 标签搜索"
          className="ml-2 flex-1 rounded-lg border bg-white px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}} />
        {tab !== "raw" && (
          <button onClick={()=>setEdit({ kind: tab as Kind, name: "", body: defaultBody(tab as Kind), isNew: true })}
            className="qq-btn-primary !px-3 !py-1.5 text-xs">+ 新增</button>
        )}
      </div>

      <div className="qq-card overflow-hidden">
        <table className="qq-table">
          <thead>
            {tab === "tables" && <tr><th>表名</th><th>标签</th><th>粒度</th><th>描述</th><th className="w-32">操作</th></tr>}
            {tab === "dimensions" && <tr><th>维度名</th><th>标签</th><th>覆盖表</th><th>样例值</th><th className="w-32">操作</th></tr>}
            {tab === "metrics" && <tr><th>指标名</th><th>标签</th><th>表达式</th><th>表</th><th className="w-32">操作</th></tr>}
          </thead>
          <tbody>
            {filtered.map(([name, body]) => (
              <tr key={name}>
                <td className="font-mono text-[11px]">{name}</td>
                {tab === "tables" && <>
                  <td>{(body as any).label || "—"}</td>
                  <td className="text-slate-500">{(body as any).grain || "—"}</td>
                  <td className="max-w-[360px] truncate text-slate-500" title={(body as any).description}>{(body as any).description || "—"}</td>
                </>}
                {tab === "dimensions" && <>
                  <td>{(body as any).label || "—"}</td>
                  <td className="text-slate-500">{Object.keys((body as any).table_columns || {}).length} 张</td>
                  <td className="max-w-[280px] truncate text-slate-500">{((body as any).sample_values || []).join(", ") || "—"}</td>
                </>}
                {tab === "metrics" && <>
                  <td>{(body as any).label || "—"}</td>
                  <td className="font-mono text-[11px] text-slate-500">{(body as any).expression || "—"}</td>
                  <td className="text-slate-500">{(body as any).table || "—"}</td>
                </>}
                <td>
                  <button onClick={()=>setEdit({ kind: tab as Kind, name, body, isNew: false })} className="mr-2 text-xs text-blue-600 hover:underline">编辑</button>
                  <button onClick={()=>deleteEntity(tab as Kind, name)} className="text-xs text-rose-600 hover:underline">删除</button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-8 text-center text-xs text-slate-400">无匹配条目</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* edit drawer */}
      {edit && (
        <Modal title={`${edit.isNew ? "新增" : "编辑"} ${LABEL[edit.kind]}`} onClose={()=>setEdit(null)}>
          <div className="space-y-3">
            <label className="block text-xs text-slate-500">
              名称（英文 key，蛇形小写）
              <input
                value={edit.name}
                onChange={(e)=>setEdit({...edit, name: e.target.value})}
                disabled={!edit.isNew}
                className="mt-1 w-full rounded-lg border bg-white px-3 py-1.5 text-xs font-mono"
                style={{borderColor:"#e6ecf6"}}
              />
            </label>
            <label className="block text-xs text-slate-500">
              定义（JSON）
              <textarea
                value={typeof edit.body === "string" ? edit.body : toYaml(edit.body)}
                onChange={(e)=>{
                  try { const p = parseYamlOrJson(e.target.value); setEdit({...edit, body: p}); }
                  catch { setEdit({...edit, body: e.target.value}); }
                }}
                spellCheck={false}
                className="mt-1 w-full rounded-lg border bg-white px-3 py-2 font-mono text-[11px]"
                style={{borderColor:"#e6ecf6", minHeight: 280, resize: "vertical"}}
              />
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={()=>setEdit(null)} className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{borderColor:"#e6ecf6"}}>取消</button>
              <button onClick={saveEntity} className="qq-btn-primary !px-4 !py-1.5 text-xs">保存</button>
            </div>
          </div>
        </Modal>
      )}

      {analyzeOpen && (
        <AnalyzeNewTableModal
          onClose={()=>setAnalyzeOpen(false)}
          onSaved={async ()=>{ setAnalyzeOpen(false); await refresh(); }}
        />
      )}

      {yamlEdit && (
        <Modal title={`高级 · 全文编辑 semantic.yaml`} onClose={()=>setYamlEdit(null)} wide>
          <div className="mb-2 text-xs text-slate-400">{yamlEdit.path}</div>
          <textarea
            value={yamlEdit.content}
            onChange={(e)=>setYamlEdit({...yamlEdit, content: e.target.value})}
            spellCheck={false}
            className="w-full rounded-lg border bg-white px-3 py-2 font-mono text-[11px]"
            style={{borderColor:"#e6ecf6", minHeight: 460, resize: "vertical"}}
          />
          <div className="mt-3 flex justify-end gap-2">
            <button onClick={()=>setYamlEdit(null)} className="rounded-xl border px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}}>取消</button>
            <button onClick={saveYaml} className="qq-btn-primary !px-4 !py-1.5 text-xs">保存并热重载</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function AnalyzeNewTableModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [table, setTable] = useState("");
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  async function analyze() {
    if (!table.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.semanticAnalyze(table.trim());
      if ((r as any).ok && (r as any).proposal) setProposal((r as any).proposal);
      else setErr((r as any).user_message || "分析失败");
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  async function saveAll() {
    if (!proposal) return;
    setBusy(true);
    try {
      const tp = proposal.table_proposal || {};
      if (Object.keys(tp).length) {
        await api.semanticUpsert("tables", table.trim(), tp);
      }
      for (const [k, v] of Object.entries(proposal.dimensions || {})) {
        await api.semanticUpsert("dimensions", k, v);
      }
      for (const [k, v] of Object.entries(proposal.metrics || {})) {
        await api.semanticUpsert("metrics", k, v);
      }
      onSaved();
    } catch (e: any) { alert("保存失败: " + (e?.message || e)); }
    finally { setBusy(false); }
  }

  return (
    <Modal title="自动分析新表" onClose={onClose} wide>
      <div className="space-y-3">
        <div className="text-xs text-slate-500">输入 MySQL chatbi 库中的物理表名，让 qwen 自动识别维度 / 指标 / 时间字段，生成语义层建议。生成后可手动微调再保存。</div>
        <div className="flex gap-2">
          <input value={table} onChange={(e)=>setTable(e.target.value)}
            placeholder="例如：ads_bi_month_shop_item_dan_summary_df"
            className="flex-1 rounded-lg border bg-white px-3 py-1.5 text-xs font-mono" style={{borderColor:"#e6ecf6"}} />
          <button onClick={analyze} disabled={busy || !table.trim()}
            className="qq-btn-primary !px-3 !py-1.5 text-xs disabled:opacity-50">
            {busy && !proposal ? "分析中…" : "开始分析"}
          </button>
        </div>
        {err && <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{err}</div>}
        {proposal && (
          <>
            <div className="text-xs text-slate-600">
              <div className="mb-1 font-semibold">表建议</div>
              <pre className="max-h-[160px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px]">{toYaml(proposal.table_proposal)}</pre>
              <div className="mb-1 mt-3 font-semibold">维度建议（{Object.keys(proposal.dimensions||{}).length}）</div>
              <pre className="max-h-[160px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px]">{toYaml(proposal.dimensions)}</pre>
              <div className="mb-1 mt-3 font-semibold">指标建议（{Object.keys(proposal.metrics||{}).length}）</div>
              <pre className="max-h-[160px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px]">{toYaml(proposal.metrics)}</pre>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={onClose} className="rounded-xl border px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}}>取消</button>
              <button onClick={saveAll} disabled={busy} className="qq-btn-primary !px-4 !py-1.5 text-xs disabled:opacity-50">
                {busy ? "保存中…" : "一键保存到语义层"}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

const LABEL: Record<Kind, string> = { tables: "数据表", dimensions: "维度", metrics: "指标" };

function defaultBody(kind: Kind): any {
  if (kind === "tables") return { label: "", description: "", grain: "", time_field: "", primary_dimensions: [], measures: [] };
  if (kind === "dimensions") return { label: "", aliases: [], table_columns: {}, sample_values: [], description: "" };
  return { label: "", aliases: [], table: "", expression: "", unit: "", display_format: "currency_cn", decimals: 2, description: "" };
}

function toYaml(obj: any): string {
  return JSON.stringify(obj, null, 2);
}

function parseYamlOrJson(text: string): any {
  // 简化：只接受 JSON。语义层 YAML 由后端 PUT raw YAML 接口处理。
  return JSON.parse(text);
}

function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={onClose}>
      <div className={`qq-card max-h-[88vh] overflow-auto px-5 py-4 ${wide ? "w-[860px]" : "w-[640px]"} max-w-[92vw]`} onClick={(e)=>e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-base font-semibold text-slate-800">{title}</div>
          <button onClick={onClose} className="text-xs text-slate-500 hover:underline">关闭</button>
        </div>
        {children}
      </div>
    </div>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`rounded-lg px-3 py-1.5 ${active ? "bg-blue-50 text-blue-600" : "text-slate-500 hover:text-slate-700"}`}>
      {children}
    </button>
  );
}
