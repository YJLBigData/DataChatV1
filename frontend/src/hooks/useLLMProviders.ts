import { useCallback, useEffect, useState } from "react";

import { api } from "../api";

export type LLMProvider = { id: string; label: string; hint: string };

const LLM_STORAGE_KEY = "datachatv1:llm_provider";

/**
 * 右上角"模型下拉"的状态、持久化与刷新（#17：从 App.tsx 抽出）。
 * enabled 通常 = 已登录；登录后自动拉一次，并监听 LLM 设置页的变更事件重拉。
 */
export function useLLMProviders(enabled: boolean) {
  const [llmProviders, setLlmProviders] = useState<LLMProvider[]>([]);
  const [llmDefault, setLlmDefault] = useState<string>("");
  const [llmChoice, setLlmChoice] = useState<string>(
    () => (typeof window !== "undefined" && localStorage.getItem(LLM_STORAGE_KEY)) || "",
  );

  const reloadLLMProviders = useCallback(async () => {
    try {
      const r = await api.listLLMProviders();
      setLlmProviders(r.available || []);
      setLlmDefault(r.default || "");
      setLlmChoice((cur) => {
        const ids = (r.available || []).map((x) => x.id);
        if (cur && ids.includes(cur)) return cur;
        return r.default || ids[0] || "";
      });
    } catch {
      /* 拉不到列表不阻塞主流程 */
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void reloadLLMProviders();
    const onChanged = () => { void reloadLLMProviders(); };
    window.addEventListener("datachat:llm_providers_changed", onChanged);
    return () => window.removeEventListener("datachat:llm_providers_changed", onChanged);
  }, [enabled, reloadLLMProviders]);

  useEffect(() => {
    if (llmChoice) localStorage.setItem(LLM_STORAGE_KEY, llmChoice);
  }, [llmChoice]);

  return { llmProviders, llmDefault, llmChoice, setLlmChoice, reloadLLMProviders };
}
