/** SmartQ（Quick BI 智能小Q）API。 */
import type { AnswerPayload } from "../types";
import { jsonReq } from "./http";

export const smartqApi = {
  smartqStatus: () => jsonReq<{ enabled: boolean; ready: boolean }>("/api/smartq/status"),
  smartqDiagnostics: () =>
    jsonReq<{ enabled: boolean; configured: boolean; ready: boolean; server_domain: string;
              api_base: string; api_key: string; api_secret: string; user_token: string;
              default_user_id: string }>("/api/smartq/diagnostics"),
  smartqDatasets: () =>
    jsonReq<{ ok: boolean; error?: string; items: { cube_id: string; name: string; theme: string }[] }>(
      "/api/smartq/datasets",
    ),
  smartqQuery: (req: { question: string; cube_id?: string | null; cube_ids?: string[] | null; conversation_id?: string | null }) =>
    jsonReq<{ ok: boolean; error?: string; question?: string; answer?: AnswerPayload;
              sql?: string; conversation_id?: string; trace_id?: string; rows?: number }>(
      "/api/smartq/query",
      { method: "POST", body: JSON.stringify(req) },
    ),
};
