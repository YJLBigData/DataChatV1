import { Check, Database, Lock, RefreshCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, friendlyError } from "../api";
import { toast } from "../shared/toast";
import type { SmartQDataset } from "../types";

/**
 * 顶部「数据范围」选择器（模型配置左侧）。
 *
 * 两种数据范围：
 *   · 飞鹤小Q 数据库（默认）—— 内置数据库智能问数（NL2SQL），selectedIds 为空即此态；
 *   · 智能小Q 数据集（Quick BI）—— 选择一个/多个 SmartQ 数据集，selectedIds 非空即此态。
 *
 * 规则：数据范围只在「新建对话」时可切换；对话一旦开始（当前窗口已有消息）即锁定，
 * 由父级传入 `locked` 控制（基于活动会话是否已有 turns）。
 */
interface Props {
  selectedIds: string[];
  onChange: (ids: string[], datasets: SmartQDataset[]) => void;
  /** 对话进行中 → 锁定切换（仅展示当前范围，禁止打开/修改）。 */
  locked?: boolean;
}

type Scope = "feihe" | "smartq";

export function SmartQDatasetButton({ selectedIds, onChange, locked = false }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<SmartQDataset[]>([]);
  // 当前生效的数据范围（由 selectedIds 推导）。
  const currentScope: Scope = selectedIds.length ? "smartq" : "feihe";
  // 弹窗内的草稿：范围 + 已勾选数据集。
  const [draftScope, setDraftScope] = useState<Scope>(currentScope);
  const [draft, setDraft] = useState<string[]>(selectedIds);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  const selectedSet = useMemo(() => new Set(draft), [draft]);

  // 键盘可达性（审计 P2，对齐导出队列弹窗）：打开时 Esc 关闭，并把焦点移到关闭按钮，
  // 避免焦点被困在页面里 / 遮罩挡住后续点击。
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); }
    };
    window.addEventListener("keydown", onKey);
    closeBtnRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

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
    if (locked) return;  // 对话进行中：禁止打开
    setDraftScope(currentScope);
    setDraft(selectedIds);
    setOpen(true);
    if (currentScope === "smartq" || selectedIds.length) void load();
  }, [locked, currentScope, load, selectedIds]);

  // 切到「智能小Q」范围时按需加载数据集列表。
  const pickScope = (scope: Scope) => {
    setDraftScope(scope);
    if (scope === "smartq" && items.length === 0) void load();
  };

  const toggle = (id: string) => {
    setDraft((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const confirm = () => {
    if (draftScope === "feihe") {
      onChange([], []);          // 切回飞鹤小Q数据库
      setOpen(false);
      return;
    }
    if (draft.length === 0) {
      toast.error("请至少选择一个智能小Q数据集，或切换到飞鹤小Q数据库");
      return;
    }
    const picked = items.filter((d) => draft.includes(d.cube_id));
    onChange(draft, picked);
    setOpen(false);
  };

  // 按钮上展示当前生效范围。
  const scopeLabel = currentScope === "smartq"
    ? `智能小Q · ${selectedIds.length} 个数据集`
    : "飞鹤小Q数据库";

  return (
    <div className="relative">
      <button
        type="button"
        disabled={locked}
        className={`flex h-8 items-center gap-1.5 rounded-lg border px-2 text-[12px] ${
          locked
            ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-400"
            : currentScope === "smartq"
              ? "border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100"
              : "text-slate-500 hover:bg-slate-50"
        }`}
        title={locked
          ? "对话进行中不可切换数据范围，请新建对话后再切换"
          : "选择本轮对话的数据范围（飞鹤小Q数据库 / 智能小Q数据集）"}
        onClick={openDialog}
      >
        {locked ? <Lock size={13} strokeWidth={1.8} /> : <Database size={14} strokeWidth={1.8} />}
        <span>数据范围</span>
        <span className="text-slate-300">·</span>
        <span className={`max-w-[150px] truncate ${currentScope === "smartq" ? "font-medium" : ""}`}>{scopeLabel}</span>
      </button>

      {open && !locked && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setOpen(false)} />
          <div
            role="dialog"
            aria-label="选择数据范围"
            className="fixed left-1/2 top-20 z-50 w-[min(560px,calc(100vw-24px))] -translate-x-1/2 rounded-lg border bg-white shadow-xl"
            style={{ borderColor: "#e6ecf6" }}
          >
            <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: "#eef1f8" }}>
              <div>
                <div className="text-[14px] font-semibold text-slate-800">数据范围</div>
                <div className="mt-0.5 text-[11px] text-slate-400">选择本轮对话的数据来源（仅新建对话时可切换）</div>
              </div>
              <button
                ref={closeBtnRef}
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                title="关闭（Esc）"
                aria-label="关闭"
                onClick={() => setOpen(false)}
              >
                <X size={15} />
              </button>
            </div>

            <div className="space-y-2 px-4 py-3">
              {/* 范围一：飞鹤小Q 数据库（默认） */}
              <label
                className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 ${
                  draftScope === "feihe" ? "border-blue-300 bg-blue-50/60" : "hover:bg-slate-50"
                }`}
                style={{ borderColor: draftScope === "feihe" ? undefined : "#eef1f8" }}
              >
                <input type="radio" className="mt-1" name="datascope" checked={draftScope === "feihe"} onChange={() => pickScope("feihe")} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-slate-700">飞鹤小Q 数据库</span>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">默认</span>
                  </span>
                  <span className="mt-0.5 block text-[11px] text-slate-400">使用飞鹤内置数据库智能问数（NL2SQL）</span>
                </span>
              </label>

              {/* 范围二：智能小Q 数据集（Quick BI） */}
              <label
                className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 ${
                  draftScope === "smartq" ? "border-blue-300 bg-blue-50/60" : "hover:bg-slate-50"
                }`}
                style={{ borderColor: draftScope === "smartq" ? undefined : "#eef1f8" }}
              >
                <input type="radio" className="mt-1" name="datascope" checked={draftScope === "smartq"} onChange={() => pickScope("smartq")} />
                <span className="min-w-0 flex-1">
                  <span className="text-[13px] font-medium text-slate-700">智能小Q 数据集（Quick BI）</span>
                  <span className="mt-0.5 block text-[11px] text-slate-400">选择一个或多个 Quick BI 智能小Q 数据集问数</span>
                </span>
              </label>

              {/* 智能小Q 选中后：数据集多选列表 */}
              {draftScope === "smartq" && (
                <div className="rounded-md border" style={{ borderColor: "#eef1f8" }}>
                  <div className="flex items-center justify-between border-b px-3 py-2" style={{ borderColor: "#f3f5fa" }}>
                    <span className="text-[12px] text-slate-500">
                      已选 {draft.length} 个
                      {draft.length ? (
                        <span className="ml-1 text-slate-400">{items.filter((d) => draft.includes(d.cube_id)).map((d) => d.name).join("、")}</span>
                      ) : null}
                    </span>
                    <button type="button" className="flex items-center gap-1 text-[12px] text-slate-400 hover:text-blue-500" onClick={load}>
                      <RefreshCcw size={13} />刷新
                    </button>
                  </div>
                  <div className="max-h-[300px] overflow-y-auto">
                    {loading ? (
                      <div className="px-3 py-8 text-center text-[12px] text-slate-400">加载中…</div>
                    ) : items.length ? (
                      items.map((d) => (
                        <label
                          key={d.cube_id}
                          className="flex cursor-pointer items-start gap-3 border-b px-3 py-2.5 last:border-b-0 hover:bg-slate-50"
                          style={{ borderColor: "#f3f5fa" }}
                        >
                          <input type="checkbox" className="mt-1" checked={selectedSet.has(d.cube_id)} onChange={() => toggle(d.cube_id)} />
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
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t px-4 py-3" style={{ borderColor: "#eef1f8" }}>
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
