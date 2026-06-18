"""Planner 的纯解析常量与 LLM plan schema —— 从 planner.py 拆出（零行为变化）。

这些都是与 Planner 实例无关的「问句规则识别」资产：季度/阈值(HAVING)/追问/时间表达
的正则与映射，以及给 LLM 的 plan JSON schema 模板。独立成文件后可被单测直接覆盖，
planner.py 仍按原名引用（import 回去），方法体内的写法不变。
"""
from __future__ import annotations

import re

QUARTER_RE = re.compile(r"(?:(\d{4})年?)?(?:第)?([1234一二三四])\s*季度")
QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4", "1": "1", "2": "2", "3": "3", "4": "4"}

# 指标阈值过滤（HAVING）：把"达成率低于90%""销售额超过100万""占比大于5%"这类
# 对【指标】的比较条件提取出来。注意：否定式/双字算子（不低于/不超过）必须排在裸算子
# （低于/超过）之前——alternation 在同一起点按从左到右第一个命中，"不"位置先吃掉"不低于"，
# 避免被误判成 lt。整段为非重叠匹配（finditer），所以"不低于"里的"低于"不会被二次命中。
_THRESHOLD_RE = re.compile(
    r"(?P<op>不低于|不少于|不小于|大于等于|不小过|至少|≥|>=|"
    r"不超过|不高于|不大于|小于等于|至多|最多|≤|<=|"
    r"高于|大于|超过|超出|多于|大过|＞|>|"
    r"低于|小于|不足|不到|少于|达不到|未达到|未及|低过|＜|<)"
    r"\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<pct>%|％)?\s*(?P<unit>万|亿)?"
)
_THRESHOLD_OP_CANON = {
    "不低于": "gte", "不少于": "gte", "不小于": "gte", "大于等于": "gte", "不小过": "gte",
    "至少": "gte", "≥": "gte", ">=": "gte",
    "不超过": "lte", "不高于": "lte", "不大于": "lte", "小于等于": "lte",
    "至多": "lte", "最多": "lte", "≤": "lte", "<=": "lte",
    "高于": "gt", "大于": "gt", "超过": "gt", "超出": "gt", "多于": "gt", "大过": "gt", "＞": "gt", ">": "gt",
    "低于": "lt", "小于": "lt", "不足": "lt", "不到": "lt", "少于": "lt",
    "达不到": "lt", "未达到": "lt", "未及": "lt", "低过": "lt", "＜": "lt", "<": "lt",
}

# 多轮"继续/下钻/切片"标记 —— 出现任一即视为对上一轮的追问（而非独立新问题）。
# 刻意只收录追问语气词；独立问句里常见的 "各/分别/排序/列出/前N" 一律不在此列，
# 避免把带完整口径的标准问句误判为追问。
CONTINUATION_MARKERS: tuple[str, ...] = (
    "只看", "仅看", "单看", "就看", "继续", "接着", "顺着", "下钻", "钻取",
    "拆到", "拆开", "拆分", "细分到", "细化到", "展开看", "再按", "再看",
    "再拆", "还看", "其中", "上面", "上述", "刚才", "之前", "这些", "那些",
    "这几个", "那几个", "这3个", "这三个", "那3个", "那三个", "表现怎么样",
    "怎么样", "加上", "去掉", "换成", "改成", "基础上",
)

# 时间表达（出现任一即认为问句自带时间口径，多为独立新问题）。
_TIME_HINT_RE = re.compile(
    r"20\d{2}|季度|本月|当月|这个月|上月|上个月|本年|今年|去年|上年|"
    r"年初至今|ytd|近\s*\d|最近|\d+\s*月"
)

PLAN_SCHEMA = """{
  "metric": "<语义层 metrics 的英文 key>",
  "extra_metrics": ["<其它同表指标 key>"],
  "table": "<物理表名，从语义层 tables 中选择，必须与 metric 一致>",
  "group_by": ["<语义层 dimensions 英文 key>"],
  "filters": [
    {"dimension": "<语义层 dimensions key>", "op": "eq|in|like", "values": ["..."]}
  ],
  "having": [
    {"metric": "<语义层 metrics key>", "op": "lt|lte|gt|gte|eq|ne", "value": 0.9}
  ],
  "time_range": {
    "kind": "none|relative|absolute|range",
    "period": "this_month|last_month|this_year|last_year|ytd|last_n_months",
    "n": 0,
    "year": "YYYY",
    "months": ["MM"],
    "start_ym": "YYYY-MM",
    "end_ym": "YYYY-MM"
  },
  "calculation": "yoy_growth|mom_growth|ratio|rank|trend|delta|cumulative|''",
  "order_by": [{"field": "<metric or dimension key>", "dir": "asc|desc"}],
  "limit": 0,
  "needs_clarify": false,
  "clarify_reason": "",
  "clarify_options": [],
  "confidence": 0.0,
  "reasoning": "<一段中文解释>"
}"""
