import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { ConfirmDialog, PromptDialog } from "./Modal";
import type { ConversationMeta, Folder } from "../types";

/** 当前打开的对话框（统一 Modal，替代原生 prompt/confirm）。 */
type Dialog =
  | { kind: "create-folder" }
  | { kind: "rename-folder"; folder: Folder }
  | { kind: "delete-folder"; folder: Folder }
  | { kind: "rename-conv"; conv: ConversationMeta }
  | { kind: "delete-conv"; conv: ConversationMeta };

interface Props {
  items: ConversationMeta[];
  activeId: string | null;
  onPick: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  collapsed: boolean;
  onToggle: () => void;
  /** 当前有结果但用户没切回去看的对话 — 用于显示红点。 */
  unreadCids?: Set<string>;
  /** 当前正在 streaming 的对话 — 显示小转圈。 */
  streamingCids?: Set<string>;
}

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return d.toTimeString().slice(0, 5);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * 会话历史 + 文件夹 — 两种视图：「全部会话」/ 每个文件夹。
 * 鼠标悬停时显示「收藏到 / 移出 / 改名 / 删除」操作。
 */
export function ConversationList({ items, activeId, onPick, onNew, onRename, onDelete, collapsed, onToggle, unreadCids, streamingCids }: Props) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [foldersOf, setFoldersOf] = useState<Record<string, string[]>>({}); // conv_id -> folder_ids
  const [activeFolderId, setActiveFolderId] = useState<string | null>(null);  // null = 全部
  const [folderConversations, setFolderConversations] = useState<ConversationMeta[]>([]);
  const [collectFor, setCollectFor] = useState<ConversationMeta | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);

  async function refreshFolders() {
    try {
      const r = await api.listFolders();
      setFolders(r.items || []);
    } catch { /* ignore */ }
  }
  useEffect(() => { refreshFolders(); }, []);

  // 切到某个文件夹 → 拉它下面的会话
  useEffect(() => {
    if (!activeFolderId) { setFolderConversations([]); return; }
    (async () => {
      try {
        const r = await api.folderConversations(activeFolderId);
        setFolderConversations((r.items as any) || []);
      } catch { setFolderConversations([]); }
    })();
  }, [activeFolderId]);

  // 后台批量查询每个会话被收藏到哪些文件夹（用于显示星标）
  useEffect(() => {
    (async () => {
      const map: Record<string, string[]> = {};
      for (const it of items.slice(0, 50)) {
        try {
          const r = await api.conversationFolderIds(it.id);
          if (r.folder_ids?.length) map[it.id] = r.folder_ids;
        } catch { /* ignore */ }
      }
      setFoldersOf(map);
    })();
  }, [items]);

  const shown = useMemo<ConversationMeta[]>(() => {
    if (activeFolderId) return folderConversations;
    return items;
  }, [activeFolderId, folderConversations, items]);

  // 文件夹增删改：用统一 Modal（见底部 dialog 渲染），不再用原生 prompt/confirm。
  function createFolder() {
    const name = newFolderName.trim();
    if (name) { void doCreateFolder(name); } else { setDialog({ kind: "create-folder" }); }
  }
  async function doCreateFolder(name: string) {
    try {
      await api.createFolder(name);
      setNewFolderName("");
      await refreshFolders();
    } catch (e: any) { alert("失败：" + (e?.message || e)); }
  }
  async function doDeleteFolder(f: Folder) {
    try {
      await api.deleteFolder(f.id);
      if (activeFolderId === f.id) setActiveFolderId(null);
      await refreshFolders();
    } catch (e: any) { alert("失败：" + (e?.message || e)); }
  }
  async function doRenameFolder(f: Folder, name: string) {
    try { await api.renameFolder(f.id, name); await refreshFolders(); }
    catch (e: any) { alert("失败：" + (e?.message || e)); }
  }

  if (collapsed) {
    return (
      <div className="flex w-12 flex-col items-center gap-2 border-r bg-white py-3" style={{ borderColor: "#eef1f8" }}>
        <button className="qq-btn-ghost" title="展开会话列表" onClick={onToggle}><span className="text-lg">»</span></button>
        <button className="qq-btn-ghost" title="新建会话" onClick={onNew}><span className="text-lg text-blue-500">+</span></button>
      </div>
    );
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r bg-white" style={{ borderColor: "#eef1f8" }}>
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3" style={{ borderColor: "#eef1f8" }}>
        <div className="text-sm font-semibold text-slate-700">会话历史</div>
        <div className="flex items-center gap-1">
          <button className="qq-btn-ghost px-2 py-1" title="新建会话" onClick={onNew}><span className="text-base text-blue-500">+ 新建</span></button>
          <button className="qq-btn-ghost px-1.5 py-1" title="收起" onClick={onToggle}><span className="text-base">«</span></button>
        </div>
      </div>

      {/* 文件夹标签 */}
      <div className="border-b px-2 py-2" style={{ borderColor: "#eef1f8" }}>
        <div className="mb-1.5 flex items-center justify-between px-1 text-[10px] uppercase tracking-wide text-slate-400">
          <span>📁 文件夹</span>
          <button onClick={createFolder} title="新建文件夹" className="text-blue-500 hover:underline">+</button>
        </div>
        <div className="flex flex-wrap gap-1">
          <FolderChip label="全部"  active={activeFolderId===null} onClick={()=>setActiveFolderId(null)} />
          {folders.map((f) => (
            <FolderChip
              key={f.id}
              label={f.name}
              active={activeFolderId === f.id}
              onClick={()=>setActiveFolderId(f.id)}
              onRename={()=>setDialog({ kind: "rename-folder", folder: f })}
              onDelete={()=>setDialog({ kind: "delete-folder", folder: f })}
            />
          ))}
        </div>
      </div>

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {shown.length === 0 ? (
          <div className="px-3 py-6 text-xs text-slate-400">
            {activeFolderId ? "该文件夹暂无收藏" : "暂无历史会话，开个新对话开始问数 →"}
          </div>
        ) : (
          shown.map((it) => {
            const collected = !!foldersOf[it.id]?.length;
            const unread = !!unreadCids?.has(it.id);
            const streamingHere = !!streamingCids?.has(it.id);
            return (
              <div
                key={it.id}
                className={`group mb-1 flex cursor-pointer items-start gap-2 rounded-xl px-3 py-2 ${
                  activeId === it.id ? "bg-blue-50/80" : "hover:bg-slate-50"
                }`}
                onClick={() => onPick(it.id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1">
                    {collected && <span title="已收藏到文件夹" className="text-[11px] text-amber-500">★</span>}
                    <span className={`truncate text-sm ${activeId === it.id ? "font-semibold text-blue-700" : "text-slate-700"}`}>{it.title || "新会话"}</span>
                    {streamingHere && (
                      <span title="正在生成回答" className="ml-1 inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
                    )}
                    {!streamingHere && unread && (
                      <span title="有新结果未查看" className="ml-1 inline-flex h-1.5 w-1.5 rounded-full bg-rose-500" />
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400">{fmtTime(it.updated_at)}</div>
                </div>
                <div className="hidden flex-row items-center gap-1.5 group-hover:flex">
                  <button
                    className="text-[11px] text-amber-500 hover:text-amber-600"
                    title="收藏到文件夹"
                    onClick={(e) => { e.stopPropagation(); setCollectFor(it); }}
                  >★</button>
                  <button
                    className="rounded border px-1.5 py-0.5 text-[10px] text-slate-500 hover:border-blue-200 hover:text-blue-600"
                    style={{ borderColor: "#e6ecf6" }}
                    title="重命名会话"
                    onClick={(e) => { e.stopPropagation(); setDialog({ kind: "rename-conv", conv: it }); }}
                  >重命名</button>
                  <button
                    className="rounded border px-1.5 py-0.5 text-[10px] text-slate-500 hover:border-rose-200 hover:text-rose-600"
                    style={{ borderColor: "#e6ecf6" }}
                    title="删除会话"
                    onClick={(e) => { e.stopPropagation(); setDialog({ kind: "delete-conv", conv: it }); }}
                  >删除</button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 收藏到文件夹 弹窗 */}
      {collectFor && (
        <CollectModal
          conversation={collectFor}
          folders={folders}
          onClose={() => setCollectFor(null)}
          onChanged={async () => {
            const r = await api.conversationFolderIds(collectFor.id);
            setFoldersOf(prev => ({ ...prev, [collectFor.id]: r.folder_ids || [] }));
            if (activeFolderId) {
              const f = await api.folderConversations(activeFolderId);
              setFolderConversations((f.items as any) || []);
            }
          }}
        />
      )}

      {/* 统一对话框（替代原生 prompt/confirm） */}
      <PromptDialog
        open={dialog?.kind === "create-folder"}
        title="新建文件夹" label="文件夹名" placeholder="如：经营月报" confirmText="创建"
        onSubmit={(v) => { setDialog(null); void doCreateFolder(v); }}
        onCancel={() => setDialog(null)}
      />
      <PromptDialog
        open={dialog?.kind === "rename-folder"}
        title="重命名文件夹" label="文件夹名"
        defaultValue={dialog?.kind === "rename-folder" ? dialog.folder.name : ""}
        onSubmit={(v) => { const d = dialog; setDialog(null); if (d?.kind === "rename-folder") void doRenameFolder(d.folder, v); }}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "delete-folder"}
        title="删除文件夹" danger confirmText="删除"
        message={dialog?.kind === "delete-folder" ? `删除文件夹「${dialog.folder.name}」？（不删除原会话）` : ""}
        onConfirm={() => { const d = dialog; setDialog(null); if (d?.kind === "delete-folder") void doDeleteFolder(d.folder); }}
        onCancel={() => setDialog(null)}
      />
      <PromptDialog
        open={dialog?.kind === "rename-conv"}
        title="重命名会话" label="会话名称"
        defaultValue={dialog?.kind === "rename-conv" ? (dialog.conv.title || "") : ""}
        onSubmit={(v) => { const d = dialog; setDialog(null); if (d?.kind === "rename-conv") onRename(d.conv.id, v); }}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog?.kind === "delete-conv"}
        title="删除会话" danger confirmText="删除"
        message={dialog?.kind === "delete-conv" ? `确认删除会话「${dialog.conv.title || "新会话"}」？` : ""}
        onConfirm={() => { const d = dialog; setDialog(null); if (d?.kind === "delete-conv") onDelete(d.conv.id); }}
        onCancel={() => setDialog(null)}
      />
    </aside>
  );
}

function FolderChip({ label, active, onClick, onRename, onDelete }: {
  label: string; active: boolean; onClick: () => void; onRename?: () => void; onDelete?: () => void
}) {
  return (
    <div className="group relative">
      <button onClick={onClick}
        className={`rounded-md px-2 py-0.5 text-[11px] ${active ? "bg-blue-100 text-blue-700" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
        {label}
      </button>
      {(onRename || onDelete) && (
        <div className="absolute right-0 top-full z-10 mt-1 hidden gap-1 rounded-md bg-white px-1 py-1 text-[10px] shadow-md group-hover:flex" style={{ border: "1px solid #eef1f8" }}>
          {onRename && <button onClick={onRename} className="px-1.5 py-0.5 text-slate-500 hover:text-blue-600">重命名</button>}
          {onDelete && <button onClick={onDelete} className="px-1.5 py-0.5 text-slate-500 hover:text-rose-600">删除</button>}
        </div>
      )}
    </div>
  );
}

function CollectModal({ conversation, folders, onClose, onChanged }: {
  conversation: ConversationMeta; folders: Folder[]; onClose: () => void; onChanged: () => Promise<void> | void
}) {
  const [included, setIncluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.conversationFolderIds(conversation.id);
        setIncluded(r.folder_ids || []);
      } catch { setIncluded([]); }
    })();
  }, [conversation.id]);

  async function toggle(fid: string) {
    setBusy(true);
    try {
      if (included.includes(fid)) {
        await api.uncollectConversation(conversation.id, fid);
        setIncluded(prev => prev.filter(x => x !== fid));
      } else {
        await api.collectConversation(conversation.id, fid);
        setIncluded(prev => [...prev, fid]);
      }
      await onChanged();
    } catch (e: any) { alert("失败：" + (e?.message || e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm" onClick={onClose}>
      <div className="qq-card w-[380px] px-5 py-4" onClick={(e)=>e.stopPropagation()}>
        <div className="mb-2 text-sm font-semibold text-slate-700">收藏到文件夹</div>
        <div className="mb-3 truncate text-xs text-slate-500">「{conversation.title || "新会话"}」</div>
        {folders.length === 0 ? (
          <div className="rounded-lg bg-slate-50 px-3 py-4 text-center text-xs text-slate-400">还没有文件夹，先去左侧 + 创建一个</div>
        ) : (
          <div className="space-y-1">
            {folders.map(f => {
              const on = included.includes(f.id);
              return (
                <label key={f.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${on ? "border-blue-200 bg-blue-50" : "border-slate-200 hover:bg-slate-50"}`}>
                  <input type="checkbox" checked={on} disabled={busy} onChange={()=>toggle(f.id)} className="accent-blue-500" />
                  <span className="flex-1 text-slate-700">{f.name}</span>
                </label>
              );
            })}
          </div>
        )}
        <div className="mt-3 text-right">
          <button onClick={onClose} className="rounded-xl border px-3 py-1 text-xs text-slate-500 hover:bg-slate-50" style={{borderColor:"#e6ecf6"}}>完成</button>
        </div>
      </div>
    </div>
  );
}
