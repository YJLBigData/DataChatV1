import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ChartMode } from "../types";
import type { ChartModeMeta } from "../utils/chartDetect";
import { ChartThumbnail } from "./ChartThumbnail";

interface Props {
  modes: ChartModeMeta[];
  current: ChartMode;
  onChange: (mode: ChartMode) => void;
}

/**
 * 集中图表按钮：点开后展示 14 个图表卡片，灰色 = 当前数据不支持。
 *
 * 关键点：菜单用 React Portal 渲染到 document.body，避免被 AnswerCard
 * 外层的 overflow-y-auto 滚动容器裁剪。位置随按钮的 getBoundingClientRect()
 * 计算，超出视口底部时自动向上翻。
 */
export function ChartSwitcher({ modes, current, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; right: number; placeAbove: boolean }>(
    { top: 0, right: 0, placeAbove: false },
  );

  const enabledCount = modes.filter((m) => m.enabled).length;
  const currentLabel = modes.find((m) => m.id === current)?.label || "列表";

  /** 计算菜单位置：默认在按钮下方对齐右侧；底部空间不够则翻到上方。 */
  const reposition = () => {
    const btn = btnRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const margin = 8;
    const popHeight = 360;     // 估算高度，足够放 14 个卡片
    const spaceBelow = window.innerHeight - rect.bottom;
    const placeAbove = spaceBelow < popHeight + margin;
    const top = placeAbove ? Math.max(8, rect.top - margin - popHeight) : rect.bottom + margin;
    const right = Math.max(8, window.innerWidth - rect.right);
    setPos({ top, right, placeAbove });
  };

  useLayoutEffect(() => {
    if (open) reposition();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onScroll = () => reposition();
    const onResize = () => reposition();
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node;
      if (btnRef.current?.contains(target)) return;
      if (popRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    window.addEventListener("pointerdown", onPointer);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  return (
    <div className="inline-block">
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-xl border bg-white px-3 py-1.5 text-xs text-slate-600 hover:border-blue-200 hover:text-blue-600"
        style={{ borderColor: "#e6ecf6" }}
        title="选择图表样式"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="11" width="4" height="10" />
          <rect x="10" y="6" width="4" height="15" />
          <rect x="17" y="14" width="4" height="7" />
        </svg>
        <span>图表</span>
        <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600">{currentLabel}</span>
        <span className="text-[10px] text-slate-400">{enabledCount}/{modes.length}</span>
        <svg width="10" height="10" viewBox="0 0 12 12" className={open ? "rotate-180 transition" : "transition"}>
          <path d="M2 4l4 4 4-4" stroke="currentColor" fill="none" strokeWidth="1.6" />
        </svg>
      </button>

      {open && createPortal(
        <div
          ref={popRef}
          role="menu"
          className="rounded-2xl border bg-white p-3 shadow-xl"
          style={{
            position: "fixed",
            top: pos.top,
            right: pos.right,
            width: "min(560px, 86vw)",
            maxHeight: "min(60vh, 460px)",
            overflowY: "auto",
            borderColor: "#e6ecf6",
            zIndex: 9999,
          }}
        >
          <div className="mb-2 flex items-center justify-between px-1 text-[11px]">
            <span className="font-semibold uppercase tracking-wider text-slate-400">图表中心</span>
            <span className="text-slate-400">默认列表 · 灰色表示当前数据不支持</span>
          </div>
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            {modes.map((m) => {
              const active = m.id === current;
              const disabled = !m.enabled;
              return (
                <button
                  key={m.id}
                  type="button"
                  disabled={disabled}
                  title={disabled ? m.reason : m.description}
                  onClick={() => {
                    if (disabled) return;
                    onChange(m.id);
                    setOpen(false);
                  }}
                  className={`flex items-start gap-2.5 rounded-xl border px-2.5 py-2 text-left transition ${
                    disabled
                      ? "cursor-not-allowed border-slate-100 bg-slate-50 text-slate-300"
                      : active
                      ? "border-blue-200 bg-blue-50 text-blue-600"
                      : "border-slate-200 bg-white text-slate-600 hover:border-blue-200 hover:text-blue-600"
                  }`}
                >
                  <div className="mt-0.5 flex h-7 w-11 shrink-0 items-center justify-center rounded-md bg-white/70" style={{ border: "1px solid #eef1f8" }}>
                    <ChartThumbnail mode={m.id} active={active} disabled={disabled} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold">{m.label}</span>
                      {active ? (
                        <svg width="10" height="10" viewBox="0 0 12 12">
                          <path d="M2 6.5l3 3 5-6" stroke="currentColor" fill="none" strokeWidth="1.8" />
                        </svg>
                      ) : null}
                    </div>
                    <div className="mt-0.5 text-[10px] leading-4 text-inherit/70 line-clamp-2">
                      {disabled ? m.reason : m.description}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
