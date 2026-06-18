import { Check, Database, RefreshCcw, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { api, friendlyError } from "../api";
import { toast } from "../shared/toast";
import type { SmartQDataset } from "../types";

interface Props {
  selectedIds: string[];
  onChange: (ids: string[], datasets: SmartQDataset[]) => void;
}

export function SmartQDatasetButton({ selectedIds, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<SmartQDataset[]>([]);
  const [draft, setDraft] = useState<string[]>(selectedIds);

  const selectedSet = useMemo(() => new Set(draft), [draft]);
  const selectedNames = useMemo(() => {
    const byId = new Map(items.map((d) => [d.cube_id, d.name]));
    return selectedIds.map((id) => byId.get(id) || id);
  }, [items, selectedIds]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.smartqDatasets();
      setItems(res.items || []);
      if (!res.ok && res.error) toast.error(res.error);
    } catch (e: any) {
      toast.error(friendlyError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const openDialog = useCallback(() => {
    setDraft(selectedIds);
    setOpen(true);
    void load();
  }, [load, selectedIds]);

  const toggle = (id: string) => {
    setDraft((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const confirm = () => {
    const picked = items.filter((d) => draft.includes(d.cube_id));
    onChange(draft, picked);
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        className={`flex h-8 items-center gap-1.5 rounded-lg border px-2 text-[12px] hover:bg-slate-50 ${
          selectedIds.length ? "border-blue-200 bg-blue-50 text-blue-600" : "text-slate-500"
        }`}
        title="选择 Quick BI 智能小Q数据集作为本轮问数上下文"
        onClick={openDialog}
      >
        <Database size={14} strokeWidth={1.8} />
        <span>数据反问</span>
        {selectedIds.length ? (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-500 px-1 text-[10px] text-white">
            {selectedIds.length}
          </span>
        ) : null}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setOpen(false)} />
          <div
            role="dialog"
            aria-label="选择智能小Q数据集"
            className="fixed left-1/2 top-20 z-50 w-[min(560px,calc(100vw-24px))] -translate-x-1/2 rounded-lg border bg-white shadow-xl"
            style={{ borderColor: "#e6ecf6" }}
          >
            <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: "#eef1f8" }}>
              <div>
                <div className="text-[14px] font-semibold text-slate-800">数据反问</div>
                <div className="mt-0.5 text-[11px] text-slate-400">选择本轮使用的 Quick BI 智能小Q数据集</div>
              </div>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                title="关闭"
                onClick={() => setOpen(false)}
              >
                <X size={15} />
              </button>
            </div>

            <div className="px-4 py-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="min-w-0 text-[12px] text-slate-500">
                  已选 {draft.length} 个
                  {draft.length ? (
                    <span className="ml-1 text-slate-400">{items.filter((d) => draft.includes(d.cube_id)).map((d) => d.name).join("、")}</span>
                  ) : null}
                </div>
                <button type="button" className="flex items-center gap-1 text-[12px] text-slate-400 hover:text-blue-500" onClick={load}>
                  <RefreshCcw size={13} />刷新
                </button>
              </div>

              <div className="max-h-[360px] overflow-y-auto rounded-md border" style={{ borderColor: "#eef1f8" }}>
                {loading ? (
                  <div className="px-3 py-8 text-center text-[12px] text-slate-400">加载中…</div>
                ) : items.length ? (
                  items.map((d) => (
                    <label
                      key={d.cube_id}
                      className="flex cursor-pointer items-start gap-3 border-b px-3 py-2.5 last:border-b-0 hover:bg-slate-50"
                      style={{ borderColor: "#f3f5fa" }}
                    >
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={selectedSet.has(d.cube_id)}
                        onChange={() => toggle(d.cube_id)}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium text-slate-700">{d.name}</span>
                        {d.theme ? <span className="mt-0.5 block truncate text-[11px] text-slate-400">{d.theme}</span> : null}
                      </span>
                    </label>
                  ))
                ) : (
                  <div className="px-3 py-8 text-center text-[12px] text-slate-400">暂无可用数据集</div>
                )}
              </div>

              {selectedNames.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selectedNames.map((name) => (
                    <span key={name} className="rounded bg-blue-50 px-2 py-1 text-[11px] text-blue-600">{name}</span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="flex items-center justify-end gap-2 border-t px-4 py-3" style={{ borderColor: "#eef1f8" }}>
              <button type="button" className="qq-btn px-3 py-1.5 text-[12px]" onClick={() => { setDraft([]); onChange([], []); setOpen(false); }}>
                清空
              </button>
              <button type="button" className="qq-btn px-3 py-1.5 text-[12px]" onClick={() => setOpen(false)}>
                取消
              </button>
              <button type="button" className="qq-btn-primary flex items-center gap-1 px-3 py-1.5 text-[12px]" onClick={confirm}>
                <Check size={13} />确认
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
