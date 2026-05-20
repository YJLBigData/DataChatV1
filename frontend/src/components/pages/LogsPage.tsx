import { useEffect, useState } from "react";

import { api } from "../../api";
import type { QueryLogEntry } from "../../types";

/** 管理员问数审计 — 分页 + 用户/状态/关键字筛选 + 详情抽屉。 */
export function LogsPage() {
  const [items, setItems] = useState<QueryLogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [limit] = useState(50);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [status, setStatus] = useState("");
  const [keyword, setKeyword] = useState("");
  const [active, setActive] = useState<QueryLogEntry | null>(null);

  async function refresh(off: number = offset) {
    setLoading(true); setErr(null);
    try {
      const resp = await api.listLogs({
        limit, offset: off,
        username: username || undefined,
        status: status || undefined,
        keyword: keyword || undefined,
      });
      setItems(resp.items || []);
      setTotal(resp.total || 0);
      setOffset(off);
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(0); /* eslint-disable-next-line */ }, []);

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <div className="flex items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">问数审计</h2>
          <p className="text-xs text-slate-400">每一次问数（含失败、澄清）都自动记录。仅管理员可查看。</p>
        </div>
        <div className="rounded-xl border bg-white px-3 py-1 text-xs text-slate-500" style={{borderColor:"#e6ecf6"}}>共 {total} 条</div>
      </div>

      {/* 摘要卡 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="总查询" value={total} />
        <StatCard label="成功"   value={items.filter(it=>it.status==="ok").length} color="text-emerald-600"/>
        <StatCard label="澄清"   value={items.filter(it=>it.status==="clarify").length} color="text-amber-600"/>
        <StatCard label="失败"   value={items.filter(it=>it.status==="error").length} color="text-rose-600"/>
      </div>

      {/* 过滤 */}
      <div className="qq-card flex flex-wrap items-center gap-2 px-4 py-3">
        <input value={username} onChange={(e) => setUsername(e.target.value)}
          placeholder="按用户名过滤"
          className="w-40 rounded-lg border bg-white px-3 py-1.5 text-xs" style={{ borderColor: "#e6ecf6" }} />
        <select value={status} onChange={(e) => setStatus(e.target.value)}
          className="rounded-lg border bg-white px-3 py-1.5 text-xs" style={{ borderColor: "#e6ecf6" }}>
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="clarify">澄清</option>
          <option value="error">失败</option>
        </select>
        <input value={keyword} onChange={(e) => setKeyword(e.target.value)}
          placeholder="问句 / 指标关键词"
          className="flex-1 rounded-lg border bg-white px-3 py-1.5 text-xs" style={{ borderColor: "#e6ecf6" }} />
        <button onClick={() => refresh(0)} className="qq-btn-primary !px-3 !py-1.5 text-xs">查询</button>
      </div>

      <div className="qq-card overflow-hidden">
        {err && <div className="px-5 py-3 text-xs text-rose-600">{err}</div>}
        {loading && <div className="px-5 py-6 text-center text-xs text-slate-400">加载中…</div>}
        {!loading && (
          <div className="overflow-auto" style={{ maxHeight: "calc(100vh - 380px)" }}>
            <table className="qq-table">
              <thead className="sticky top-0 bg-white">
                <tr>
                  <th>时间</th><th>用户</th><th>问题</th>
                  <th>指标</th><th className="text-right">行数</th><th className="text-right">耗时</th>
                  <th>状态</th><th className="w-16">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.id} className="hover:bg-blue-50/30">
                    <td className="whitespace-nowrap tabular-nums text-slate-500">
                      {new Date(it.created_at * 1000).toLocaleString()}
                    </td>
                    <td>{it.username}</td>
                    <td className="max-w-[320px] truncate" title={it.question}>{it.question}</td>
                    <td className="text-slate-500 font-mono text-[11px]">{it.metric || "—"}</td>
                    <td className="tabular-nums text-right">{it.rows}</td>
                    <td className="tabular-nums text-right">{it.elapsed_ms} ms</td>
                    <td>
                      {it.status === "ok"      && <span className="qq-pill-green">成功</span>}
                      {it.status === "clarify" && <span className="qq-pill-amber">澄清</span>}
                      {it.status === "error"   && <span className="qq-pill-red">失败</span>}
                      {it.cached && <span className="qq-pill-grey ml-1">缓存</span>}
                    </td>
                    <td><button onClick={() => setActive(it)} className="text-xs text-blue-600 hover:underline">详情</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!items.length && <div className="px-5 py-8 text-center text-xs text-slate-400">暂无记录</div>}
          </div>
        )}
        <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-slate-500" style={{ borderColor: "#eef1f8" }}>
          <span>第 {Math.floor(offset / limit) + 1} 页 · 每页 {limit}</span>
          <div className="flex gap-2">
            <button disabled={offset === 0} onClick={() => refresh(Math.max(0, offset - limit))}
              className="rounded border px-2 py-1 text-xs disabled:opacity-40" style={{ borderColor: "#e6ecf6" }}>上一页</button>
            <button disabled={offset + limit >= total} onClick={() => refresh(offset + limit)}
              className="rounded border px-2 py-1 text-xs disabled:opacity-40" style={{ borderColor: "#e6ecf6" }}>下一页</button>
          </div>
        </div>
      </div>

      {active && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={() => setActive(null)}>
          <div className="qq-card max-h-[85vh] w-[760px] max-w-[92vw] overflow-auto px-5 py-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-base font-semibold text-slate-800">日志详情</div>
              <button onClick={() => setActive(null)} className="text-xs text-slate-500 hover:underline">关闭</button>
            </div>
            <div className="text-[11px] text-slate-400">trace_id: <span className="font-mono">{active.trace_id}</span></div>
            <div className="mt-2 text-sm font-medium text-slate-700 break-words">{active.question}</div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600">
              <Field label="用户" value={active.username}/>
              <Field label="状态" value={active.status}/>
              <Field label="指标" value={active.metric || "—"}/>
              <Field label="数据表" value={active.table || "—"}/>
              <Field label="行数" value={String(active.rows)}/>
              <Field label="耗时" value={`${active.elapsed_ms} ms`}/>
              <Field label="是否命中缓存" value={active.cached ? "是" : "否"}/>
              <Field label="是否澄清" value={active.needs_clarify ? "是" : "否"}/>
            </div>
            {active.error && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{active.error}</div>}
            {active.sql && (
              <details className="mt-3" open>
                <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">SQL</summary>
                <pre className="mt-1 max-h-[200px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-700">{active.sql}</pre>
              </details>
            )}
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">QueryPlan</summary>
              <pre className="mt-1 max-h-[200px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-700">{JSON.stringify(active.plan, null, 2)}</pre>
            </details>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color = "text-slate-800" }: { label: string; value: number; color?: string }) {
  return (
    <div className="qq-card px-4 py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className="mt-0.5 text-slate-700 font-mono text-[11px]">{value}</div>
    </div>
  );
}
