"""语义层版本快照 + 校验 + 回滚（#15）。

企业级风险控制：semantic.yaml 全文编辑原本"保存即热重载"，没有预校验、没有历史、
无法回滚。这里补齐：
  · validate_semantic：保存前 dry-run 校验（YAML 语法 + 必填根键 + 计数），不落盘；
  · snapshot：每次保存前自动快照当前版本到 config/semantic_versions/；
  · list_versions / read_version：列出 / 查看历史版本（供前端做 diff）；
  · 回滚由调用方读取某版本内容后，走与保存一致的"校验→快照→写入→热重载"路径。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_KEYS = ("tables", "metrics", "dimensions")
# 版本号格式：semantic_YYYYmmdd_HHMMSS_<ms>.yaml —— 严格校验，防路径穿越。
_VID_RE = re.compile(r"^semantic_\d{8}_\d{6}_\d{1,3}\.yaml$")
_KEEP_VERSIONS = 50


def _versions_dir(semantic_path: Path) -> Path:
    d = semantic_path.parent / "semantic_versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_semantic(content: str) -> dict[str, Any]:
    """dry-run 校验：YAML 语法 + 必填根键 + 基本计数。不落盘。"""
    try:
        parsed = yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [f"YAML 语法错误：{exc}"], "summary": {}}
    if not isinstance(parsed, dict):
        return {"ok": False, "errors": ["根节点必须是 YAML mapping"], "summary": {}}
    errors: list[str] = []
    for k in _REQUIRED_KEYS:
        if k not in parsed:
            errors.append(f"缺少必填字段：{k}")
        elif not isinstance(parsed[k], dict):
            errors.append(f"字段 {k} 必须是 mapping（key→定义）")
    summary = {k: (len(parsed[k]) if isinstance(parsed.get(k), dict) else 0) for k in _REQUIRED_KEYS}
    return {"ok": not errors, "errors": errors, "summary": summary}


def snapshot(semantic_path: Path) -> str | None:
    """把当前 semantic.yaml 快照一份，返回 version id；源文件不存在则返回 None。"""
    if not semantic_path.exists():
        return None
    vid = f"semantic_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000}.yaml"
    dst = _versions_dir(semantic_path) / vid
    dst.write_text(semantic_path.read_text(encoding="utf-8"), encoding="utf-8")
    _prune(_versions_dir(semantic_path))
    return vid


def _prune(d: Path) -> None:
    files = sorted(d.glob("semantic_*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[_KEEP_VERSIONS:]:
        try:
            p.unlink()
        except OSError:
            pass


def list_versions(semantic_path: Path) -> list[dict[str, Any]]:
    d = _versions_dir(semantic_path)
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("semantic_*.yaml"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"id": p.name, "bytes": st.st_size, "mtime": st.st_mtime})
    return out


def read_version(semantic_path: Path, vid: str) -> str:
    if not _VID_RE.match(vid or ""):
        raise ValueError("非法版本号")
    p = _versions_dir(semantic_path) / vid
    if not p.exists():
        raise ValueError("版本不存在")
    return p.read_text(encoding="utf-8")
