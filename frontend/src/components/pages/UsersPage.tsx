import { useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import type { AuthUser } from "../../types";

/** 用户管理（仅管理员）— 创建 / 列表 / 重置密码 / 删除。
 *  Hint：数据权限由「数据权限」页面单独配置，不在这里管。
 */
export function UsersPage() {
  const [items, setItems] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<"user" | "admin">("user");
  const [lastOtp, setLastOtp] = useState<{ username: string; password: string } | null>(null);

  async function refresh() {
    setLoading(true); setErr(null);
    try { setItems((await api.listUsers()).items || []); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  const filtered = useMemo(
    () => items.filter((u) => !search.trim() || u.username.toLowerCase().includes(search.trim().toLowerCase())),
    [items, search],
  );

  async function create() {
    if (!newUsername.trim()) { alert("用户名不能为空"); return; }
    if (newPassword && newPassword.length < 8) { alert("密码至少 8 位（或留空让系统随机生成）"); return; }
    try {
      const r = await api.createUser(newUsername.trim(), newPassword || null, newRole, newEmail.trim());
      setNewUsername(""); setNewPassword(""); setNewEmail(""); setNewRole("user");
      await refresh();
      if (r.one_time_password) {
        setLastOtp({ username: r.username, password: r.one_time_password });
      } else {
        alert(`已创建 ${r.username}`);
      }
    } catch (e: any) { alert("创建失败: " + (e?.message || e)); }
  }
  async function resetPwd(u: AuthUser) {
    const choice = confirm(`重置 ${u.username} 的密码：\n· 确定 = 系统随机生成强密码\n· 取消 = 我自己输入`);
    if (choice) {
      try {
        const r = await api.resetPassword(u.username, null, true);
        if (r.one_time_password) setLastOtp({ username: u.username, password: r.one_time_password });
      } catch (e: any) { alert("失败: " + (e?.message || e)); }
    } else {
      const p = prompt(`为 ${u.username} 设置新密码（至少 8 位，含字母+数字）`);
      if (!p || p.length < 8) return;
      try { await api.resetPassword(u.username, p, true); alert("已重置"); }
      catch (e: any) { alert("失败: " + (e?.message || e)); }
    }
  }
  async function remove(u: AuthUser) {
    if (u.username === "admin") { alert("不能删除默认管理员"); return; }
    if (!confirm(`确定删除用户 ${u.username} ?`)) return;
    try { await api.deleteUser(u.username); await refresh(); }
    catch (e: any) { alert("失败: " + (e?.message || e)); }
  }
  async function toggleActive(u: AuthUser) {
    const next = !(u.is_active ?? true);
    if (!next && !confirm(`确定停用 ${u.username}？停用后该用户立即无法登录、现有会话立即失效。`)) return;
    try { await api.setUserActive(u.username, next); await refresh(); }
    catch (e: any) { alert("失败: " + (e?.message || e)); }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-6 py-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">用户管理</h2>
          <p className="text-xs text-slate-400">仅管理员可访问。普通用户登录后可在右上角自助修改密码；数据权限请在「数据权限」页配置。</p>
        </div>
        <div className="rounded-xl border bg-white px-3 py-1 text-xs text-slate-500" style={{borderColor:"#e6ecf6"}}>
          共 {items.length} 个账号
        </div>
      </div>

      <div className="qq-card px-5 py-4">
        <div className="mb-3 text-sm font-semibold text-slate-700">新建用户</div>
        <div className="grid grid-cols-12 gap-2">
          <input
            value={newUsername} onChange={(e) => setNewUsername(e.target.value)}
            placeholder="用户名"
            className="col-span-3 rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
          />
          <input
            value={newEmail} onChange={(e) => setNewEmail(e.target.value)}
            placeholder="飞书邮箱（如 x@feihe.com）"
            className="col-span-3 rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
          />
          <input
            type="password"
            value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
            placeholder="留空 = 系统生成强密码"
            className="col-span-3 rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
          />
          <select
            value={newRole} onChange={(e) => setNewRole(e.target.value as any)}
            className="col-span-1 rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
          >
            <option value="user">用户</option>
            <option value="admin">管理员</option>
          </select>
          <button onClick={create} className="qq-btn-primary col-span-2 !py-2 text-xs">+ 新增</button>
        </div>
        <div className="mt-2 text-[11px] text-slate-400">飞书邮箱用于发送报告推送；密码留空会生成一次性强密码，新用户首次登录后必须改密。</div>
      </div>

      {lastOtp && (
        <div className="qq-card border-amber-200 bg-amber-50/80 px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="text-sm text-amber-800">
              <div className="font-semibold">已为 <span className="font-mono">{lastOtp.username}</span> 生成一次性密码</div>
              <div className="mt-1">请妥善记录并尽快转告本人（仅显示这一次）：</div>
              <div className="mt-2 inline-block rounded-md bg-white px-3 py-1.5 font-mono text-base text-amber-900" style={{ border: "1px solid #fcd34d" }}>{lastOtp.password}</div>
            </div>
            <button onClick={() => setLastOtp(null)} className="text-xs text-amber-700 hover:underline">我已记录，关闭</button>
          </div>
        </div>
      )}

      <div className="qq-card overflow-hidden">
        <div className="flex items-center justify-between border-b px-5 py-3" style={{ borderColor: "#eef1f8" }}>
          <input value={search} onChange={(e)=>setSearch(e.target.value)} placeholder="按用户名搜索"
            className="w-60 rounded-lg border bg-white px-3 py-1.5 text-xs" style={{borderColor:"#e6ecf6"}} />
          <button onClick={refresh} className="text-xs text-blue-600 hover:underline">{loading ? "刷新中…" : "刷新"}</button>
        </div>
        {err && <div className="px-5 py-3 text-xs text-rose-600">{err}</div>}
        {!loading && (
          <table className="qq-table">
            <thead>
              <tr><th>用户名</th><th>角色</th><th>飞书邮箱</th><th>创建时间</th><th>状态</th><th className="w-56">操作</th></tr>
            </thead>
            <tbody>
              {filtered.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="flex items-center gap-2">
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-100 text-[11px] font-semibold text-blue-700">
                        {u.username.slice(0,1).toUpperCase()}
                      </span>
                      <span>{u.username}</span>
                      {u.must_change_password && <span className="qq-pill-amber" title="待首次改密">待改密</span>}
                    </div>
                  </td>
                  <td>{u.role === "admin"
                    ? <span className="qq-pill-blue">管理员</span>
                    : <span className="qq-pill-grey">普通用户</span>}
                  </td>
                  <td className="text-slate-500 font-mono text-[11px]">{u.email || "—"}</td>
                  <td className="tabular-nums text-slate-500">
                    {u.created_at ? new Date(u.created_at * 1000).toLocaleString() : "—"}
                  </td>
                  <td>
                    {(u.is_active ?? true)
                      ? <span className="qq-pill-grey">启用</span>
                      : <span className="qq-pill-amber" title="已停用，无法登录">已停用</span>}
                  </td>
                  <td>
                    <button onClick={() => resetPwd(u)} className="mr-2 text-xs text-blue-600 hover:underline">重置密码</button>
                    {u.username !== "admin" && (
                      <button onClick={() => toggleActive(u)} className="mr-2 text-xs text-amber-600 hover:underline">
                        {(u.is_active ?? true) ? "停用" : "启用"}
                      </button>
                    )}
                    {u.username !== "admin" && (
                      <button onClick={() => remove(u)} className="text-xs text-rose-600 hover:underline">删除</button>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr><td colSpan={6} className="px-5 py-8 text-center text-xs text-slate-400">无匹配账号</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
