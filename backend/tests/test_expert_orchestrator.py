"""专家团编排器 ok/失败语义（审计 P0-3）：失败的分析/合成绝不报成功。

离线、确定性：注入假 LLM，不触达真实模型/DB。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.expert_team.orchestrator import ExpertTeamOrchestrator  # noqa: E402
from app.expert_team.registry import get_registry  # noqa: E402


class _Res:
    def __init__(self, text):
        self.text = text


class _FakeLLM:
    """可配置的假 LLM：路由返回固定计划；chat 按 mode 决定成功/失败。"""

    def __init__(self, *, route_experts, chat_mode="ok"):
        self._route_experts = route_experts
        self.chat_mode = chat_mode  # ok | fail_all | fail_synth

    def chat_json(self, messages, *, schema_hint=None, temperature=0.0):
        return ({"route": "slow", "plan": "测试调度", "want_report": True,
                 "experts": self._route_experts}, {})

    def chat(self, messages, *, temperature=0.2, **kw):
        sys_prompt = messages[0]["content"] if messages else ""
        is_synth = "报告合成" in sys_prompt
        if self.chat_mode == "fail_all":
            raise RuntimeError("llm down")
        if self.chat_mode == "fail_synth" and is_synth:
            raise RuntimeError("synth down")
        return _Res("（分析正文）结论先行：测试通过。")


def _two_expert_ids():
    workers = [w for w in get_registry().workers()][:2]
    return [{"id": w.id, "subtask": "子任务", "data_query": ""} for w in workers]


def test_all_experts_fail_is_not_ok():
    """全部专家分析失败 → ok:false，不把失败文本当成功答案。"""
    orch = ExpertTeamOrchestrator(llm=_FakeLLM(route_experts=_two_expert_ids(), chat_mode="fail_all"))
    out = orch.run("本月销售诊断", want_report=True)
    assert out["ok"] is False
    assert not out.get("report")


def test_synthesis_failure_is_not_ok():
    """专家成功但报告合成失败 → ok:false（合成门未过），前端走降级/错误态。"""
    orch = ExpertTeamOrchestrator(llm=_FakeLLM(route_experts=_two_expert_ids(), chat_mode="fail_synth"))
    out = orch.run("本月销售诊断", want_report=True)
    assert out["ok"] is False
    # 报告原文仍带回（供排查），但 ok 必须为假
    assert "report" in out


def test_happy_path_is_ok():
    """专家 + 合成都成功 → ok:true，有报告。"""
    orch = ExpertTeamOrchestrator(llm=_FakeLLM(route_experts=_two_expert_ids(), chat_mode="ok"))
    out = orch.run("本月销售诊断", want_report=True)
    assert out["ok"] is True
    assert out["report"]
    assert isinstance(out.get("warnings"), list)


def test_empty_question_is_not_ok():
    """空问题 → ok:false（不进入编排）。"""
    orch = ExpertTeamOrchestrator(llm=_FakeLLM(route_experts=_two_expert_ids()))
    out = orch.run("   ", want_report=False)
    assert out["ok"] is False
