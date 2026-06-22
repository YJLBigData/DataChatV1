"""Quick BI 独立部署版 SmartQ OpenAPI 客户端。

飞鹤 Quick BI 网关使用 `/openapi/v2/smartq/...` 路径与 `X-Gw-*` 签名头。
本模块只封装 SmartQ 传输、签名、结果归一化和多数据集兼容策略；路由/业务入口不再
重复写签名和请求逻辑。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .config import SmartQConfig, load_smartq_config
from .normalize import normalize_smartq_answer, smartq_answer_is_substantive

logger = logging.getLogger("datachat.smartq")
logging.getLogger("httpx").setLevel(logging.WARNING)

DATASET_LIST_PATH = "/openapi/v2/smartq/query/llmCubeWithThemeList"
QUERY_PATH = "/openapi/v2/smartq/queryByQuestion"
# 官方 QueryDatasetSmartqStatus：返回某数据集是否已开启智能小Q（布尔 Result）。
DATASET_STATUS_PATH = "/openapi/v2/dataset/smartq/status"


class SmartQError(Exception):
    """对用户友好的 SmartQ 错误。"""

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
        lk = key.lower()
        for dk, dv in d.items():
            if dk.lower() == lk and dv not in (None, ""):
                return dv
    return default


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    if v is None:
        return []
    return [v]


def _safe_str(v: Any) -> str:
    return "" if v is None else str(v)


def _coerce_bool(v: Any) -> bool:
    """把网关五花八门的布尔表示（true/1/"open"/"enabled"…）归一成 Python bool。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "enabled", "open", "y")


class SmartQClient:
    def __init__(self, cfg: Optional[SmartQConfig] = None):
        self.cfg = cfg or load_smartq_config()

    # ----------------------------------------------------------------- 公共 API

    def get_dataset_list(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        """获取当前 SmartQ 用户可用数据集。"""
        uid = user_id or resolve_smartq_user_id(self.cfg)
        payload = self._request("GET", DATASET_LIST_PATH, params={"userId": uid})
        data = _first(payload, "data", "Data", "result", "Result", default=payload)
        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            cube_ids = _first(data, "cubeIds", "cube_ids", default=None)
            if isinstance(cube_ids, dict):
                return [
                    {"cube_id": str(cid), "name": str(name or "未命名数据集"), "theme": ""}
                    for cid, name in cube_ids.items()
                    if str(cid or "").strip()
                ]
            items = (
                _first(data, "cubeList", "cube_list", "items", "list", "data", default=None)
                or _first(data, "llmCubeWithThemeList", "cubeWithThemeList", default=None)
                or []
            )
            if isinstance(items, dict):
                items = _as_list(_first(items, "items", "list", "cubeList", default=[]))
        out: list[dict[str, Any]] = []
        for item in _as_list(items):
            if not isinstance(item, dict):
                continue
            cube_id = _safe_str(_first(item, "cubeId", "cube_id", "CubeId", "id", "Id", default="")).strip()
            name = _safe_str(_first(item, "cubeName", "cube_name", "CubeName", "name", "Name", default="")).strip()
            theme = _safe_str(_first(item, "themeName", "theme", "ThemeName", "Theme", default="")).strip()
            if cube_id:
                out.append({"cube_id": cube_id, "name": name or "未命名数据集", "theme": theme})
        return out

    # Backward-compatible alias used by older tests/routes.
    def list_datasets(self, *, user_id: str) -> list[dict[str, Any]]:
        return self.get_dataset_list(user_id=user_id)

    def query_by_question(self, *, question: str, cube_id: str, user_id: str | None = None,
                          cube_name: str = "") -> dict[str, Any]:
        """单数据集问数。"""
        uid = user_id or resolve_smartq_user_id(self.cfg)
        raw = self._query_raw(question=question, user_id=uid, cube_id=cube_id)
        return self._normalize_query_result(raw, question=question, cube_id=cube_id, cube_name=cube_name)

    def query_multi_datasets(self, *, question: str, cube_ids: list[str], user_id: str | None = None,
                             cube_names: dict[str, str] | None = None) -> dict[str, Any]:
        """多数据集问数。

        策略：优先尝试官方 `multipleCubeIds` 单次请求；如果网关/SmartQ 不支持或返回空壳，
        自动降级为逐数据集查询，并在返回结果中保留每个数据集名称。
        """
        ids = [c.strip() for c in (cube_ids or []) if c and c.strip()]
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise SmartQError("请选择智能小Q数据集。")
        uid = user_id or resolve_smartq_user_id(self.cfg)
        names = cube_names or {}
        if len(ids) == 1:
            one = self.query_by_question(
                question=question, cube_id=ids[0], user_id=uid, cube_name=names.get(ids[0], ""),
            )
            return {
                "success": bool(one.get("success")),
                "question": question,
                "mode": "single_dataset",
                "native_multi_supported": None,
                "answer": one.get("answer") or {},
                "sql": one.get("sql") or "",
                "chart_type": one.get("chart_type") or "",
                "results": [one],
                "summary": one.get("summary") or "",
                "error": one.get("error"),
            }

        native_error = ""
        try:
            raw = self._query_raw(question=question, user_id=uid, multiple_cube_ids=ids)
            native = self._normalize_query_result(raw, question=question, cube_id=",".join(ids), cube_name="多数据集")
            if native.get("success") and self._native_result_is_grouped(native.get("answer") or {}, len(ids)):
                return {
                    "success": True,
                    "question": question,
                    "mode": "native_multi_dataset",
                    "native_multi_supported": True,
                    "answer": native.get("answer") or {},
                    "sql": native.get("sql") or "",
                    "chart_type": native.get("chart_type") or "",
                    "results": [native],
                    "summary": native.get("summary") or "SmartQ 已原生完成多数据集问数。",
                    "error": None,
                }
            native_error = (
                native.get("error")
                or "SmartQ 原生多数据集未返回可区分的数据集维度结果"
            )
            logger.info("[smartq] native multipleCubeIds returned no substantive result: %s", native_error)
        except SmartQError as exc:
            native_error = exc.message
            logger.info("[smartq] native multipleCubeIds unavailable: %s", exc.detail or exc.message)

        results: list[dict[str, Any]] = []
        for cube_id in ids:
            try:
                item = self.query_by_question(
                    question=question, cube_id=cube_id, user_id=uid, cube_name=names.get(cube_id, ""),
                )
            except SmartQError as exc:
                item = {
                    "cube_id": cube_id,
                    "cube_name": names.get(cube_id, "") or cube_id,
                    "success": False,
                    "sql": "",
                    "chart_type": "",
                    "data": [],
                    "answer": {},
                    "summary": "",
                    "error": exc.message,
                }
            results.append(item)
        ok_count = sum(1 for r in results if r.get("success"))
        answer = self._build_multi_answer(question, results, native_error=native_error)
        return {
            "success": ok_count > 0,
            "question": question,
            "mode": "multi_dataset",
            "native_multi_supported": False,
            "native_error": native_error,
            "answer": answer,
            "sql": self._join_sql(results),
            "chart_type": "TABLE",
            "results": results,
            "summary": answer.get("narrative") or "",
            "error": None if ok_count else "所有数据集问数均未成功。",
        }

    # Older route compatibility.
    def query(self, *, user_question: str, cube_id: str = "", cube_ids: Optional[list[str]] = None,
              user_id: str) -> dict[str, Any]:
        ids = [c for c in (cube_ids or []) if c] or ([cube_id] if cube_id else [])
        result = self.query_multi_datasets(question=user_question, cube_ids=ids, user_id=user_id)
        return result.get("answer") or {}

    def dataset_status(self, *, cube_id: str, user_id: str | None = None) -> dict[str, Any]:
        """查询某数据集是否已开启智能小Q（官方 QueryDatasetSmartqStatus，返回布尔 Result）。

        审计 P1：必须接入真实状态接口，不再永远返回 enabled=True。接口异常 → 抛 SmartQError，
        由路由层转成 ok:false，前端据此明确区分"未开启 / 无权限 / 接口异常"。
        """
        cid = (cube_id or "").strip()
        if not cid:
            raise SmartQError("请提供数据集 ID。")
        uid = user_id or resolve_smartq_user_id(self.cfg)
        payload = self._request("GET", DATASET_STATUS_PATH, params={"cubeId": cid, "userId": uid})
        result = _first(payload, "data", "Data", "result", "Result", "status", "enabled", default=None)
        if isinstance(result, dict):
            # 兼容 {"enabled": true} / {"status": 1} / {"smartqStatus": "open"} 等包裹形态
            result = _first(result, "enabled", "status", "smartqStatus", "open", "Result", default=result)
        enabled = _coerce_bool(result)
        return {"cube_id": cid, "enabled": enabled, "unchecked": False}

    # ------------------------------------------------------------------ 传输层

    def _query_raw(self, *, question: str, user_id: str, cube_id: str = "",
                   multiple_cube_ids: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "userId": user_id,
            "userQuestion": question,
        }
        if multiple_cube_ids:
            body["multipleCubeIds"] = multiple_cube_ids
        elif cube_id:
            body["cubeId"] = cube_id
        else:
            raise SmartQError("请选择智能小Q数据集。")
        last_error: SmartQError | None = None
        for attempt in range(2):
            try:
                return self._request("POST", QUERY_PATH, json_body=body)
            except SmartQError as exc:
                last_error = exc
                # SmartQ 偶发把明确的计数问题误判为"非数据查询"，短重试可明显降低失败率。
                retryable = ("不是关于数据查询" in exc.message) or ("未能找到相关字段" in exc.message)
                if not retryable or attempt >= 1:
                    raise
                time.sleep(0.4)
        raise last_error or SmartQError("智能小Q查询失败，请稍后再试。")

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                 json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.cfg.ready:
            raise SmartQError("智能小Q（SmartQ）未启用或未配置完整，请联系管理员。")
        api_path = self._api_path(path)
        url = f"{self.cfg.server_domain}{api_path}"
        method_u = method.upper()
        # Quick BI 独立部署文档里的 Java SDK 对 POST JSON 入参也使用 addParameter；
        # 网关签名同样要求把这些业务参数纳入 parameters 排序签名。
        sign_params = self._flatten_params(params if method_u == "GET" else (json_body or None))
        headers = self._signed_headers(method_u, api_path, sign_params)
        try:
            with httpx.Client(timeout=self.cfg.timeout) as client:
                resp = client.request(
                    method_u,
                    url,
                    params=self._flatten_params(params if method_u == "GET" else json_body),
                    json=json_body if method_u != "GET" else None,
                    headers=headers,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[smartq] %s %s transport error: %s", method_u, path, exc)
            raise SmartQError("智能小Q服务暂时不可用，请稍后再试。", detail=str(exc))
        text = resp.text[:500]
        if resp.status_code >= 400:
            logger.warning("[smartq] %s %s HTTP %s: %s", method_u, path, resp.status_code, text)
            raise SmartQError("智能小Q请求失败，请稍后再试或联系管理员。", detail=text)
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[smartq] %s %s invalid json: %s", method_u, path, text)
            raise SmartQError("智能小Q返回异常，请稍后再试。", detail=str(exc))
        if not isinstance(data, dict):
            raise SmartQError("智能小Q返回异常，请稍后再试。")

        success = _first(data, "success", "Success", default=None)
        code = _safe_str(_first(data, "code", "Code", default="")).strip()
        msg = _safe_str(_first(data, "message", "Message", "msg", default="")).strip()
        success_false = success is False or str(success).strip().lower() == "false"
        if success_false or code.lower() in {"false", "error"} or code.upper().startswith(("AE", "OE")):
            logger.warning("[smartq] %s %s business error: code=%s msg=%s", method_u, path, code, msg[:200])
            raise SmartQError(msg or "智能小Q无法回答该问题，请换个问法或检查数据集权限。", detail=str(data)[:500])
        if success is None and not any(k in data for k in ("data", "Data", "result", "Result")):
            logger.warning("[smartq] %s %s unexpected response shape: %s", method_u, path, str(data)[:300])
            raise SmartQError("智能小Q返回异常，请稍后再试。")
        return data

    def _api_path(self, path: str) -> str:
        p = path if path.startswith("/") else "/" + path
        base = self.cfg.api_base
        if not base:
            return p
        return f"{base}{p}"

    def _signed_headers(self, method: str, path: str,
                        params: dict[str, Any] | None = None) -> dict[str, str]:
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        gw_headers = {
            "X-Gw-AccessId": self.cfg.api_key,
            "X-Gw-Nonce": nonce,
            "X-Gw-Timestamp": timestamp,
        }
        string_to_sign = self._build_string_to_sign(method, path, params or {}, gw_headers)
        signature = base64.b64encode(
            hmac.new(
                self.cfg.api_secret.encode("utf-8"),
                self._percent_encode(string_to_sign).encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.cfg.server_domain,
            "X-Gw-AccessId": self.cfg.api_key,
            "X-Gw-Nonce": nonce,
            "X-Gw-Timestamp": timestamp,
            "X-Gw-Signature": signature,
        }

    @staticmethod
    def _flatten_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
        if not params:
            return None
        out: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, (list, tuple)):
                out[key] = ",".join(_safe_str(v) for v in value)
            else:
                out[key] = value
        return out

    @staticmethod
    def _build_string_to_sign(method: str, path: str, params: dict[str, Any],
                              headers: dict[str, str]) -> str:
        parts = [method.upper(), path.replace("+", " ")]
        param_pairs: list[str] = []
        for key in sorted(params):
            value = params[key]
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple)):
                value = ",".join(_safe_str(v) for v in value)
            param_pairs.append(f"{key}={value}")
        parts.append("&".join(param_pairs))
        header_pairs = [f"{key}:{headers[key]}" for key in sorted(headers) if headers.get(key)]
        parts.append("\n".join(header_pairs))
        return "\n".join(parts)

    @staticmethod
    def _percent_encode(value: str) -> str:
        return quote(value, safe="").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    # -------------------------------------------------------------- 归一化

    def _normalize_query_result(self, raw: dict[str, Any], *, question: str,
                                cube_id: str, cube_name: str = "") -> dict[str, Any]:
        data = _first(raw, "data", "Data", "result", "Result", default=raw)
        if isinstance(data, dict):
            payload = data
        else:
            payload = {"Values": data if isinstance(data, list) else []}
        answer = normalize_smartq_answer(payload, question=question)
        ok = smartq_answer_is_substantive(answer)
        sql = _safe_str((answer.get("explainability") or {}).get("sql") or "")
        chart_type = _safe_str(_first(payload, "chartType", "ChartType", "chart", default=""))
        summary = _safe_str(answer.get("narrative") or "")
        return {
            "cube_id": cube_id,
            "cube_name": cube_name or cube_id,
            "success": ok,
            "sql": sql,
            "chart_type": chart_type or _safe_str((answer.get("chart") or {}).get("type") or ""),
            "data": (answer.get("table") or {}).get("display_rows") or [],
            "answer": answer if ok else {},
            "summary": summary if ok else "",
            "error": None if ok else "智能小Q未返回有效结果，请确认数据集权限或换个问法。",
        }

    @staticmethod
    def _join_sql(results: list[dict[str, Any]]) -> str:
        blocks = []
        for item in results:
            sql = _safe_str(item.get("sql") or "").strip()
            if not sql:
                continue
            name = _safe_str(item.get("cube_name") or item.get("cube_id") or "")
            blocks.append(f"-- {name}\n{sql}")
        return "\n\n".join(blocks)

    @staticmethod
    def _native_result_is_grouped(answer: dict[str, Any], expected_count: int) -> bool:
        """原生 multipleCubeIds 是否返回了可区分的数据集维度。

        Quick BI 有时接受 multipleCubeIds 但只返回其中一个数据集的单指标卡。为了避免前端
        把单一数据集结果误当成"多数据集综合结果"，只有表头包含数据集/cube 类字段且行数
        覆盖多个数据集时才采用原生结果。
        """
        table = answer.get("table") or {}
        rows = table.get("display_rows") or table.get("rows") or []
        cols = table.get("display_columns") or table.get("columns") or []
        labels = []
        for col in cols:
            if isinstance(col, dict):
                labels.append(str(col.get("label") or col.get("key") or ""))
            else:
                labels.append(str(col))
        has_dataset_col = any(("数据集" in label) or ("cube" in label.lower()) for label in labels)
        return bool(has_dataset_col and len(rows) >= min(expected_count, 2))

    @staticmethod
    def _build_multi_answer(question: str, results: list[dict[str, Any]], *,
                            native_error: str = "") -> dict[str, Any]:
        columns = ["cube_name", "status", "summary", "rows"]
        display_columns = [
            {"key": "cube_name", "label": "数据集", "kind": "dimension", "unit": "", "format": "", "decimals": 0},
            {"key": "status", "label": "状态", "kind": "dimension", "unit": "", "format": "", "decimals": 0},
            {"key": "summary", "label": "结果摘要", "kind": "value", "unit": "", "format": "", "decimals": 0},
            {"key": "rows", "label": "返回行数", "kind": "metric", "unit": "行", "format": "", "decimals": 0},
        ]
        rows: list[list[str]] = []
        ok_count = 0
        for item in results:
            ok = bool(item.get("success"))
            ok_count += 1 if ok else 0
            ans = item.get("answer") or {}
            table = ans.get("table") or {}
            rows.append([
                _safe_str(item.get("cube_name") or item.get("cube_id") or ""),
                "成功" if ok else "失败",
                _safe_str(item.get("summary") or item.get("error") or ""),
                _safe_str(table.get("row_count") if ok else 0),
            ])
        total = len(results)
        narrative = f"已对 {total} 个智能小Q数据集分别问数，{ok_count} 个成功、{total - ok_count} 个失败。"
        if native_error:
            narrative += " 原生多数据集请求未采用，已自动降级为逐数据集查询。"
        return {
            "needs_clarify": False,
            "narrative": narrative,
            "highlights": [],
            "risk_notes": [],
            "table": {
                "columns": columns,
                "rows": rows,
                "display_columns": display_columns,
                "display_rows": rows,
                "row_count": len(rows),
                "elapsed_ms": 0,
            },
            "chart": {"type": "table"},
            "suggestions": [],
            "clarify_options": [],
            "explainability": {
                "sql": SmartQClient._join_sql(results),
                "row_count": len(rows),
                "reasoning": f"SmartQ 多数据集兼容查询：{question}",
                "used_tables": [],
                "filters_applied": [],
                "group_by": [],
                "confidence": 1.0 if ok_count else 0.0,
            },
        }


def resolve_smartq_user_id(cfg: SmartQConfig, *, fallback: str = "") -> str:
    """服务端身份映射：优先显式 user id，其次 user_token，最后用户邮箱/用户名兜底。"""
    return cfg.default_user_id or cfg.user_token or fallback
