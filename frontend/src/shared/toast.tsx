/**
 * 轻量全局 toast —— 替代原生 alert/confirm 的提示部分（企业 UI 不该用浏览器原生阻塞弹窗）。
 *
 * 用法：
 *   import { toast } from "../shared/toast";
 *   toast.error("保存失败"); toast.success("已保存"); toast.info("正在处理…");
 * 在应用根挂一次 <Toaster />（见 App.tsx）。无依赖、纯事件订阅。
 */
import { useEffect, useState } from "react";

export type ToastKind = "error" | "success" | "info";
export interface ToastItem { id: string; kind: ToastKind; message: string }

type Listener = (items: ToastItem[]) => void;
let items: ToastItem[] = [];
const listeners = new Set<Listener>();

function emit() { for (const l of listeners) l(items); }

function dismiss(id: string) {
  items = items.filter((t) => t.id !== id);
  emit();
}

function push(kind: ToastKind, message: string, ttl: number) {
  const id = Math.random().toString(36).slice(2) + Date.now().toString(36);
  items = [...items, { id, kind, message: String(message ?? "") }];
  emit();
  window.setTimeout(() => dismiss(id), ttl);
  return id;
}

export const toast = {
  error: (m: string) => push("error", m, 5000),
  success: (m: string) => push("success", m, 3000),
  info: (m: string) => push("info", m, 3500),
  dismiss,
};

const STYLE: Record<ToastKind, string> = {
  error: "bg-rose-600 text-white",
  success: "bg-emerald-600 text-white",
  info: "bg-slate-800 text-white",
};

export function Toaster() {
  const [list, setList] = useState<ToastItem[]>(items);
  useEffect(() => {
    listeners.add(setList);
    setList(items);
    return () => { listeners.delete(setList); };
  }, []);
  if (!list.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex max-w-[min(92vw,360px)] flex-col gap-2">
      {list.map((t) => (
        <div
          key={t.id}
          role="status"
          onClick={() => dismiss(t.id)}
          className={`cursor-pointer rounded-xl px-4 py-2.5 text-[13px] leading-snug shadow-lg ${STYLE[t.kind]}`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
