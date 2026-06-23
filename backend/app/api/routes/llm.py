"""LLM provider 目录 + 管理端 LLM 设置/多预设（从 main.py 拆出，零行为变化）。

/api/llm/providers 对所有登录用户开放（右上角下拉用，绝不回 key 明文）；其余 /api/admin/llm-*
要求 require_admin。secret 一律脱敏；写设置即时生效（下次 LLM 调用自动用新值，无需重启）。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import require_admin, require_user
from app.api.schemas import LLMPresetCreateReq, LLMPresetPatchReq, LLMPresetTestReq, LLMSettingsPutReq
from app.core.auth import User

logger = logging.getLogger("datachat.api")

router = APIRouter(tags=["llm"])


@router.get("/api/llm/providers")
def api_llm_providers(_user: User = Depends(require_user)) -> dict[str, Any]:
    """右上角下拉框用：列出本环境**已配置**的 LLM provider + 默认值。
    不返回任何 key/secret 明文。"""
    from app.core.llm.router import available_providers, default_provider
    return {
        "available": available_providers(),
        "default": default_provider(),
    }


@router.get("/api/admin/llm-settings")
def api_admin_get_llm_settings(_: User = Depends(require_admin)) -> dict[str, Any]:
    """读当前生效的 LLM 配置（DB 优先 → env → cfg 默认）。
    secret(DASHSCOPE_API_KEY) 一律脱敏成 'sk-***1234'，**绝不**回完整密文。"""
    from app.core.llm_settings import get_llm_settings_store
    return {"settings": get_llm_settings_store().get_all_effective()}


@router.put("/api/admin/llm-settings")
def api_admin_put_llm_settings(
    req: LLMSettingsPutReq = Body(...),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    """写 LLM 配置到 SQLite，**写完即生效**（下一次 LLM 调用自动用新值，无需重启）。
    空字符串/None 视为"清除该键"，下次回退到 env 或代码默认。
    允许键白名单：DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / DASHSCOPE_MODEL /
               DASHSCOPE_EMBED_MODEL / LLM_PROVIDER。"""
    from app.core.llm_settings import get_llm_settings_store
    store = get_llm_settings_store()
    # req.dict(exclude_unset=False) 取所有字段；空串=清除，None=不动
    payload = req.dict()
    # None 字段视作"未传/不动"，过滤掉
    updates = {k: v for k, v in payload.items() if v is not None}
    changed = store.set_many(updates)
    logger.info("admin llm-settings update keys=%s", changed)
    return {"ok": True, "updated": changed, "version": store.version}


@router.get("/api/admin/llm-presets")
def api_admin_list_llm_presets(_: User = Depends(require_admin)) -> dict[str, Any]:
    from app.core.llm_presets import get_llm_presets_store
    return {"items": [p.to_dict_masked() for p in get_llm_presets_store().list_all(include_inactive=True)]}


@router.post("/api/admin/llm-presets/test")
def api_admin_test_llm_preset_candidate(
    req: LLMPresetTestReq = Body(...),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    """保存前测试：用候选配置直接发一句问题，必须收到非空回复才返回 ok=True；不写库。"""
    from app.core.llm.llm_probe import probe_preset_config, DEFAULT_TEST_PROMPT
    from app.core.llm_presets import get_llm_presets_store
    api_key = req.api_key or ""
    # 编辑场景（P1）：未输入新 AK（api_key 为空）但带了 preset_id → 用旧 AK + 草稿字段合并测试，
    # 确保测的就是"即将保存的 base_url/model"，而不是旧的整套配置。
    if req.provider == "bailian" and not api_key.strip() and req.preset_id:
        existing = get_llm_presets_store().get(req.preset_id)
        if existing and existing.provider == "bailian":
            api_key = existing.api_key or ""
    result = probe_preset_config(
        req.provider,
        api_key=api_key, base_url=req.base_url,
        model=req.model,
    )
    result["prompt"] = req.prompt or DEFAULT_TEST_PROMPT
    return result


@router.post("/api/admin/llm-presets")
def api_admin_create_llm_preset(
    req: LLMPresetCreateReq = Body(...),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    from app.core.llm_presets import get_llm_presets_store
    try:
        p = get_llm_presets_store().create(
            name=req.name, provider=req.provider, api_key=req.api_key,
            base_url=req.base_url, model=req.model, embed_model=req.embed_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "preset": p.to_dict_masked()}


@router.put("/api/admin/llm-presets/{preset_id}")
def api_admin_update_llm_preset(
    preset_id: str,
    req: LLMPresetPatchReq = Body(...),
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    from app.core.llm_presets import get_llm_presets_store
    try:
        p = get_llm_presets_store().update(
            preset_id,
            name=req.name, provider=req.provider, api_key=req.api_key,
            base_url=req.base_url, model=req.model, embed_model=req.embed_model,
            is_active=req.is_active,
        )
    except ValueError as exc:
        code = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc))
    return {"ok": True, "preset": p.to_dict_masked()}


@router.delete("/api/admin/llm-presets/{preset_id}")
def api_admin_delete_llm_preset(
    preset_id: str,
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    from app.core.llm_presets import get_llm_presets_store
    get_llm_presets_store().delete(preset_id)
    return {"ok": True}


@router.post("/api/admin/llm-presets/{preset_id}/set-default")
def api_admin_set_default_llm_preset(
    preset_id: str,
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    from app.core.llm_presets import get_llm_presets_store
    try:
        get_llm_presets_store().set_default(preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}


@router.post("/api/admin/llm-presets/{preset_id}/test")
def api_admin_test_existing_llm_preset(
    preset_id: str,
    _: User = Depends(require_admin),
) -> dict[str, Any]:
    """对已存的 preset 跑一发测试，把结果写回 last_test_*"""
    from app.core.llm_presets import get_llm_presets_store
    from app.core.llm.llm_probe import probe_preset_config
    store = get_llm_presets_store()
    p = store.get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail="preset 不存在")
    result = probe_preset_config(p.provider, api_key=p.api_key, base_url=p.base_url, model=p.model)
    store.record_test(preset_id, bool(result.get("ok")), str(result.get("text") or result.get("error") or ""))
    return result
