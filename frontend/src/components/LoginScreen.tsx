import { useState } from "react";

interface Props {
  onLogin: (username: string, password: string) => Promise<void>;
}

export function LoginScreen({ onLogin }: Props) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!username.trim() || !password) return;
    setErr(null); setBusy(true);
    try {
      await onLogin(username.trim(), password);
    } catch (e: any) {
      setErr(e?.message || "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full w-full items-center justify-center" style={{ background: "radial-gradient(circle at 30% 20%, #ecf0ff 0, #f5f7fc 60%)" }}>
      <div className="qq-card w-[380px] px-7 py-8">
        <div className="mb-5 flex flex-col items-center">
          <div className="qq-avatar mb-3">Q</div>
          <h2 className="text-xl font-semibold tracking-tight text-slate-800">飞鹤小Q · 智能问数</h2>
          <p className="mt-1 text-xs text-slate-400">高管经营数据问答平台</p>
        </div>
        <label className="mb-1.5 block text-xs text-slate-500">账号</label>
        <input
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-3 w-full rounded-xl border bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-300"
          style={{ borderColor: "#e6ecf6" }}
          placeholder="输入用户名"
        />
        <label className="mb-1.5 block text-xs text-slate-500">密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          className="mb-2 w-full rounded-xl border bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-300"
          style={{ borderColor: "#e6ecf6" }}
          placeholder="输入密码"
        />
        {err && <div className="mb-2 rounded-lg bg-rose-50 px-3 py-1.5 text-xs text-rose-600">{err}</div>}
        <button
          className="qq-btn-primary mt-3 w-full justify-center disabled:cursor-not-allowed disabled:opacity-60"
          onClick={submit}
          disabled={busy || !username || !password}
        >
          {busy ? "登录中…" : "登录"}
        </button>
        <div className="mt-3 text-center text-[11px] text-slate-400">
          首次使用请在飞书上找杨金龙开通账号权限。
        </div>
      </div>
    </div>
  );
}
