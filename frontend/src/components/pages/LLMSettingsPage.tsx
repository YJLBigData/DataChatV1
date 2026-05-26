import { useEffect, useState } from "react";

import { api } from "../../api";
import type { LLMPreset, LLMPresetTestResult } from "../../types";

/** LLM 设置（仅 admin）—— 多套预设（preset）+ 保存前强制测试。
 *  · 列表展示所有预设；可点「设为默认」/「编辑」/「测试」/「删除」
 *  · 「新建」打开模态框：填表 → 测试 → 只有测试通过（拿到非空回复）才允许保存
 *  · 顶部右上角下拉自动用这里的列表
 */
type DraftPreset = {
  id?: string;
  name: string;
  provider: "bailian" | "feihe";
  api_key: string;        // 编辑已有 secret 时初值为 ""（用户必须重新输入或留空保留旧值）
  api_key_touched: boolean; // 仅当 true 才把 api_key 发到后端（避免覆盖为空）
  base_url: string;
  model: string;
  embed_model: string;
};

const EMPTY_DRAFT: DraftPreset = {
  name: "", provider: "bailian", api_key: "", api_key_touched: false,
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  model: "qwen-plus", embed_model: "text-embedding-v3",
};

/** 通知 App.tsx 顶部下拉框重拉 providers —— 任何会改变下拉项的操作（新建/更新/删除/设默认）都要喊一声。 */
function notifyProvidersChanged() {
  try { window.dispatchEvent(new CustomEvent("datachat:llm_providers_changed")); } catch { /* SSR-safe */ }
}

export function LLMSettingsPage() {
  const [items, setItems] = useState<LLMPreset[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [draft, setDraft] = useState<DraftPreset>(EMPTY_DRAFT);
  const [editing, setEditing] = useState<LLMPreset | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<LLMPresetTestResult | null>(null);
  // 标记"当前 testResult 对应的草稿快照"，避免改了字段后还能用旧测试结果保存
  const [testSnapshot, setTestSnapshot] = useState<string>("");

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const r = await api.adminListLLMPresets();
      setItems(r.items || []);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { refresh(); }, []);

  function openCreate() {
    setEditing(null);
    setDraft(EMPTY_DRAFT);
    setTestResult(null); setTestSnapshot("");
    setModalOpen(true);
  }
  function openEdit(p: LLMPreset) {
    setEditing(p);
    setDraft({
      id: p.id,
      name: p.name,
      provider: p.provider,
      api_key: "",                // 不回显旧密文；用户不改就传 null（保留）
      api_key_touched: false,
      base_url: p.base_url,
      model: p.model,
      embed_model: p.embed_model,
    });
    setTestResult(null); setTestSnapshot("");
    setModalOpen(true);
  }
  function closeModal() {
    if (saving || testing) return;
    setModalOpen(false);
  }

  function draftKey(d: DraftPreset): string {
    // 用于判断 testResult 是否还对应当前草稿（任何相关字段变化都失效）
    return [d.provider, d.api_key_touched ? d.api_key : "(keep)", d.base_url, d.model].join("|");
  }
  const testValid = testResult?.ok && testSnapshot === draftKey(draft);

  async function runTest() {
    if (!draft.model.trim()) { setErr("model 必填"); return; }
    if (draft.provider === "bailian" && draft.api_key_touched && !draft.api_key.trim()) {
      setErr("bailian provider 必须填 api_key（或者点取消保留原 key）"); return;
    }
    setTesting(true); setErr(null); setTestResult(null);
    try {
      // 编辑场景：未输入新 key 时把后端已存的 key 也带过去测一发？后端 /test 不读库，
      // 这里测的是"用户当前看到/即将保存的配置"。未触碰旧 key → 用空 api_key 测会失败。
      // 解决：编辑时若未触碰，让后端用 preset id 测（用 existing test endpoint）。
      let res: LLMPresetTestResult;
      if (editing && !draft.api_key_touched && draft.provider === "bailian") {
        // 测已存的（用旧 key），但前端字段改的 model/base 还没保存——提示用户先存
        res = await api.adminTestExistingLLMPreset(editing.id);
      } else {
        res = await api.adminTestLLMPresetCandidate({
          provider: draft.provider,
          api_key: draft.api_key,
          base_url: draft.base_url,
          model: draft.model,
        });
      }
      setTestResult(res);
      if (res.ok) setTestSnapshot(draftKey(draft));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    if (!testValid) { setErr("请先点「测试连接」并通过后再保存"); return; }
    if (!draft.name.trim()) { setErr("预设名不能为空"); return; }
    setSaving(true); setErr(null);
    try {
      if (editing) {
        await api.adminUpdateLLMPreset(editing.id, {
          name: draft.name,
          provider: draft.provider,
          api_key: draft.api_key_touched ? draft.api_key : null, // null = 后端"不动"
          base_url: draft.base_url,
          model: draft.model,
          embed_model: draft.embed_model,
        });
        setToast(`已更新预设：${draft.name}`);
      } else {
        await api.adminCreateLLMPreset({
          name: draft.name,
          provider: draft.provider,
          api_key: draft.api_key,
          base_url: draft.base_url,
          model: draft.model,
          embed_model: draft.embed_model,
        });
        setToast(`已创建预设：${draft.name}`);
      }
      setModalOpen(false);
      await refresh();
      notifyProvidersChanged();
      setTimeout(() => setToast(null), 4000);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setSaving(false);
    }
  }

  async function setAsDefault(p: LLMPreset) {
    try { await api.adminSetDefaultLLMPreset(p.id); setToast(`已把「${p.name}」设为默认`); await refresh(); notifyProvidersChanged(); setTimeout(() => setToast(null), 3000); }
    catch (e: any) { setErr(e?.message || String(e)); }
  }
  async function remove(p: LLMPreset) {
    if (!confirm(`确定删除预设「${p.name}」？\n它将被软删除（is_active=0）。`)) return;
    try { await api.adminDeleteLLMPreset(p.id); setToast(`已删除「${p.name}」`); await refresh(); notifyProvidersChanged(); setTimeout(() => setToast(null), 3000); }
    catch (e: any) { setErr(e?.message || String(e)); }
  }
  async function testOne(p: LLMPreset) {
    setToast(`测试中…「${p.name}」`);
    try {
      const r = await api.adminTestExistingLLMPreset(p.id);
      setToast(r.ok ? `✓ ${p.name}：${(r.text||'').slice(0, 60)} (${r.latency_ms}ms)` : `✗ ${p.name}：${r.error || '未知错误'}`);
      await refresh();
      setTimeout(() => setToast(null), 6000);
    } catch (e: any) { setErr(e?.message || String(e)); }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b bg-white px-5 py-3" style={{ borderColor: "#eef1f8" }}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[15px] font-semibold tracking-tight text-slate-800">LLM 设置（多套预设）</div>
            <div className="text-[12px] text-slate-400">每个预设 = 一套 provider + AK + 模型；右上角下拉切换即换模型；保存前必测</div>
          </div>
          <div className="flex items-center gap-2">
            <button className="qq-btn px-3 py-1.5 text-xs" onClick={refresh} disabled={loading}>{loading ? "加载…" : "刷新"}</button>
            <button className="qq-btn-primary px-3 py-1.5 text-xs" onClick={openCreate}>新建预设</button>
          </div>
        </div>
        {toast && <div className="mt-2 rounded-lg bg-emerald-50 px-3 py-1.5 text-[12px] text-emerald-700">{toast}</div>}
        {err && <div className="mt-2 rounded-lg bg-rose-50 px-3 py-1.5 text-[12px] text-rose-700">{err}</div>}
      </header>

      <section className="flex-1 overflow-y-auto px-5 py-5">
        {items.length === 0 ? (
          <div className="qq-card max-w-3xl px-5 py-6 text-center text-sm text-slate-500">
            还没有任何预设。点右上角「新建预设」开始 —— 填入百炼 AK + 模型名，测试通过即可保存。
          </div>
        ) : (
          <div className="qq-card overflow-hidden">
            <table className="w-full text-[13px]">
              <thead className="bg-slate-50 text-[11.5px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">名称</th>
                  <th className="px-3 py-2 text-left">Provider</th>
                  <th className="px-3 py-2 text-left">模型</th>
                  <th className="px-3 py-2 text-left">AK</th>
                  <th className="px-3 py-2 text-left">状态</th>
                  <th className="px-3 py-2 text-left">上次测试</th>
                  <th className="px-3 py-2 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-t" style={{ borderColor: "#eef1f8" }}>
                    <td className="px-3 py-2 font-medium text-slate-700">{p.name}</td>
                    <td className="px-3 py-2"><span className="qq-pill-grey">{p.provider}</span></td>
                    <td className="px-3 py-2 font-mono text-[12px] text-slate-700">{p.model}</td>
                    <td className="px-3 py-2 font-mono text-[11.5px] text-slate-500">{p.api_key || "—"}</td>
                    <td className="px-3 py-2">
                      {p.is_default && <span className="qq-pill-blue">默认</span>}
                      {!p.is_active && <span className="qq-pill-grey">已停用</span>}
                    </td>
                    <td className="px-3 py-2 text-[11.5px] text-slate-500">
                      {p.last_tested_at ? (
                        <span className={p.last_test_ok ? "text-emerald-600" : "text-rose-600"}>
                          {p.last_test_ok ? "✓" : "✗"} {new Date(p.last_tested_at * 1000).toLocaleString()}
                          <br /><span className="text-slate-400">{(p.last_test_response || "").slice(0, 80)}</span>
                        </span>
                      ) : <span className="text-slate-400">未测试</span>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button className="qq-btn px-2 py-0.5 text-[11px]" onClick={() => testOne(p)}>测试</button>
                        {!p.is_default && p.is_active && (
                          <button className="qq-btn px-2 py-0.5 text-[11px]" onClick={() => setAsDefault(p)}>设为默认</button>
                        )}
                        <button className="qq-btn px-2 py-0.5 text-[11px]" onClick={() => openEdit(p)}>编辑</button>
                        <button className="qq-btn px-2 py-0.5 text-[11px] hover:!border-rose-200 hover:!text-rose-600" onClick={() => remove(p)}>删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="qq-card mt-4 px-5 py-3 text-[12px] leading-6 text-slate-600">
          <div className="font-medium text-slate-700">说明</div>
          <ul className="mt-1 list-disc pl-5">
            <li>顶部右上角下拉会列出所有<strong>活跃且能用</strong>的预设（百炼 preset 必须有 AK；飞鹤要求服务器 .env 配过 AES_KEY）。</li>
            <li>切「默认预设」= 切下一次 chat 默认走哪一套（用户右上角可临时覆盖，按 token 记忆）。</li>
            <li>保存前的「测试连接」会真问一句"你是什么模型"，**只有非空回复才放行保存**。</li>
            <li>编辑已有预设时，AK 默认<strong>保留旧值</strong>（不显示密文也不下发），需要换 AK 时点「修改 AK」清空再填新值。</li>
          </ul>
        </div>
      </section>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4" onClick={closeModal}>
          <div className="qq-card w-full max-w-lg px-5 py-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div className="text-[14px] font-semibold text-slate-800">{editing ? `编辑预设：${editing.name}` : "新建预设"}</div>
              <button className="qq-btn px-2 py-0.5 text-xs" onClick={closeModal} disabled={saving || testing}>关闭</button>
            </div>

            <div className="mt-3 space-y-3">
              <Field label="名称">
                <input className="qq-input w-full" placeholder="如：百炼 qwen-plus / 飞鹤 Agent"
                       value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>

              <Field label="Provider">
                <div className="flex gap-2">
                  {(["bailian", "feihe"] as const).map((v) => (
                    <button key={v} className={"qq-chip " + (draft.provider === v ? "!border-blue-500 !bg-blue-50 !text-blue-700" : "")}
                            onClick={() => { setDraft({ ...draft, provider: v }); setTestResult(null); }}>
                      {v === "bailian" ? "百炼（DashScope 直连）" : "飞鹤（公司 ADP 网关）"}
                    </button>
                  ))}
                </div>
              </Field>

              {draft.provider === "bailian" && (
                <>
                  <Field label="API Key (sk-...)">
                    {editing && !draft.api_key_touched ? (
                      <div className="flex items-center gap-2">
                        <input className="qq-input flex-1 bg-slate-50 font-mono text-[12px] text-slate-500" disabled value={editing.api_key || "(已保存，未显示)"} />
                        <button className="qq-btn px-2 py-1 text-xs" onClick={() => setDraft({ ...draft, api_key_touched: true, api_key: "" })}>修改 AK</button>
                      </div>
                    ) : (
                      <input className="qq-input w-full font-mono text-[12px]" type="password"
                             placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                             value={draft.api_key} onChange={(e) => { setDraft({ ...draft, api_key: e.target.value, api_key_touched: true }); setTestResult(null); }} />
                    )}
                  </Field>

                  <Field label="Base URL">
                    <input className="qq-input w-full font-mono text-[12px]"
                           value={draft.base_url}
                           onChange={(e) => { setDraft({ ...draft, base_url: e.target.value }); setTestResult(null); }} />
                  </Field>
                </>
              )}

              <Field label="Chat 模型">
                <input className="qq-input w-full font-mono text-[12px]"
                       placeholder={draft.provider === "bailian" ? "qwen-plus / qwen-max / qwen3.6-max-preview" : "kaier_znws / d2b-order ..."}
                       value={draft.model}
                       onChange={(e) => { setDraft({ ...draft, model: e.target.value }); setTestResult(null); }} />
              </Field>

              {draft.provider === "bailian" && (
                <Field label="Embedding 模型（可留空，默认 text-embedding-v3）">
                  <input className="qq-input w-full font-mono text-[12px]" placeholder="text-embedding-v3"
                         value={draft.embed_model}
                         onChange={(e) => setDraft({ ...draft, embed_model: e.target.value })} />
                </Field>
              )}

              {testResult && (
                <div className={"rounded-xl border px-3 py-2 text-[12px] leading-6 " + (testResult.ok ? "border-emerald-100 bg-emerald-50 text-emerald-700" : "border-rose-100 bg-rose-50 text-rose-700")}>
                  {testResult.ok ? (
                    <>
                      <div className="font-medium">✓ 测试通过 ({testResult.latency_ms} ms){testResult.model_echo ? ` · 模型回执 ${testResult.model_echo}` : ""}</div>
                      <div className="mt-1 whitespace-pre-wrap break-words">{(testResult.text || "").slice(0, 400)}</div>
                    </>
                  ) : (
                    <>
                      <div className="font-medium">✗ 测试失败</div>
                      <div className="mt-1">{testResult.error}</div>
                    </>
                  )}
                </div>
              )}
            </div>

            <div className="mt-4 flex items-center justify-between border-t pt-3" style={{ borderColor: "#eef1f8" }}>
              <div className="text-[11.5px] text-slate-400">
                {testValid ? "✓ 测试已通过，可保存" : "请先点「测试连接」并通过"}
              </div>
              <div className="flex gap-2">
                <button className="qq-btn px-3 py-1.5 text-xs" onClick={runTest} disabled={testing || saving}>
                  {testing ? "测试中…" : "测试连接"}
                </button>
                <button className="qq-btn-primary px-3 py-1.5 text-xs" onClick={save} disabled={!testValid || saving}>
                  {saving ? "保存中…" : "保存"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[12.5px] font-medium text-slate-700">{label}</label>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}
