import { useEffect, useState } from "react";

import { api } from "../../api";
import type { LLMSettingItem } from "../../types";

/** LLM 设置（仅 admin）—— 百炼 AK / base / 模型 + 默认 provider；
 *  改完点保存即刻生效（写 SQLite，下次 LLM 调用自动读，不需要重启）。
 *  Secret 永远脱敏显示，编辑要点「修改」清空再填新值。
 */
type SettingsMap = Record<string, LLMSettingItem>;

const FIELDS: { key: string; label: string; hint?: string; placeholder?: string; isSecret?: boolean }[] = [
  { key: "DASHSCOPE_API_KEY", label: "DashScope API Key", hint: "百炼/阿里云 sk- 开头的 AK", placeholder: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", isSecret: true },
  { key: "DASHSCOPE_BASE_URL", label: "DashScope Base URL", hint: "默认 https://dashscope.aliyuncs.com/compatible-mode/v1", placeholder: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { key: "DASHSCOPE_MODEL", label: "Chat 模型", hint: "qwen-plus / qwen-max / qwen3.6-max-preview / qwen-turbo / ...", placeholder: "qwen-plus" },
  { key: "DASHSCOPE_EMBED_MODEL", label: "Embedding 模型", hint: "默认 text-embedding-v3；切换会让检索向量缓存失效", placeholder: "text-embedding-v3" },
];

const PROVIDER_OPTIONS = [
  { value: "bailian", label: "百炼（DashScope 直连）" },
  { value: "feihe", label: "飞鹤（公司 ADP 网关）" },
];

export function LLMSettingsPage() {
  const [data, setData] = useState<SettingsMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string | null>>({}); // null=不动，""=清除，"xxx"=新值
  const [secretUnlocked, setSecretUnlocked] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const r = await api.adminGetLLMSettings();
      setData(r.settings);
      setEdits({});
      setSecretUnlocked({});
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { refresh(); }, []);

  function setEdit(key: string, val: string) {
    setEdits((p) => ({ ...p, [key]: val }));
  }
  function clearField(key: string) {
    if (!confirm(`确定要清除「${key}」？\n下次将回退到环境变量或代码默认。`)) return;
    setEdits((p) => ({ ...p, [key]: "" }));
  }
  function unlockSecret(key: string) {
    setSecretUnlocked((p) => ({ ...p, [key]: true }));
    setEdits((p) => ({ ...p, [key]: "" })); // 解锁时先清成空，等用户输新
  }

  async function save() {
    if (Object.keys(edits).length === 0) { setToast("没有改动"); return; }
    setSaving(true); setErr(null);
    try {
      const r = await api.adminPutLLMSettings(edits);
      setToast(`已保存 ${r.updated.length} 项（v${r.version}）`);
      await refresh();
      setTimeout(() => setToast(null), 4000);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  const hasEdits = Object.keys(edits).length > 0;
  const currentProvider = data?.LLM_PROVIDER?.value || "";
  const draftProvider = edits.LLM_PROVIDER ?? currentProvider;

  return (
    <div className="flex h-full flex-col">
      <header className="border-b bg-white px-5 py-3" style={{ borderColor: "#eef1f8" }}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[15px] font-semibold tracking-tight text-slate-800">LLM 设置</div>
            <div className="text-[12px] text-slate-400">百炼 AK / 模型 / 默认 Provider —— 改完即时生效，无需重启</div>
          </div>
          <div className="flex items-center gap-2">
            <button className="qq-btn px-3 py-1.5 text-xs" onClick={refresh} disabled={loading}>{loading ? "加载…" : "刷新"}</button>
            <button className="qq-btn-primary px-3 py-1.5 text-xs" onClick={save} disabled={!hasEdits || saving}>{saving ? "保存中…" : `保存${hasEdits ? `（${Object.keys(edits).length} 项改动）` : ""}`}</button>
          </div>
        </div>
        {toast && <div className="mt-2 rounded-lg bg-emerald-50 px-3 py-1.5 text-[12px] text-emerald-700">{toast}</div>}
        {err && <div className="mt-2 rounded-lg bg-rose-50 px-3 py-1.5 text-[12px] text-rose-700">{err}</div>}
      </header>

      <section className="flex-1 overflow-y-auto px-5 py-5">
        <div className="qq-card max-w-3xl px-5 py-4">
          <div className="text-[13px] font-semibold text-slate-700">百炼 / DashScope</div>
          <div className="mt-1 text-[11.5px] text-slate-400">值来源：<span className="qq-pill-grey">db</span>=管理页已保存 / <span className="qq-pill-grey">env</span>=服务器 .env / <span className="qq-pill-grey">default</span>=代码默认</div>

          <div className="mt-4 space-y-4">
            {FIELDS.map((f) => {
              const cur = data?.[f.key];
              const editVal = edits[f.key];
              const showValue = editVal !== undefined && editVal !== null;
              const locked = f.isSecret && cur?.is_set && !secretUnlocked[f.key] && editVal === undefined;
              return (
                <div key={f.key} className="border-b pb-3 last:border-0" style={{ borderColor: "#eef1f8" }}>
                  <div className="flex items-center justify-between">
                    <label className="text-[12.5px] font-medium text-slate-700">{f.label}</label>
                    <div className="flex items-center gap-1.5">
                      {cur && <span className="qq-pill-grey">{cur.source}</span>}
                      {cur?.is_set && <span className="qq-pill-blue">已配置</span>}
                    </div>
                  </div>
                  {f.hint && <div className="mt-1 text-[11px] text-slate-400">{f.hint}</div>}
                  <div className="mt-1.5 flex items-center gap-2">
                    {locked ? (
                      <>
                        <input
                          type="text"
                          disabled
                          value={cur?.value || ""}
                          className="qq-input flex-1 cursor-not-allowed bg-slate-50 font-mono text-[12px] text-slate-500"
                        />
                        <button className="qq-btn px-2 py-1 text-xs" onClick={() => unlockSecret(f.key)}>修改</button>
                      </>
                    ) : (
                      <>
                        <input
                          type={f.isSecret ? "password" : "text"}
                          placeholder={f.placeholder || ""}
                          value={showValue ? (editVal as string) : (cur?.value || "")}
                          onChange={(e) => setEdit(f.key, e.target.value)}
                          className="qq-input flex-1 font-mono text-[12px]"
                        />
                        {cur?.is_set && editVal === undefined && (
                          <button className="qq-btn px-2 py-1 text-xs" onClick={() => clearField(f.key)} title="清除该键回退到 env 或默认">清除</button>
                        )}
                        {editVal !== undefined && (
                          <button className="qq-btn px-2 py-1 text-xs" onClick={() => {
                            const next = { ...edits }; delete next[f.key]; setEdits(next);
                            if (f.isSecret) setSecretUnlocked((p) => ({ ...p, [f.key]: false }));
                          }}>取消</button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}

            {/* 默认 provider */}
            <div className="pt-2">
              <label className="text-[12.5px] font-medium text-slate-700">默认 LLM Provider</label>
              <div className="mt-1 text-[11px] text-slate-400">前端右上角下拉的默认值；用户也可临时切换覆盖。</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {PROVIDER_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setEdit("LLM_PROVIDER", opt.value)}
                    className={"qq-chip " + (draftProvider === opt.value ? "!border-blue-500 !bg-blue-50 !text-blue-700" : "")}
                  >{opt.label}</button>
                ))}
                {edits.LLM_PROVIDER !== undefined && (
                  <button className="qq-btn px-2 py-1 text-xs" onClick={() => {
                    const next = { ...edits }; delete next.LLM_PROVIDER; setEdits(next);
                  }}>取消改动</button>
                )}
              </div>
              {data?.LLM_PROVIDER && (
                <div className="mt-1.5 text-[11px] text-slate-400">当前生效：<span className="font-mono">{data.LLM_PROVIDER.value || "(未设置→使用代码默认)"}</span>（来源 {data.LLM_PROVIDER.source}）</div>
              )}
            </div>
          </div>
        </div>

        <div className="qq-card mt-4 max-w-3xl px-5 py-3 text-[12px] leading-6 text-slate-600">
          <div className="font-medium text-slate-700">说明</div>
          <ul className="mt-1 list-disc pl-5">
            <li>保存后<strong>立即生效</strong>，下一次 chat / 检索 调用直接读 DB 值，<strong>无需 systemctl restart</strong>。</li>
            <li>「清除」会把 DB 行删掉，下次回退到服务器 <code>.env</code> 的同名变量；如果 .env 也没设，再回退到代码默认。</li>
            <li>切换 chat 模型立即影响所有用户；切换 embedding 模型会让 Redis 里旧向量缓存"作废"（key 含模型名），下一次问数会触发一次冷启动 embedding，正常现象。</li>
            <li>百炼 AK 留空 → 右上角下拉只剩飞鹤一个 provider，前端会自动隐藏下拉。</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
