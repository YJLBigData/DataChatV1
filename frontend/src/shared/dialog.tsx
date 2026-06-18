/**
 * 命令式确认/输入对话框 —— 替代浏览器原生 confirm()/prompt()（企业 UI 不用原生阻塞弹窗）。
 *
 * 用法（在 async 函数里 await）：
 *   if (!(await confirmDialog({ message: "确定删除？", danger: true }))) return;
 *   const name = await promptDialog({ label: "新名称", defaultValue: cur });
 *   if (name == null) return; // 用户取消
 * 在应用根挂一次 <DialogHost />（见 App.tsx）。
 */
import { useEffect, useState } from "react";

type ConfirmOpts = { title?: string; message: string; confirmText?: string; cancelText?: string; danger?: boolean };
type PromptOpts = { title?: string; label?: string; defaultValue?: string; placeholder?: string; confirmText?: string };

type Req =
  | { kind: "confirm"; id: number; opts: ConfirmOpts; resolve: (v: boolean) => void }
  | { kind: "prompt"; id: number; opts: PromptOpts; resolve: (v: string | null) => void };

let _seq = 0;
let _listener: ((r: Req | null) => void) | null = null;

export function confirmDialog(opts: ConfirmOpts): Promise<boolean> {
  return new Promise((resolve) => {
    if (!_listener) { resolve(window.confirm(opts.message)); return; }
    _listener({ kind: "confirm", id: ++_seq, opts, resolve });
  });
}

export function promptDialog(opts: PromptOpts): Promise<string | null> {
  return new Promise((resolve) => {
    if (!_listener) { resolve(window.prompt(opts.label || opts.title || "", opts.defaultValue || "")); return; }
    _listener({ kind: "prompt", id: ++_seq, opts, resolve });
  });
}

export function DialogHost() {
  const [req, setReq] = useState<Req | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    _listener = (r) => { setReq(r); if (r?.kind === "prompt") setText(r.opts.defaultValue || ""); };
    return () => { _listener = null; };
  }, []);

  if (!req) return null;

  const close = (val: boolean | string | null) => {
    if (req.kind === "confirm") req.resolve(val as boolean);
    else req.resolve(val as string | null);
    setReq(null);
  };

  const isConfirm = req.kind === "confirm";
  const title = req.opts.title || (isConfirm ? "确认" : "输入");

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-900/30 backdrop-blur-sm p-4"
      onClick={() => close(isConfirm ? false : null)}>
      <div className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 text-[15px] font-semibold text-slate-800">{title}</div>
        {isConfirm ? (
          <div className="whitespace-pre-line text-[13px] leading-relaxed text-slate-600">{req.opts.message}</div>
        ) : (
          <div className="space-y-1.5">
            {req.opts.label && <div className="text-[12px] text-slate-500">{req.opts.label}</div>}
            <input
              autoFocus value={text} placeholder={req.opts.placeholder || ""}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") close(text); }}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-300"
            />
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button className="qq-btn px-3 py-1.5 text-sm" onClick={() => close(isConfirm ? false : null)}>
            {(req.opts as ConfirmOpts).cancelText || "取消"}
          </button>
          <button
            className={`px-3 py-1.5 text-sm rounded-xl text-white ${isConfirm && (req.opts as ConfirmOpts).danger ? "bg-rose-500 hover:bg-rose-600" : "qq-btn-primary"}`}
            onClick={() => close(isConfirm ? true : text)}
          >
            {req.opts.confirmText || (isConfirm ? "确定" : "确定")}
          </button>
        </div>
      </div>
    </div>
  );
}
