/** 数据导出队列（XLSX 大表异步导出）API。 */
import type { ExportJob } from "../types";
import { BASE, jsonReq } from "./http";

export const exportsApi = {
  createExport: (conversation_id: string, trace_id: string) =>
    jsonReq<{ ok: boolean; error?: string; job?: ExportJob }>("/api/exports", {
      method: "POST", body: JSON.stringify({ conversation_id, trace_id }),
    }),
  listExports: () => jsonReq<{ items: ExportJob[] }>("/api/exports"),
  getExport: (id: string) => jsonReq<ExportJob>(`/api/exports/${encodeURIComponent(id)}`),
  deleteExport: (id: string) =>
    jsonReq<{ ok: boolean; deleted?: boolean; job_id?: string; file_deleted?: boolean }>(
      `/api/exports/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  exportDownloadUrl: (id: string) => `${BASE}/api/exports/${encodeURIComponent(id)}/download`,
};
