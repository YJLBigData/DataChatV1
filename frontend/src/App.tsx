/**
 * 飞鹤小Q · 智能问数 — 应用外壳（shell）。
 *
 * 职责收敛到「组合 + 布局 + 路由」：auth/boot、左侧导航簇（主导航 + 两套会话列表）、
 * 顶部 Header（provider 下拉 + 导出队列 + 用户菜单）、按 page 切换的页面区。
 * 各页自带状态：问数→useChat + ChatPage；专家团→useExpertTeam + ExpertPanelPage；
 * 管理页→各自页面组件。App 不再持有问数流式状态机（已抽到 hooks/useChat.ts）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, auth } from "./api";
import { useLLMProviders } from "./hooks/useLLMProviders";
import { useChat } from "./hooks/useChat";
import { useExpertTeam } from "./hooks/useExpertTeam";
import { Toaster } from "./shared/toast";
import { DialogHost } from "./shared/dialog";
import { ConversationList, type FolderScope } from "./components/ConversationList";
import { ExportQueueButton } from "./components/ExportQueueButton";
import { LoginScreen } from "./components/LoginScreen";
import { PasswordModal } from "./components/PasswordModal";
import { Sidebar } from "./components/Sidebar";
import { SmartQDatasetButton } from "./components/SmartQDatasetButton";
import { UserMenu } from "./components/UserMenu";
import { ChatPage } from "./components/pages/ChatPage";
import { ExpertPanelPage } from "./components/pages/ExpertPanelPage";
import { LLMSettingsPage } from "./components/pages/LLMSettingsPage";
import { LogsPage } from "./components/pages/LogsPage";
import { PermissionsPage } from "./components/pages/PermissionsPage";
import { ReportTemplatesPage } from "./components/pages/ReportTemplatesPage";
import { SemanticPage } from "./components/pages/SemanticPage";
import { UsersPage } from "./components/pages/UsersPage";
import type { AuthUser, BootstrapInfo, PageId, SmartQDataset } from "./types";

/** 专家团文件夹/收藏接口集合（与问数完全独立的一套，传给复用的 ConversationList）。 */
const EXPERT_FOLDER_SCOPE: FolderScope = {
  listFolders: api.expertListFolders,
  createFolder: api.expertCreateFolder,
  renameFolder: api.expertRenameFolder,
  deleteFolder: api.expertDeleteFolder,
  folderConversations: api.expertFolderConversations,
  collectConversation: api.expertCollectConversation,
  uncollectConversation: api.expertUncollectConversation,
  conversationFolderIds: api.expertConversationFolderIds,
  conversationFolderIdsBatch: api.expertConversationFolderIdsBatch,
};

export default function App() {
  /* ------------------------------- auth + boot ----------------------------- */
  const [user, setUser] = useState<AuthUser | null>(() => auth.getUser());
  const [boot, setBoot] = useState<BootstrapInfo | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  /* ------------------------------- router ---------------------------------- */
  const [page, setPage] = useState<PageId>("chat");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  /** 移动端（< md）侧栏抽屉开关；桌面端常驻不受此影响。 */
  const [mobileNav, setMobileNav] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);

  /* --------------- LLM provider 切换（右上角下拉，每次 chat 请求都传） --------------- */
  const { llmProviders, llmDefault, llmChoice, setLlmChoice } = useLLMProviders(!!user);
  const [smartqCubeIds, setSmartqCubeIds] = useState<string[]>([]);
  const [smartqPickedDatasets, setSmartqPickedDatasets] = useState<SmartQDataset[]>([]);

  /* 问数 / 专家团：两套 App 级状态容器（切页/刷新不中断；红点统一管理）。 */
  const chat = useChat({ enabled: !!user, user, llmChoice, smartqCubeIds });
  const expert = useExpertTeam(!!user, { smartqCubeIds });

  /** 数据范围仅在「新建对话」（当前窗口尚无消息）时可切换；对话一旦开始即锁定。 */
  const scopeLocked = (page === "expert" ? expert.turns.length : chat.turns.length) > 0;

  /* ----------------------------- 401 handling ------------------------------ */
  const chatReset = chat.reset;
  useEffect(() => {
    const fn = () => { setUser(null); chatReset(); setSmartqCubeIds([]); setSmartqPickedDatasets([]); };
    window.addEventListener("datachat:unauthorized", fn);
    return () => window.removeEventListener("datachat:unauthorized", fn);
  }, [chatReset]);

  /* ------------------------------- bootstrap ------------------------------- */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const b = await api.bootstrap();
        if (!cancelled) setBoot(b);
      } catch (e: any) {
        if (!cancelled) setBootError(e?.message || String(e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /* ------------------------------- auth fns -------------------------------- */
  const onLogin = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    auth.setToken(r.token); auth.setUser(r.user);
    setUser(r.user);
  }, []);
  const onLogout = useCallback(() => {
    auth.clear(); setUser(null); chatReset(); setSmartqCubeIds([]); setSmartqPickedDatasets([]); setPage("chat");
  }, [chatReset]);

  const headerHealth = useMemo(() => {
    if (!boot) return null;
    // 只要有任意一项就展示下拉框（内置两条 legacy 永远在，所以基本恒为 true）。
    const hasChoices = llmProviders.length >= 1;
    const current = llmProviders.find((p) => p.id === llmChoice);
    return (
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {hasChoices ? (
          <label
            className="qq-pill-blue !cursor-pointer !py-0 !pr-1 inline-flex items-center gap-1"
            title={current?.hint || "切换大模型 provider（仅本次会话本地保存）"}
          >
            <span>🤖</span>
            <select
              className="bg-transparent text-[11px] font-medium outline-none cursor-pointer pr-1"
              value={llmChoice}
              onChange={(e) => setLlmChoice(e.target.value)}
            >
              {llmProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}{p.id === llmDefault ? "（默认）" : ""}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="qq-pill-blue" title={current?.hint || ""}>
            {current?.label || boot.model.name}
          </span>
        )}
      </div>
    );
  }, [boot, llmProviders, llmChoice, llmDefault, setLlmChoice]);

  /* ------------------------------- early states ---------------------------- */
  if (bootError) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f5f7fc]">
        <div className="qq-card max-w-md px-6 py-5 text-center">
          <div className="text-base font-semibold text-rose-600">服务无法连接</div>
          <div className="mt-2 text-xs text-slate-500">{bootError}</div>
          <button className="qq-btn-primary mt-4" onClick={() => location.reload()}>刷新页面</button>
        </div>
      </div>
    );
  }
  if (!boot) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f5f7fc]">
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <span className="qq-loading-dot" /><span className="qq-loading-dot" /><span className="qq-loading-dot" />
          <span className="ml-2">正在唤醒飞鹤小Q…</span>
        </div>
      </div>
    );
  }
  if (!user || !auth.getToken()) {
    return <LoginScreen onLogin={onLogin} />;
  }

  /* ------------------------------- main render ----------------------------- */
  return (
    <div className="flex h-full w-full bg-[#f5f7fc]">
      {/* 移动端抽屉遮罩（点击关闭） */}
      {mobileNav && (
        <div className="fixed inset-0 z-30 bg-slate-900/30 backdrop-blur-sm md:hidden" onClick={() => setMobileNav(false)} />
      )}

      {/* 导航簇（主导航 + 会话栏）：桌面常驻；移动端为 off-canvas 抽屉 */}
      <div
        className={
          "z-40 flex h-full shrink-0 transition-transform duration-200 md:static md:z-auto md:translate-x-0 " +
          (mobileNav ? "fixed inset-y-0 left-0 translate-x-0" : "fixed inset-y-0 left-0 -translate-x-full md:translate-x-0")
        }
      >
        {/* left main nav */}
        <Sidebar
          user={user}
          current={page}
          onChange={(p) => { setPage(p); setMobileNav(false); }}
          badges={{ expert: expert.unread.size > 0 }}
        />

        {page === "chat" && (
          <ConversationList
            items={chat.conversations}
            activeId={chat.activeId}
            onPick={(id) => { chat.openConversation(id); setPage("chat"); setMobileNav(false); }}
            onNew={() => { chat.startNew(); setPage("chat"); setMobileNav(false); }}
            onRename={chat.renameConversation}
            onDelete={chat.deleteConversation}
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed((v) => !v)}
            unreadCids={chat.unread}
            streamingCids={chat.streamingConvs}
          />
        )}

        {page === "expert" && (
          <ConversationList
            items={expert.conversations}
            activeId={expert.activeId}
            onPick={(id) => { expert.openConversation(id); setMobileNav(false); }}
            onNew={() => { expert.startNew(); setMobileNav(false); }}
            onRename={expert.renameConversation}
            onDelete={expert.deleteConversation}
            collapsed={sidebarCollapsed}
            onToggle={() => setSidebarCollapsed((v) => !v)}
            unreadCids={expert.unread}
            streamingCids={expert.streamingCids}
            scope={EXPERT_FOLDER_SCOPE}
            title="专家团历史"
            emptyHint="暂无专家团分析，开个新分析交给专家团 →"
          />
        )}
      </div>

      {/* page area (main) */}
      <div className="flex flex-1 min-w-0">
        <main className="flex flex-1 min-w-0 flex-col">
          <header className="flex items-center justify-between border-b bg-white px-4 py-3 sm:px-5" style={{ borderColor: "#eef1f8" }}>
            <div className="flex items-center gap-2 sm:gap-3">
              {/* 移动端汉堡按钮：打开导航抽屉 */}
              <button
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 md:hidden"
                title="菜单" onClick={() => setMobileNav(true)}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" /></svg>
              </button>
              <div className="qq-avatar !h-8 !w-8 !rounded-xl !text-base">Q</div>
              <div>
                <div className="text-[15px] font-semibold tracking-tight text-slate-800">
                  飞鹤小Q · 智能问数
                </div>
                <div className="hidden text-[11px] text-slate-400 sm:block">交给小Q，你可以相信我</div>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <SmartQDatasetButton
                selectedIds={smartqCubeIds}
                locked={scopeLocked}
                onChange={(ids, datasets) => { setSmartqCubeIds(ids); setSmartqPickedDatasets(datasets); }}
              />
              {headerHealth}
              <ExportQueueButton />
              <UserMenu user={user} onChangePassword={() => setPwdOpen(true)} onLogout={onLogout} />
            </div>
          </header>

          {page === "chat" && (
            <ChatPage chat={chat} user={user} suggestions={boot.suggestions} />
          )}

          {page === "expert" && (
            <ExpertPanelPage user={user} llmProvider={llmChoice} expert={expert} smartqDatasets={smartqPickedDatasets} />
          )}

          {/* ====================== admin pages ====================== */}
          {page === "users" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><UsersPage /></section>
          )}
          {page === "logs" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><LogsPage /></section>
          )}
          {page === "report_templates" && (
            <section className="flex-1 overflow-y-auto"><ReportTemplatesPage /></section>
          )}
          {page === "semantic" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><SemanticPage /></section>
          )}
          {page === "permissions" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><PermissionsPage /></section>
          )}
          {page === "llm_settings" && user.role === "admin" && (
            <section className="flex-1 overflow-y-auto"><LLMSettingsPage /></section>
          )}
          {(page === "logs" || page === "users" || page === "permissions" || page === "semantic" || page === "llm_settings") && user.role !== "admin" && (
            <section className="flex flex-1 items-center justify-center text-sm text-slate-400">
              该页面仅管理员可访问
            </section>
          )}
        </main>
      </div>

      <Toaster />
      <DialogHost />
      <PasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} onChanged={() => { /* noop */ }} />
    </div>
  );
}
