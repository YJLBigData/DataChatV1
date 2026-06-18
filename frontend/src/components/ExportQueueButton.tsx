/**
 * 顶部「数据导出队列」按钮 + 面板（自包含）。
 *  · 显示进行中(queued/running)+就绪(ready) 的计数徽标；
 *  · 面板列出我的导出 job：状态 / 行数 / 下载 / 删除；
 *  · 有进行中 job 或面板打开时轮询刷新；导出提交事件触发即时刷新。
 * 下载走带 Bearer 的 fetch + blob（FileResponse 需鉴权，普通 <a> 带不上 token）。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api, auth } from "../api";
import { toast } from "../shared/toast";
import type { ExportJob } from "../types";

const ACTIVE = new Set(["queued", "running"]);

function statusText(s: ExportJob["status"]): string {
  return { queued: "排队中", running: "生成中", ready: "可下载", error: "失败", expired: "已过期" }[s] || s;
}
function statusClass(s: ExportJob["status"]): string {
  if (s === "ready") return "text-emerald-600";
  if (s === "error" || s === "expired") return "text-rose-500";
  return "text-amber-600";
}

export function ExportQueueButton() {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  const refresh = useCallback(async () => {
    try { setJobs((await api.listExports()).items || []); } catch { /* ignore */ }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // 提交导出后立即刷新
  useEffect(() => {
    const fn = () => { refresh(); };
    window.addEventListener("datachat:export-submitted", fn);
    return () => window.removeEventListener("datachat:export-submitted", fn);
  }, [refresh]);

  // 有进行中任务或面板打开 → 轮询（面板关闭且无在途任务时停止，避免空转）
  useEffect(() => {
    const active = jobs.some((j) => ACTIVE.has(j.status));
    if (!active && !open) return;
    const h = window.setInterval(refresh, 2500);
    return () => window.clearInterval(h);
  }, [jobs, open, refresh]);

  // 键盘可达性（#10）：打开时 Esc 关闭；打开瞬间把焦点移到关闭按钮，避免焦点被困在页面里。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    window.addEventListener("keydown", onKey);
    closeBtnRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const activeCount = jobs.filter((j) => ACTIVE.has(j.status)).length;
  const readyCount = jobs.filter((j) => j.status === "ready").length;
  const badge = activeCount + readyCount;

  const download = useCallback(async (job: ExportJob) => {
    try {
      const tk = auth.getToken();
      const resp = await fetch(api.exportDownloadUrl(job.id), {
        headers: tk ? { Authorization: "Bearer " + tk } : {},
      });
      if (!resp.ok) {
        // surface 后端真实原因（409 未就绪 / 410 已过期 / 404 不存在），而非笼统"下载失败"。
        let msg = resp.status === 410 ? "该导出文件已过期，请重新导出"
          : resp.status === 409 ? "导出尚未就绪，请稍候再试"
          : resp.status === 404 ? "导出任务不存在"
          : "下载失败，请稍后重试";
        try {
          const t = await resp.text();
          const j = t ? JSON.parse(t) : null;
          const d = j && (typeof j.detail === "string" ? j.detail : j.detail?.user_message);
          if (d) msg = d;
        } catch { /* 用上面的状态码兜底文案 */ }
        toast.error(msg);
        refresh();  // 状态可能已变（过期/被清理）→ 立即同步列表
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = job.filename || "export.xlsx";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch { toast.error("下载失败，请稍后重试"); }
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    try { await api.deleteExport(id); refresh(); } catch { toast.error("删除失败"); }
  }, [refresh]);

  return (
    <div className="relative">
      <button
        className="relative flex h-8 items-center gap-1 rounded-lg border px-2 text-[12px] text-slate-500 hover:bg-slate-50"
        style={{ borderColor: "#e6ecf6" }}
        title="数据导出队列"
        onClick={() => { setOpen((v) => !v); refresh(); }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="hidden sm:inline">导出</span>
        {badge > 0 && (
          <span className={`ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] text-white ${activeCount ? "bg-amber-500" : "bg-emerald-500"}`}>
            {badge}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            ref={panelRef}
            role="dialog"
            aria-label="数据导出队列"
            className="absolute right-0 z-40 mt-1 w-[320px] rounded-xl border bg-white p-2 shadow-lg"
            style={{ borderColor: "#eef1f8" }}
          >
            <div className="flex items-center justify-between px-2 py-1">
              <span className="text-[13px] font-semibold text-slate-700">数据导出队列</span>
              <span className="flex items-center gap-2">
                <button className="text-[11px] text-slate-400 hover:text-slate-600" onClick={refresh}>刷新</button>
                <button
                  ref={closeBtnRef}
                  className="flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                  title="关闭（Esc）" aria-label="关闭" onClick={() => setOpen(false)}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" /></svg>
                </button>
              </span>
            </div>
            {jobs.length === 0 ? (
              <div className="px-3 py-6 text-center text-[12px] text-slate-400">
                暂无导出任务。在结果卡片点「导出 Excel」即可加入队列。
              </div>
            ) : (
              <div className="max-h-[360px] space-y-1 overflow-y-auto">
                {jobs.map((j) => (
                  <div key={j.id} className="rounded-lg border px-2.5 py-1.5 text-[12px]" style={{ borderColor: "#eef1f8" }}>
                    <div className="flex items-center gap-2">
                      <span className="flex-1 truncate text-slate-700" title={j.question}>{j.question || "导出"}</span>
                      <span className={`shrink-0 ${statusClass(j.status)}`}>{statusText(j.status)}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                      {j.status === "ready" && <span>{j.row_count} 行{j.truncated ? "（已截断）" : ""}</span>}
                      {j.status === "error" && <span className="truncate text-rose-400">{j.error}</span>}
                      <span className="ml-auto flex items-center gap-2">
                        {j.status === "ready" && (
                          <button className="text-blue-500 hover:underline" onClick={() => download(j)}>下载</button>
                        )}
                        <button className="text-slate-400 hover:text-rose-500" onClick={() => remove(j.id)}>删除</button>
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
