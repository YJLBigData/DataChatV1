import { useEffect, useState } from "react";

import { api, auth } from "../api";

interface Props {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
}

/** 修改自己的密码 — 任何登录用户都能用。 */
export function PasswordModal({ open, onClose, onChanged }: Props) {
  const [oldPwd, setOldPwd] = useState("");
  const [pwd, setPwd] = useState("");
  const [pwd2, setPwd2] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) { setOldPwd(""); setPwd(""); setPwd2(""); setErr(null); setBusy(false); }
  }, [open]);

  // 前后端规则必须一致（后端 is_password_strong：≥8 位 + 至少 2 类字符 + 非弱口令）。
  function checkStrength(p: string): string {
    if (p.length < 8) return "新密码至少 8 位";
    let kinds = 0;
    if (/[a-zA-Z]/.test(p)) kinds++;
    if (/\d/.test(p)) kinds++;
    if (/[^\w]/.test(p)) kinds++;
    if (kinds < 2) return "密码至少包含 2 类字符（字母/数字/符号）";
    if (["12345678", "password", "admin123", "qwerty"].includes(p.toLowerCase())) return "禁止使用常见弱口令";
    return "";
  }

  async function submit() {
    if (!oldPwd || !pwd) return;
    const weak = checkStrength(pwd);
    if (weak) { setErr(weak); return; }
    if (pwd !== pwd2)   { setErr("两次输入的新密码不一致"); return; }
    setBusy(true); setErr(null);
    try {
      await api.changeMyPassword(oldPwd, pwd);
      onChanged();
      onClose();
      // 安全（P1）：改密后后端已吊销旧 token，必须强制重新登录（不再沿用旧会话）。
      alert("密码已更新，请使用新密码重新登录");
      auth.clear();
      window.dispatchEvent(new CustomEvent("datachat:unauthorized"));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm">
      <div className="qq-card w-[380px] px-6 py-5">
        <div className="mb-3 text-base font-semibold text-slate-800">修改我的密码</div>
        <label className="mb-1 block text-xs text-slate-500">当前密码</label>
        <input
          type="password" autoFocus
          value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
          className="mb-2 w-full rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
        />
        <label className="mb-1 block text-xs text-slate-500">新密码（至少 8 位，含字母+数字/符号）</label>
        <input
          type="password"
          value={pwd} onChange={(e) => setPwd(e.target.value)}
          className="mb-2 w-full rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
        />
        <label className="mb-1 block text-xs text-slate-500">确认新密码</label>
        <input
          type="password"
          value={pwd2} onChange={(e) => setPwd2(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          className="mb-2 w-full rounded-lg border bg-white px-3 py-2 text-sm" style={{ borderColor: "#e6ecf6" }}
        />
        {err && <div className="mb-2 rounded-lg bg-rose-50 px-3 py-1.5 text-xs text-rose-600">{err}</div>}
        <div className="mt-2 flex justify-end gap-2">
          <button className="rounded-xl border px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50" style={{ borderColor: "#e6ecf6" }} onClick={onClose}>
            取消
          </button>
          <button className="qq-btn-primary !px-4 !py-1.5 text-xs disabled:opacity-50" onClick={submit} disabled={busy}>
            {busy ? "提交中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
