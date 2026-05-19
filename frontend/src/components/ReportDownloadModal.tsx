import { useEffect, useState } from "react";

import { api, auth } from "../api";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 点击「下载」时执行，传入选中的 template_id（可选）。 */
  onDownload: (template_id: string | null) => Promise<void>;
}

/** 下载报告 — 选模板。 */
export function ReportDownloadModal({ open, onClose, onDownload }: Props) {
  const [items, setItems] = useState<{id:string;name:string;is_default:boolean;prompt:string}[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        const r = await api.listReportTemplates();
        setItems(r.items || []);
        const def = (r.items || []).find(t => t.is_default);
        setChosen(def?.id || r.items?.[0]?.id || null);
      } catch { /* ignore */ }
    })();
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={onClose}>
      <div className="qq-card w-[560px] max-w-[92vw] px-5 py-4" onClick={(e)=>e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-base font-semibold text-slate-800">下载经营分析报告</div>
          <button onClick={onClose} className="text-xs text-slate-500 hover:underline">关闭</button>
        </div>
        <p className="mb-3 text-xs text-slate-500">选择一个提示词模板（决定报告风格）。默认是飞鹤上市报告标准商业分析格式。</p>
        <div className="max-h-[280px] overflow-auto space-y-1.5">
          {items.map((t) => {
            const on = t.id === chosen;
            return (
              <label key={t.id} className={`flex cursor-pointer items-start gap-2 rounded-xl border px-3 py-2 ${on ? "border-blue-200 bg-blue-50" : "border-slate-200 hover:border-slate-300"}`}>
                <input type="radio" checked={on} onChange={() => setChosen(t.id)} className="mt-1 accent-blue-500" />
                <div className="flex-1 min-w-0 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-700">{t.name}</span>
                    {t.is_default && <span className="qq-pill-blue">默认</span>}
                  </div>
                  <div className="mt-1 max-h-12 overflow-hidden text-[11px] leading-5 text-slate-500 line-clamp-2">{t.prompt.slice(0, 200)}…</div>
                </div>
              </label>
            );
          })}
          {items.length === 0 && <div className="rounded-lg bg-slate-50 px-3 py-4 text-center text-xs text-slate-400">未配置任何模板</div>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{borderColor:"#e6ecf6"}}>取消</button>
          <button onClick={async () => {
            setBusy(true);
            try { await onDownload(chosen); onClose(); } catch (e: any) { alert("下载失败：" + (e?.message || e)); }
            finally { setBusy(false); }
          }} disabled={busy} className="qq-btn-primary !px-4 !py-1.5 text-xs disabled:opacity-50">
            {busy ? "生成中…" : "生成并下载"}
          </button>
        </div>
        <div className="mt-2 text-center text-[10px] text-slate-400">需要新模板？管理员可在「报告模板」页面管理。当前登录用户：{auth.getUser()?.username || "—"}</div>
      </div>
    </div>
  );
}
