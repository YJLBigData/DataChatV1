import { useEffect, useRef, useState } from "react";

import type { AuthUser } from "../types";

interface Props {
  user: AuthUser;
  onChangePassword: () => void;
  onLogout: () => void;
}

/** 顶部右侧的头像菜单 — 修改密码 / 退出登录。 */
export function UserMenu({ user, onChangePassword, onLogout }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const fn = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", fn);
    return () => window.removeEventListener("pointerdown", fn);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className="flex items-center gap-2 rounded-xl border bg-white px-2.5 py-1.5 text-xs text-slate-600 hover:border-blue-200"
        style={{ borderColor: "#e6ecf6" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-100 text-[11px] font-semibold text-blue-700">
          {user.username.slice(0, 1).toUpperCase()}
        </span>
        <span>{user.username}</span>
        {user.role === "admin" && <span className="qq-pill-blue !px-1.5 !py-0">admin</span>}
      </button>
      {open && (
        <div
          className="absolute right-0 top-10 z-30 w-44 overflow-hidden rounded-xl border bg-white py-1 shadow-lg"
          style={{ borderColor: "#eef1f8" }}
        >
          <button
            className="block w-full px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"
            onClick={() => { setOpen(false); onChangePassword(); }}
          >
            修改我的密码
          </button>
          <button
            className="block w-full px-3 py-2 text-left text-xs text-rose-600 hover:bg-rose-50"
            onClick={() => { setOpen(false); onLogout(); }}
          >
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}
