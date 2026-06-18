/**
 * 前端 API 客户端 —— 单一入口，按 domain 拆分到 src/api/*。
 *
 * 命名空间：
 *   auth.*         本地 token / 用户缓存（src/api/http.ts）
 *   friendlyError  统一错误文案（src/api/http.ts）
 *   api.*          REST 调用 + SSE 流（按 domain 组合：auth/chat/expert/exports/smartq/admin）
 *
 * 拆分前这里是 589 行的巨型对象；现在仅做"组合 + 再导出"，各 domain 自包含、可独立演进。
 * 公开符号（api / auth / friendlyError）保持不变 —— 调用方 import 路径与用法零改动。
 */
export { auth, friendlyError } from "./api/http";

import { adminApi } from "./api/admin";
import { authApi } from "./api/auth";
import { chatApi } from "./api/chat";
import { expertApi } from "./api/expert";
import { exportsApi } from "./api/exports";
import { smartqApi } from "./api/smartq";

export const api = {
  ...chatApi,
  ...authApi,
  ...expertApi,
  ...exportsApi,
  ...smartqApi,
  ...adminApi,
};
