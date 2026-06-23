"""审计修复回归（2026-06-22）：基础设施类（P1 依赖漂移 / P1 pytest 收集 / 启动脚本闸）。

全部离线、零副作用：不装包、不联网、不起服务。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))


# ============================================ P1: pytest 全量收集不再被探活模块污染

def test_llm_probe_module_renamed_and_guarded():
    """原 test_runner.py 被 pytest 误收集成测试（缺 fixture 报错）。现已更名 llm_probe.py：
    1) 旧模块名不存在；2) 新模块带 __test__ = False 双保险；3) 业务函数 probe_* 可用。"""
    import importlib

    # 旧模块名应已不存在（彻底更名，不留 test_ 前缀的可收集文件）
    assert not (BACKEND / "app" / "core" / "llm" / "test_runner.py").exists()

    mod = importlib.import_module("app.core.llm.llm_probe")
    assert getattr(mod, "__test__", None) is False, "llm_probe 必须声明 __test__ = False"
    assert hasattr(mod, "probe_bailian") and hasattr(mod, "probe_feihe")
    assert hasattr(mod, "probe_preset_config")
    # 不应再暴露 test_ 前缀的探活函数（pytest 会按名字收集 test_*）
    assert not hasattr(mod, "test_bailian")
    assert not hasattr(mod, "test_preset_config")


def test_probe_preset_config_unknown_provider():
    from app.core.llm.llm_probe import probe_preset_config
    r = probe_preset_config("claude", api_key="x", model="x")
    assert r["ok"] is False and "不支持" in r["error"]


# ============================================ P1: 依赖漂移体检脚本

def _load_checker():
    import importlib.util
    path = ROOT / "scripts" / "check_requirements.py"
    spec = importlib.util.spec_from_file_location("check_requirements", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_requirements_parses_and_skips_noise(tmp_path):
    checker = _load_checker()
    req = tmp_path / "requirements.txt"
    req.write_text(
        "\n".join([
            "# comment line",
            "",
            "fastapi==0.110.0  # inline comment",
            "-r other.txt",
            "-e .",
            "--extra-index-url https://example.com",
            "git+https://github.com/x/y.git",
            "https://example.com/pkg.whl",
            "requests>=2.0",
        ]),
        encoding="utf-8",
    )
    parsed = checker.parse_requirements(req)
    # 只保留两条真正可校验的需求；注释/-r/-e/选项/URL 全部剔除
    assert parsed == ["fastapi==0.110.0", "requests>=2.0"]


def test_check_requirements_flags_missing_package(tmp_path):
    checker = _load_checker()
    req = tmp_path / "requirements.txt"
    req.write_text("this-package-surely-does-not-exist-9z9z9z==1.2.3\n", encoding="utf-8")
    unmet = checker.unmet_requirements(req)
    assert any("this-package-surely-does-not-exist" in u for u in unmet)


def test_check_requirements_passes_for_installed(tmp_path):
    """已安装且版本满足 → 不报缺口（用一定装了的 pytest 自身做正例）。"""
    checker = _load_checker()
    from importlib.metadata import version
    req = tmp_path / "requirements.txt"
    req.write_text(f"pytest=={version('pytest')}\n", encoding="utf-8")
    assert checker.unmet_requirements(req) == []


# ============================================ P1: 启动脚本依赖闸（防回退到弱抽样）

def test_start_sh_uses_requirements_hash_gate():
    """start.sh 必须用 requirements 哈希 + 体检闸，而非旧的"抽样 import 5 个包"弱判定。"""
    start_sh = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert "requirements.sha256" in start_sh, "start.sh 应基于 requirements 哈希戳判定安装"
    assert "check_requirements.py" in start_sh, "start.sh 应调用依赖体检脚本兜底漂移"
    # 旧弱判定（仅抽样 import）不应再作为唯一安装条件存在
    assert 'import fastapi, sqlglot, redis, bcrypt, jwt' not in start_sh
