import { useEffect, useRef, useState } from "react";

/**
 * 项目内统一模态组件 —— 替代浏览器原生 prompt() / confirm()。
 * 原生弹窗在企业级后台不可控（样式不统一），且在自动化/无头环境直接抛
 * "prompt() is not supported"。这里提供 Modal 外壳 + Confirm / Prompt 两种对话框。
 */
export function Modal({ open, onClose, children, width = 380 }: {
  open: boolean; onClose: () => void; children: React.ReactNode; width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 px-4 backdrop-blur-sm" onClick={onClose}>
      <div className="qq-card px-5 py-4" style={{ width, maxWidth: "100%" }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

/** 确认对话框（替代原生 confirm）。 */
export function ConfirmDialog({ open, title, message, confirmText = "确定", cancelText = "取消", danger, onConfirm, onCancel }: {
  open: boolean; title: string; message?: string; confirmText?: string; cancelText?: string;
  danger?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal open={open} onClose={onCancel}>
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      {message && <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{message}</div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{ borderColor: "#e6ecf6" }} onClick={onCancel}>
          {cancelText}
        </button>
        <button
          className={
            danger
              ? "rounded-xl bg-rose-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-rose-700"
              : "qq-btn-primary !px-4 !py-1.5 text-xs"
          }
          onClick={onConfirm}
        >
          {confirmText}
        </button>
      </div>
    </Modal>
  );
}

/** 文本输入对话框（替代原生 prompt）。回车提交，空白不提交。 */
export function PromptDialog({ open, title, label, defaultValue = "", placeholder, confirmText = "确定", onSubmit, onCancel }: {
  open: boolean; title: string; label?: string; defaultValue?: string; placeholder?: string;
  confirmText?: string; onSubmit: (value: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (open) { setValue(defaultValue); setTimeout(() => inputRef.current?.focus(), 0); }
  }, [open, defaultValue]);
  function submit() { const v = value.trim(); if (v) onSubmit(v); }
  return (
    <Modal open={open} onClose={onCancel}>
      <div className="text-sm font-semibold text-slate-800">{title}</div>
      {label && <label className="mt-2 block text-xs text-slate-500">{label}</label>}
      <input
        ref={inputRef}
        className="mt-1.5 w-full rounded-lg border bg-white px-3 py-2 text-sm"
        style={{ borderColor: "#e6ecf6" }}
        value={value}
        placeholder={placeholder}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
      />
      <div className="mt-4 flex justify-end gap-2">
        <button className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{ borderColor: "#e6ecf6" }} onClick={onCancel}>
          取消
        </button>
        <button className="qq-btn-primary !px-4 !py-1.5 text-xs" onClick={submit}>{confirmText}</button>
      </div>
    </Modal>
  );
}
