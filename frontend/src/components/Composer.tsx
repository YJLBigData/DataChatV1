import { useEffect, useRef, useState } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  loading: boolean;
  placeholder?: string;
  onAbort?: () => void;
  forceRefresh: boolean;
  onToggleForceRefresh: (v: boolean) => void;
}

export function Composer({ value, onChange, onSubmit, disabled, loading, placeholder, onAbort, forceRefresh, onToggleForceRefresh }: Props) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(160, Math.max(44, el.scrollHeight)) + "px";
  }, [value]);

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (composing) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && !loading && value.trim()) onSubmit();
    }
  }

  return (
    <div className="border-t bg-white py-3" style={{ borderColor: "#eef1f8", paddingLeft: 80, paddingRight: 80 }}>
      <div className="w-full">
        <div className="qq-card flex items-end gap-2 px-3 py-2">
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            placeholder={placeholder || "把经营问题告诉我（Enter 发送，Shift+Enter 换行）"}
            className="max-h-40 min-h-[44px] flex-1 resize-none border-0 bg-transparent px-2 py-2 text-sm leading-6 text-slate-700 outline-none placeholder:text-slate-300"
          />
          {loading ? (
            <button className="qq-btn" onClick={onAbort} title="终止本次问数">
              <span className="text-rose-500">⏹</span>
              <span className="text-rose-500">停止</span>
            </button>
          ) : (
            <button
              className="qq-btn-primary disabled:cursor-not-allowed disabled:opacity-50"
              onClick={onSubmit}
              disabled={disabled || !value.trim()}
              title="发送（Enter）"
            >
              <span>发送</span>
            </button>
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between px-1 text-[11px] text-slate-400">
          <label className="flex cursor-pointer items-center gap-1.5 select-none">
            <input
              type="checkbox"
              checked={forceRefresh}
              onChange={(e) => onToggleForceRefresh(e.target.checked)}
              className="h-3 w-3 accent-blue-500"
            />
            <span>不使用缓存（每次都重新计算）</span>
          </label>
          <span>智能问数仅生成 SELECT，所有结果可解释、可追溯</span>
        </div>
      </div>
    </div>
  );
}
