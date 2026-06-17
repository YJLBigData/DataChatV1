"""专家团成员视图 —— 把"内置定义 + 用户覆盖 + 自建 skill"合并成统一的可 CRUD 成员集。

单一合并入口，供 api（增删改查）与 orchestrator（调度）共用，避免两处各自 merge 走偏。

成员类型：
  · 内置专家（registry，来自 definitions/*.md）：可【改】(写覆盖) /【删】(隐藏=软删) /【查】/【还原默认】；
  · 决策调度总监：可改/查/还原，但不可删（编排必需）；
  · 用户自建 skill（store.user_skill）：可增/改/删/查（硬删）。
"""
from __future__ import annotations

from typing import Any

from .registry import ExpertDef, get_registry
from .store import get_expert_store


def _apply_override(base: dict[str, Any], ov: dict[str, Any] | None) -> dict[str, Any]:
    if not ov:
        return base
    out = dict(base)
    for k in ("name", "profession", "emoji", "instructions"):
        v = ov.get(k)
        if v is not None and str(v).strip() != "":
            out[k] = v
    out["has_override"] = True
    return out


def _builtin_base(e: ExpertDef) -> dict[str, Any]:
    return {
        "id": e.id, "name": e.name, "profession": e.profession, "emoji": e.emoji,
        "instructions": e.persona, "skills": list(e.skill_ids),
        "is_director": e.is_director, "is_builtin": True, "has_override": False,
        "deletable": not e.is_director, "deleted": False,
    }


def list_members(user_id: str, *, include_director: bool = False, include_hidden: bool = False) -> list[dict[str, Any]]:
    """成员花名册（覆盖已应用、隐藏的已剔除）。include_director 决定是否含总监。"""
    reg = get_registry()
    store = get_expert_store()
    overrides = store.list_overrides(user_id)
    out: list[dict[str, Any]] = []

    pool: list[ExpertDef] = []
    if include_director and reg.director():
        pool.append(reg.director())
    pool.extend(reg.workers())

    for e in pool:
        ov = overrides.get(e.id)
        if ov and ov.get("deleted") and not include_hidden:
            continue
        m = _apply_override(_builtin_base(e), ov)
        if ov and ov.get("deleted"):
            m["deleted"] = True
        m.pop("instructions", None)  # 列表轻量化；完整内容走 member_detail
        out.append(m)

    for s in store.list_skills(user_id):
        d = s.to_dict()
        out.append({
            "id": d["id"], "name": d["name"], "profession": d["profession"], "emoji": d["emoji"],
            "skills": [d["id"]],
            "is_director": False, "is_builtin": False, "has_override": False,
            "deletable": True, "deleted": False,
        })
    return out


def member_detail(user_id: str, member_id: str) -> dict[str, Any] | None:
    """单个成员的完整可编辑内容（用于"查/改"表单）。含默认值便于"还原默认"对比。"""
    reg = get_registry()
    store = get_expert_store()
    # 自建 skill
    sk = store.get_skill(user_id, member_id)
    if sk:
        d = sk.to_dict()
        return {
            "id": d["id"], "name": d["name"], "profession": d["profession"], "emoji": d["emoji"],
            "instructions": sk.instructions, "skills": [d["id"]],
            "is_director": False, "is_builtin": False, "has_override": False,
            "deletable": True, "deleted": False, "methodology": "",
        }
    # 内置专家 / 总监
    e = reg.expert(member_id)
    if not e:
        return None
    ov = store.get_override(user_id, member_id)
    detail = _apply_override(_builtin_base(e), ov)
    if ov and ov.get("deleted"):
        detail["deleted"] = True
    detail["default"] = {
        "name": e.name, "profession": e.profession, "emoji": e.emoji, "instructions": e.persona,
    }
    detail["methodology"] = reg.skill_methodology(e.skill_ids, max_chars=4000) if e.skill_ids else ""
    return detail


def split_for_orchestrator(user_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """给编排器：用户自建 skill 列表 + 内置覆盖映射（含隐藏标记）。"""
    store = get_expert_store()
    user_skills = [s.to_dict() for s in store.list_skills(user_id)]
    overrides = store.list_overrides(user_id)
    return user_skills, overrides
