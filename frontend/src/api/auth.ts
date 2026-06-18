/** 鉴权 + 个人资料相关 API。 */
import type { AuthUser } from "../types";
import { jsonReq } from "./http";

export const authApi = {
  login: (username: string, password: string) =>
    jsonReq<{ token: string; user: AuthUser }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  me: () => jsonReq<AuthUser>("/api/me"),
  changeMyPassword: (old_password: string, new_password: string) =>
    jsonReq<{ ok: boolean }>("/api/me/password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    }),
  updateMyProfile: (email: string) =>
    jsonReq<AuthUser>("/api/me/profile", { method: "PATCH", body: JSON.stringify({ email }) }),
};
