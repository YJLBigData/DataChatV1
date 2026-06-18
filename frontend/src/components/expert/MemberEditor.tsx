/** 成员编辑器弹窗 —— 新建技能 / 编辑内置专家或自建技能（内置可"还原默认"）。
 * 纯展示组件：状态与保存/还原逻辑由 ExpertPanelPage 持有，这里只渲染表单 + 派发动作。 */
import type { Dispatch, SetStateAction } from "react";

export type EditorState = {
  open: boolean;
  mode: "create" | "edit";
  id?: string;
  name: string; profession: string; emoji: string; instructions: string;
  isBuiltin: boolean; isDirector: boolean; hasOverride: boolean;
  loading: boolean; saving: boolean; err: string;
};

export const EMPTY_EDITOR: EditorState = {
  open: false, mode: "create", name: "", profession: "", emoji: "✨", instructions: "",
  isBuiltin: false, isDirector: false, hasOverride: false, loading: false, saving: false, err: "",
};

interface Props {
  editor: EditorState;
  setEditor: Dispatch<SetStateAction<EditorState>>;
  onSave: () => void;
  onReset: () => void;
  onCancel: () => void;
}

export function MemberEditor({ editor, setEditor, onSave, onReset, onCancel }: Props) {
  if (!editor.open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onClick={onCancel}>
      <div className="w-full max-w-md rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-[15px] font-semibold text-slate-800">
            {editor.mode === "create" ? "新建技能 / 专家" : editor.isDirector ? "编辑决策调度总监" : editor.isBuiltin ? "编辑内置专家" : "编辑技能"}
          </div>
          {editor.isBuiltin && editor.hasOverride ? <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] text-violet-600">已改</span> : null}
        </div>
        {editor.loading ? (
          <div className="py-10 text-center text-sm text-slate-400">加载中…</div>
        ) : (
          <div className="space-y-3">
            <div className="flex gap-2">
              <input value={editor.emoji} onChange={(e) => setEditor((s) => ({ ...s, emoji: e.target.value }))}
                className="w-16 rounded-lg border border-slate-200 px-2 py-2 text-center text-sm" placeholder="✨" />
              <input value={editor.name} onChange={(e) => setEditor((s) => ({ ...s, name: e.target.value }))}
                className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="名称，如：促销ROI专家" />
            </div>
            <input value={editor.profession} onChange={(e) => setEditor((s) => ({ ...s, profession: e.target.value }))}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="角色定位，如：促销活动效果分析" />
            <textarea value={editor.instructions} onChange={(e) => setEditor((s) => ({ ...s, instructions: e.target.value }))}
              rows={7} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="该专家的分析方法论 / 关注点 / 输出要求（作为 system 指令）" />
            {editor.err ? <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{editor.err}</div> : null}
          </div>
        )}
        <div className="mt-4 flex items-center justify-between gap-2">
          <div>
            {editor.mode === "edit" && editor.isBuiltin && editor.hasOverride ? (
              <button type="button" onClick={onReset} disabled={editor.saving}
                className="qq-btn px-3 py-1.5 text-sm text-slate-500 disabled:opacity-40">还原默认</button>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} className="qq-btn px-3 py-1.5 text-sm">取消</button>
            <button type="button" onClick={onSave} disabled={editor.saving || editor.loading || !editor.name.trim()}
              className="qq-btn-primary px-3 py-1.5 text-sm disabled:opacity-40">{editor.saving ? "保存中…" : "保存"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
