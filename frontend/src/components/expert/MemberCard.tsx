/** 专家/技能成员卡片 —— 可勾选参与 + hover 行内编辑/删除（内置删=隐藏，可还原）。 */
import type { ExpertCard } from "../../types";

export function MemberCard({ m, selected, onToggle, onEdit, onDelete }: {
  m: ExpertCard; selected: boolean;
  onToggle: () => void; onEdit: () => void; onDelete: () => void;
}) {
  return (
    <div
      onClick={onToggle}
      className={`group relative flex cursor-pointer items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left transition ${
        selected ? "border-blue-300 bg-blue-50/60" : "border-slate-100 bg-white hover:border-slate-200"
      }`}
    >
      <div className="text-xl leading-none">{m.emoji}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13px] font-medium text-slate-800">{m.name}</span>
          {!m.is_builtin ? <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-600">自建</span> : null}
          {m.has_override ? <span className="rounded bg-violet-100 px-1 text-[10px] text-violet-600">已改</span> : null}
          {selected ? <span className="text-[11px] text-blue-500">✓</span> : null}
        </div>
        <div className="mt-0.5 text-[11.5px] text-slate-500">{m.profession}</div>
      </div>
      {/* 行内操作：编辑 / 删除（hover 显现），stopPropagation 避免触发勾选 */}
      <div className="absolute right-2 top-2 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
        <button type="button" title="编辑" onClick={(e) => { e.stopPropagation(); onEdit(); }}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-500">✎</button>
        {m.deletable !== false ? (
          <button type="button" title={m.is_builtin ? "隐藏（可还原）" : "删除"} onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-500">🗑</button>
        ) : null}
      </div>
    </div>
  );
}
