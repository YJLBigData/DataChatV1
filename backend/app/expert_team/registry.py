"""Expert / Skill / Knowledge 注册表 —— 解析 definitions/ 下的 markdown 定义。

定义来源（从 feihe-decision-team 移植，见 definitions/）：
  · experts/*.md   —— 专家 persona（YAML frontmatter: name/displayName/profession/skills + 正文）
  · skills/*.md    —— skill 能力说明（SKILL.md，正文即方法论/流程）
  · knowledge/*.md —— 知识库（分析方法论/行业知识/输出规范…）

对外暴露「专家」为可选调度单元：每个专家 = persona + 其绑定 skill 的方法论，
外加一个特殊的【决策调度总监】（is_director=True）。用户自建 skill 也表现为一个专家。
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("datachat.expert_team")

_DEF_DIR = Path(__file__).resolve().parent / "definitions"

# 专家 → 领域图标（仅 UI 展示用，缺省 🧑‍💼）
_DOMAIN_EMOJI = {
    "feihe-decision-team-team-lead": "🧭",
    "data-query-analyst": "⚡",
    "sales-analyst": "📈",
    "channel-analyst": "🛒",
    "user-ops-analyst": "👥",
    "market-analyst": "🔭",
    "finance-analyst": "💰",
    "data-auditor": "🔍",
    "knowledge-auditor": "📚",
}


@dataclass
class SkillDef:
    id: str
    title: str
    body: str
    triggers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "triggers": list(self.triggers)}


@dataclass
class ExpertDef:
    id: str                       # agent id（文件名，无 .md）
    name: str                     # 中文显示名（卓见全…）
    profession: str               # 中文职业（决策调度总监…）
    persona: str                  # persona 正文（system prompt 基底）
    skill_ids: list[str] = field(default_factory=list)
    is_director: bool = False
    is_builtin: bool = True
    emoji: str = "🧑‍💼"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "profession": self.profession,
            "skills": list(self.skill_ids),
            "is_director": self.is_director,
            "is_builtin": self.is_builtin,
            "emoji": self.emoji,
        }


# --------------------------------------------------------------------- parsing

_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(text or "")
    if not m:
        return {}, (text or "")
    fm_raw, body = m.group("fm"), m.group("body")
    try:
        import yaml
        data = yaml.safe_load(fm_raw) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:  # noqa: BLE001
        data = {}
    return data, body


def _zh(value: Any, fallback: str = "") -> str:
    """displayName / profession 可能是 {en, zh} dict 或纯字符串。优先取中文。"""
    if isinstance(value, dict):
        return str(value.get("zh") or value.get("en") or fallback).strip()
    return str(value or fallback).strip()


def _first_heading(body: str) -> str:
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def _extract_triggers(body: str) -> list[str]:
    """从 SKILL.md 的"触发词"表格里粗略抽取触发短语（best-effort，失败返回空）。"""
    triggers: list[str] = []
    in_section = False
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            in_section = "触发" in s
            continue
        if in_section and s.startswith("|") and "---" not in s:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells and cells[0] and cells[0] not in ("用户表达", "触发词", "场景"):
                for token in re.split(r"[、,，/]|”“|\"", cells[0]):
                    t = token.strip().strip("“”\"")
                    if t and len(t) <= 12:
                        triggers.append(t)
    return triggers[:12]


# --------------------------------------------------------------------- registry


class ExpertTeamRegistry:
    def __init__(self, def_dir: Path | None = None):
        self.def_dir = def_dir or _DEF_DIR
        self._lock = threading.RLock()
        self._skills: dict[str, SkillDef] = {}
        self._experts: dict[str, ExpertDef] = {}
        self._knowledge: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        self._load_skills()
        self._load_experts()
        self._load_knowledge()
        if not self._experts:
            logger.warning("expert_team: no experts loaded from %s", self.def_dir)

    def _load_skills(self) -> None:
        sk_dir = self.def_dir / "skills"
        for p in sorted(sk_dir.glob("*.md")) if sk_dir.exists() else []:
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            _fm, body = _split_frontmatter(text)
            sid = p.stem
            self._skills[sid] = SkillDef(
                id=sid,
                title=_first_heading(body) or sid,
                body=body.strip(),
                triggers=_extract_triggers(body),
            )

    def _load_experts(self) -> None:
        ex_dir = self.def_dir / "experts"
        for p in sorted(ex_dir.glob("*.md")) if ex_dir.exists() else []:
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            fm, body = _split_frontmatter(text)
            eid = str(fm.get("name") or p.stem).strip()
            skills = fm.get("skills") or []
            skill_ids = [str(s).strip() for s in skills if str(s).strip()] if isinstance(skills, list) else []
            is_director = ("feihe-decision" in skill_ids) or (eid == "feihe-decision-team-team-lead")
            self._experts[eid] = ExpertDef(
                id=eid,
                name=_zh(fm.get("displayName"), eid),
                profession=_zh(fm.get("profession"), "分析师"),
                persona=body.strip(),
                skill_ids=skill_ids,
                is_director=is_director,
                is_builtin=True,
                emoji=_DOMAIN_EMOJI.get(eid, "🧑‍💼"),
            )

    def _load_knowledge(self) -> None:
        kb_dir = self.def_dir / "knowledge"
        for p in sorted(kb_dir.glob("*.md")) if kb_dir.exists() else []:
            try:
                self._knowledge[p.stem] = p.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                continue

    # -------------------------------------------------------------- accessors

    def director(self) -> ExpertDef | None:
        for e in self._experts.values():
            if e.is_director:
                return e
        return None

    def workers(self) -> list[ExpertDef]:
        """非总监的专家（可被调度的分析/检核/取数专家）。"""
        return [e for e in self._experts.values() if not e.is_director]

    def expert(self, eid: str) -> ExpertDef | None:
        return self._experts.get(eid)

    def list_experts(self) -> list[ExpertDef]:
        # director 排第一，其余按 persona 顺序
        ds = [e for e in self._experts.values() if e.is_director]
        ws = [e for e in self._experts.values() if not e.is_director]
        return ds + ws

    def skill(self, sid: str) -> SkillDef | None:
        return self._skills.get(sid)

    def list_skills(self) -> list[SkillDef]:
        return list(self._skills.values())

    def skill_methodology(self, skill_ids: list[str], *, max_chars: int = 2600) -> str:
        """把若干 skill 的方法论正文拼成一段（给专家做 system prompt 用，做长度上限）。"""
        chunks: list[str] = []
        for sid in skill_ids:
            sk = self._skills.get(sid)
            if not sk:
                continue
            chunks.append(f"### Skill：{sk.title}（{sk.id}）\n{sk.body}")
        text = "\n\n".join(chunks).strip()
        return text[:max_chars]

    def knowledge_digest(self, *, max_chars: int = 6000) -> str:
        """知识库摘要（方法论/行业知识/输出规范）。数据口径以语义层为准，这里只补语义层
        未覆盖的分析性知识，避免重复。"""
        order = [
            "analysis-frameworks", "industry-knowledge",
            "output-format-spec", "few-shot-orchestration",
        ]
        parts: list[str] = []
        for key in order:
            if key in self._knowledge:
                parts.append(self._knowledge[key])
        text = "\n\n---\n\n".join(parts).strip()
        return text[:max_chars]

    def knowledge_files(self) -> dict[str, str]:
        return dict(self._knowledge)


_singleton: ExpertTeamRegistry | None = None
_singleton_lock = threading.RLock()


def get_registry() -> ExpertTeamRegistry:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ExpertTeamRegistry()
    return _singleton
