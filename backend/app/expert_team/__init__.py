"""飞鹤决策专家团（Expert Team）—— 独立入口模块。

把 feihe-decision-team（Claude-Code skills 形态的多智能体决策团）的主要能力整合进
DataChatV1 的「专家团」页面：

  · 一个【决策调度总监】skill（卓见全）作为主编排，决定调度哪些专家/skill；
  · 其余专家/skill 自主搭配（销售/渠道/用户/市场/财务/取数/检核…）；
  · 用户可以创建自定义 skill，任意组合用于问数与报告生成。

刻意完全自包含在本目录下，不污染其它模块：
  · 模型 = 复用 app.core.llm.router（与问数同一套，含右上角 provider 切换）；
  · 数据 = 复用 app.core.orchestrator 数据问数流水线（同一套 DB / 语义层）；
  · 知识库 = 复用 app.core.semantic（语义层，UI 改名「知识库」）+ 本目录 definitions/knowledge
    里的分析方法论/行业知识（语义层未覆盖的非结构化部分），不重复建设数据口径。
"""
from __future__ import annotations

from .registry import ExpertTeamRegistry, get_registry

__all__ = ["ExpertTeamRegistry", "get_registry"]
