#!/usr/bin/env python3
"""校验 backend/requirements.txt 声明的依赖是否在当前解释器里**全部装齐且版本满足**。

审计 P1（依赖安装漂移）配套的"确定性体检"：start.sh 的哈希闸负责"requirements 变了就重装"，
本脚本负责"逐条核对真的装上了"——覆盖"哈希没变但有人手删了包 / venv 半残"这类漂移。

退出码：
  0  全部满足
  1  有缺失或版本不满足（stdout 打印逐条缺口；调用方据此触发 pip install -r）
  2  用法/环境错误（如 packaging 缺失、requirements 文件不存在）

只读、离线、零副作用：不装包、不联网、不写文件。可被 start.sh 调用，也可被 pytest 直接 import。
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def parse_requirements(req_path: Path) -> list[str]:
    """读出 requirements.txt 里**可校验的包需求行**。

    跳过：空行 / 注释 / -r include / -e 可编辑安装 / 纯选项行（-i、--hash 等）/
    VCS 与 URL 直链（git+、http(s)://、本地路径），这些无法用版本号简单核对。
    """
    lines: list[str] = []
    for raw in req_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 行内注释："fastapi==1.0  # web" → 去掉 # 之后
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if line.startswith("-"):  # -r / -e / -i / --extra-index-url ...
            continue
        low = line.lower()
        if low.startswith(("git+", "http://", "https://", "file:", ".", "/")):
            continue
        lines.append(line)
    return lines


def unmet_requirements(req_path: Path) -> list[str]:
    """返回未满足的需求描述列表（空列表=全部满足）。"""
    try:
        from packaging.requirements import Requirement
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"环境缺少 packaging 库，无法校验依赖：{exc}")

    missing: list[str] = []
    for spec in parse_requirements(req_path):
        try:
            req = Requirement(spec)
        except Exception:
            # 解析不了的需求行不阻断（保守跳过，交给 pip 自己处理）
            continue
        # 带环境标记（如 ; python_version < "3.11"）且当前环境不适用 → 跳过
        if req.marker is not None and not req.marker.evaluate():
            continue
        try:
            installed = version(req.name)
        except PackageNotFoundError:
            missing.append(f"{req.name}: 未安装（需要 {req.specifier or '任意版本'}）")
            continue
        if req.specifier and installed not in req.specifier:
            missing.append(f"{req.name}: 已装 {installed}，不满足 {req.specifier}")
    return missing


def main(argv: list[str]) -> int:
    if len(argv) >= 2:
        req_path = Path(argv[1])
    else:
        req_path = Path(__file__).resolve().parent.parent / "backend" / "requirements.txt"
    if not req_path.is_file():
        print(f"requirements 文件不存在：{req_path}", file=sys.stderr)
        return 2
    try:
        missing = unmet_requirements(req_path)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if missing:
        print("以下依赖未满足（需要 pip install -r 修复）：")
        for m in missing:
            print(f"  - {m}")
        return 1
    print(f"依赖体检通过：{req_path} 全部满足。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
