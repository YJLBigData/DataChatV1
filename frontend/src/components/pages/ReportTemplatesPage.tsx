import { useEffect, useMemo, useState } from "react";

import { api, auth } from "../../api";
import type { AuthUser } from "../../types";

/** 报告提示词模板管理 — 用户隔离：
 *   · 普通用户：看到「系统默认 + 自己创建的」；只能改自己的
 *   · 管理员：看到所有用户的所有模板；可按用户筛选
 */
export function ReportTemplatesPage() {
  const me: AuthUser | null = auth.getUser();
  const isAdmin = me?.role === "admin";
  const [items, setItems] = useState<{id:string;name:string;prompt:string;is_default:boolean;user_id:string;is_system:boolean;is_mine:boolean;created_at:number;updated_at:number}[]>([]);
  const [allUsers, setAllUsers] = useState<AuthUser[]>([]);
  const [filterUser, setFilterUser] = useState<string>("");   // admin 筛选
  const [loading, setLoading] = useState(true);
  const [edit, setEdit] = useState<any>(null);

  async function refresh() {
    setLoading(true);
    try {
      const r = await api.listReportTemplates(filterUser || undefined);
      setItems(r.items || []);
    } finally { setLoading(false); }
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [filterUser]);
  // admin: load all users for filter dropdown
  useEffect(() => {
    if (!isAdmin) return;
    api.listUsers().then(r => setAllUsers(r.items || [])).catch(() => {});
  }, [isAdmin]);

  const editable = useMemo(() => {
    if (!me) return new Set<string>();
    return new Set(items.filter(t => isAdmin || t.is_mine).map(t => t.id));
  }, [items, isAdmin, me]);

  async function save() {
    if (!edit) return;
    if (!edit.name?.trim() || !edit.prompt?.trim()) { alert("名称和提示词都不能为空"); return; }
    try {
      if (edit.id) await api.updateReportTemplate(edit.id, { name: edit.name, prompt: edit.prompt, is_default: !!edit.is_default });
      else await api.createReportTemplate(edit.name, edit.prompt, !!edit.is_default, isAdmin && !!edit.system);
      setEdit(null);
      await refresh();
    } catch (e: any) { alert("失败：" + (e?.message || e)); }
  }
  async function remove(id: string) {
    if (!confirm("确定删除此模板？")) return;
    try { await api.deleteReportTemplate(id); await refresh(); }
    catch (e: any) { alert("失败：" + (e?.message || e)); }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-6 py-6">
      <div className="flex items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">报告提示词模板</h2>
          <p className="text-xs text-slate-400">
            下载 DOCX 报告时使用。{isAdmin ? "你是管理员，能看到并管理所有用户的模板。" : "你能看到系统默认模板和你自己创建的模板。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isAdmin && (
            <select value={filterUser} onChange={(e) => setFilterUser(e.target.value)}
              className="rounded-lg border bg-white px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}}>
              <option value="">全部用户</option>
              <option value="system">仅系统模板</option>
              {allUsers.map(u => <option key={u.id} value={u.id}>{u.username}</option>)}
            </select>
          )}
          <button onClick={()=>setEdit({ name: "", prompt: "", is_default: false })} className="qq-btn-primary !px-3 !py-1.5 text-xs">+ 新建模板</button>
        </div>
      </div>

      {loading && <div className="px-5 py-12 text-center text-xs text-slate-400">加载中…</div>}

      {!loading && (
        <div className="space-y-3">
          {items.length === 0 && (
            <div className="qq-card px-5 py-8 text-center text-xs text-slate-400">无模板</div>
          )}
          {items.map((t) => {
            const canEdit = editable.has(t.id);
            return (
              <div key={t.id} className="qq-card px-5 py-4">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="text-sm font-semibold text-slate-800">{t.name}</div>
                    {t.is_default && <span className="qq-pill-blue">默认</span>}
                    {t.is_system && <span className="qq-pill-grey">系统</span>}
                    {!t.is_system && t.is_mine && <span className="qq-pill-amber">我的</span>}
                    {!t.is_system && !t.is_mine && isAdmin && (
                      <span className="qq-pill-grey">归属：{allUsers.find(u => u.id === t.user_id)?.username || t.user_id.slice(0,8)}</span>
                    )}
                  </div>
                  {canEdit && (
                    <div className="flex gap-2 text-xs">
                      <button onClick={()=>setEdit({ ...t })} className="text-blue-600 hover:underline">编辑</button>
                      <button onClick={()=>remove(t.id)} className="text-rose-600 hover:underline">删除</button>
                    </div>
                  )}
                </div>
                <pre className="max-h-[160px] overflow-auto rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-700 whitespace-pre-wrap">{t.prompt}</pre>
                <div className="mt-2 text-[10px] text-slate-400">更新时间：{new Date(t.updated_at * 1000).toLocaleString()}</div>
              </div>
            );
          })}
        </div>
      )}

      {edit && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={()=>setEdit(null)}>
          <div className="qq-card w-[760px] max-w-[92vw] max-h-[85vh] overflow-auto px-5 py-4" onClick={(e)=>e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-base font-semibold text-slate-800">{edit.id ? "编辑模板" : "新建模板"}</div>
              <button onClick={()=>setEdit(null)} className="text-xs text-slate-500 hover:underline">关闭</button>
            </div>
            <label className="mb-2 block text-xs text-slate-500">
              模板名称
              <input value={edit.name} onChange={(e)=>setEdit({...edit, name: e.target.value})}
                className="mt-1 w-full rounded-lg border bg-white px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}} />
            </label>
            <label className="mb-2 block text-xs text-slate-500">
              提示词（system prompt，决定报告风格）
              <textarea value={edit.prompt} onChange={(e)=>setEdit({...edit, prompt: e.target.value})}
                spellCheck={false}
                className="mt-1 w-full rounded-lg border bg-white px-3 py-2 text-[11px]"
                style={{borderColor:"#e6ecf6", minHeight: 280, resize: "vertical"}} />
            </label>
            <label className="mb-3 flex items-center gap-2 text-xs text-slate-500">
              <input type="checkbox" checked={!!edit.is_default} onChange={(e)=>setEdit({...edit, is_default: e.target.checked})} className="accent-blue-500" />
              <span>设为默认模板（其他模板自动取消默认）</span>
            </label>
            {isAdmin && !edit.id && (
              <label className="mb-3 flex items-center gap-2 text-xs text-slate-500">
                <input type="checkbox" checked={!!edit.system} onChange={(e)=>setEdit({...edit, system: e.target.checked})} className="accent-blue-500" />
                <span>创建为<b>系统级模板</b>（所有用户可见可用；仅管理员可建）</span>
              </label>
            )}
            <div className="flex justify-end gap-2">
              <button onClick={()=>setEdit(null)} className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{borderColor:"#e6ecf6"}}>取消</button>
              <button onClick={save} className="qq-btn-primary !px-4 !py-1.5 text-xs">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
