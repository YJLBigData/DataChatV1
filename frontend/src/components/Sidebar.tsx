import type { AuthUser, PageId } from "../types";

interface Props {
  user: AuthUser;
  current: PageId;
  onChange: (page: PageId) => void;
}

interface Item {
  id: PageId;
  label: string;
  iconPath: string;        // svg path
  adminOnly?: boolean;
}

const ITEMS: Item[] = [
  { id: "chat",             label: "问数",     iconPath: "M4 4h16v12H7l-3 3z" },
  // 用户隔离的功能（普通用户也能用，看到的内容按 user_id 隔离）
  { id: "report_templates", label: "报告模板", iconPath: "M6 2h9l5 5v15H6z M15 2v5h5" },
  // 仅 admin 可见的管理类入口
  { id: "semantic",         label: "语义层",   iconPath: "M5 8l7-4 7 4M5 8v8l7 4 7-4V8M5 8l7 4 7-4", adminOnly: true },
  { id: "logs",             label: "日志",     iconPath: "M4 5h16M4 11h16M4 17h10",                 adminOnly: true },
  { id: "permissions",      label: "数据权限", iconPath: "M12 2l8 4v6c0 5-3.4 9-8 10-4.6-1-8-5-8-10V6z", adminOnly: true },
  { id: "users",            label: "用户管理", iconPath: "M16 11a4 4 0 100-8 4 4 0 000 8zm-8 0a4 4 0 100-8 4 4 0 000 8zm0 2c-3 0-6 1.5-6 4v3h12v-3c0-2.5-3-4-6-4zm8 0c-.7 0-1.4.1-2 .3 1.8 1 3 2.4 3 3.7v3h7v-3c0-2.5-4.4-4-8-4z", adminOnly: true },
  { id: "llm_settings",     label: "LLM 设置", iconPath: "M12 2l1.5 4.5L18 8l-4.5 1.5L12 14l-1.5-4.5L6 8l4.5-1.5z M12 14v8 M8 22h8", adminOnly: true },
];

/** 左侧主导航。普通用户只看 chat，管理员看全部。 */
export function Sidebar({ user, current, onChange }: Props) {
  const items = ITEMS.filter((i) => !i.adminOnly || user.role === "admin");
  return (
    <aside
      className="flex w-[64px] shrink-0 flex-col items-center gap-1 border-r bg-white py-4"
      style={{ borderColor: "#eef1f8" }}
    >
      <div className="qq-avatar !h-9 !w-9 !rounded-xl !text-base">Q</div>
      <div className="my-3 h-px w-8 bg-slate-100" />
      {items.map((it) => {
        const active = it.id === current;
        return (
          <button
            key={it.id}
            type="button"
            onClick={() => onChange(it.id)}
            title={it.label}
            className={`relative flex h-12 w-12 flex-col items-center justify-center gap-0.5 rounded-xl text-[10px] transition ${
              active
                ? "bg-blue-50 text-blue-600"
                : "text-slate-500 hover:bg-slate-50 hover:text-slate-700"
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d={it.iconPath} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>{it.label}</span>
          </button>
        );
      })}
    </aside>
  );
}
