import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import type { ConversationMeta } from "../types";

/**
 * 会话列表的加载与刷新（#17：从 App.tsx 抽出）。
 * enabled 通常 = 已登录；登录后自动拉一次。setConversations 暴露给登出时清空。
 */
export function useConversations(enabled: boolean) {
  const [conversations, setConversations] = useState<ConversationMeta[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    (async () => {
      try {
        const cs = await api.listConversations();
        if (!cancelled) setConversations(cs.items || []);
      } catch {
        /* ignore */
      }
    })();
    return () => { cancelled = true; };
  }, [enabled]);

  const refreshConversations = useCallback(async () => {
    if (!enabled) return;
    try { setConversations((await api.listConversations()).items || []); } catch { /* ignore */ }
  }, [enabled]);

  return { conversations, setConversations, refreshConversations };
}
