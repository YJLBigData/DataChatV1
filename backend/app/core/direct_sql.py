"""直接 SQL 模式 — 处理结构化 QueryPlan 装不下的复杂多表分析查询。

触发条件（任一）：
  · 用户提问明确包含「请直接返回 SQL」「可执行 SQL」「直接给 SQL」等关键词；
  · 提到的物理表数 ≥ 3；
  · 提到的指标/字段名 ≥ 8；
  · 结构化 planner 输出 confidence < 0.4 且涉及多表。

流程：
  1. 拉所有相关表的 schema（语义层 + INFORMATION_SCHEMA），拼成 prompt 上下文
  2. LLM 生成 MySQL 5.6 兼容 SQL（禁 CTE/窗口/JSON）
  3. AST guard（只 SELECT、表白名单、自动 LIMIT）
  4. 字段级权限校验（含字段白名单）
  5. 行级权限注入（必传字段权限）
  6. 执行 → 返回原始行集
  7. 让 LLM 给一段经营结论（短 narrative）

安全：所有授权检查与结构化模式相同；不绕权。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("datachat.direct_sql")


DIRECT_SQL_TRIGGERS = [
    "请直接返回", "直接返回 SQL", "直接返回SQL", "请直接给 SQL", "请生成可执行",
    "可执行的 MySQL", "可执行MySQL", "请直接生成 SQL", "请生成 SQL",
    "直接给出SQL", "直接给我 SQL", "请输出 SQL",
]


def should_use_direct_sql(question: str, *, min_tables: int = 3) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    for trig in DIRECT_SQL_TRIGGERS:
        if trig in q:
            return True
    # 多表硬指标
    table_pattern = re.compile(r"ads_[a-z_0-9]+_df|ads_[a-z_0-9]+")
    mentioned = set(table_pattern.findall(q))
    if len(mentioned) >= min_tables:
        return True
    return False


def build_schema_context(
    semantic_layer: Any,
    *,
    max_tables: int = 20,
    allowed_tables: "set[str] | frozenset[str] | None" = None,
) -> str:
    """从语义层挤出一份"给 LLM 看"的物理表 schema。

    关键：暴露**物理列名**和**物理表达式**，把业务指标名作为「业务别名」描述。
    避免 LLM 把 `terminal_sale_amount_total` (语义层 metric key) 当成物理列。

    allowed_tables 非 None 时按用户数据域过滤——LLM 看不到域外表，结构上
    杜绝直接 SQL 模式引用别人的表（guard 仍按用户白名单二次校验）。
    """
    lines: list[str] = []
    shown = 0
    for t in semantic_layer.list_tables():
        if allowed_tables is not None and t.name not in allowed_tables:
            continue
        if shown >= max_tables:
            break
        shown += 1
        lines.append(f"\n物理表：`{t.schema}`.`{t.name}`（业务名：{t.label}）")
        lines.append(f"  粒度：{t.grain or '—'}")
        if t.description:
            lines.append(f"  说明：{t.description.strip()[:240]}")
        # 时间字段
        if t.time_field:
            lines.append(f"  时间字段：`{t.time_field}` 格式 {t.time_format or 'YYYY-MM'}")
        elif t.time_field_year and t.time_field_month:
            lines.append(f"  时间字段：`{t.time_field_year}` + `{t.time_field_month}`（两列拼合）")
        # 该表上的维度物理列
        lines.append("  维度列：")
        for d in semantic_layer.list_dimensions():
            col = d.table_columns.get(t.name)
            if col:
                samples = ", ".join((d.sample_values or [])[:3])
                lines.append(f"    · `{col}`  —  {d.label}（{d.name}）" + (f"  样例: {samples}" if samples else ""))
        # 该表上的指标
        lines.append("  可用聚合（替换为完整表达式即可放在 SELECT/聚合里）：")
        for m in semantic_layer.list_metrics():
            if m.table == t.name:
                lines.append(f"    · {m.label}（业务名 {m.name}） → SQL 表达式：{m.expression}  单位 {m.unit or '—'}")
    lines.append("\n** 注意 **：")
    lines.append("· 上面 `xxx` 才是物理列名，业务名/指标名（如 terminal_sale_amount_total）**不是列名**，写在 SQL 里只能用上面给出的 SQL 表达式。")
    lines.append("· 比如要算「终端销售额」，SQL 里写 `SUM(terminal_sale_amount)`，不要写 `terminal_sale_amount_total`。")
    lines.append("· 时间过滤要按上面给的时间字段格式写（YYYY-MM 还是 year+month 两列）。")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是飞鹤数据仓库的高级 SQL 工程师。基于给定的物理表 schema 和业务问题，生成一段**可直接执行**的 MySQL 5.6 SQL。

硬性约束：
1. 只能用语义层列出的物理表与字段，**严禁编造**任何表名或字段名。
2. **只允许 SELECT**，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 任何 DML/DDL。
3. **禁止使用** CTE (WITH ...)、窗口函数 (OVER ...)、JSON 函数、rank()/row_number()。
4. 所有比率类除法必须用 `NULLIF(denominator, 0)` 保护分母为 0。
5. 输出**只有 SQL 一段**，不要 ```sql 标记，不要解释，不要 markdown。
6. 顶层加 `LIMIT 500`（除非用户在题目里明确要求 TOP N）。
7. 多表 JOIN 时使用明确的 INNER/LEFT JOIN ... ON，禁止隐式逗号 JOIN。
"""


def generate_direct_sql(
    question: str,
    *,
    semantic_layer: Any,
    llm,
    history: Optional[list] = None,
    previous_plan: Optional[dict] = None,
    allowed_tables: "set[str] | frozenset[str] | None" = None,
) -> str:
    schema = build_schema_context(semantic_layer, allowed_tables=allowed_tables)

    ctx_block = ""
    if history:
        tail = [f"[{m.get('role','user')}] {m.get('content','')}" for m in history[-4:]]
        ctx_block += "最近对话（用于判断是否为追问）：\n" + "\n".join(tail) + "\n\n"
    if previous_plan:
        import json as _json
        ctx_block += (
            "上一轮查询结构（plan）：\n"
            + _json.dumps(previous_plan, ensure_ascii=False)
            + "\n\n【多轮规则】若本句是对上一轮的追问（升维/降维/筛选/排序/换切面），"
            "必须沿用上一轮的表、时间口径与指标口径，只按本句显式增量改维度/筛选；"
            "除非本句明确点了另一个指标或另一个时间，否则不得换表/换月份。\n\n"
        )

    user_prompt = (
        f"{ctx_block}"
        f"问题：\n{question}\n\n"
        f"语义层（仅以下表和字段可用）：\n{schema}\n\n"
        f"请直接输出 SQL（一段，可执行的 MySQL 5.6）："
    )
    res = llm.chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user",   "content": user_prompt}],
        temperature=0.0,
        max_tokens=3500,
    )
    text = (res.text or "").strip()
    # 去掉常见的 markdown / 解释
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("sql"):
            text = text[3:].strip()
    if text.endswith("```"):
        text = text.rstrip("`").rstrip()
    # 切掉解释前缀
    if "SELECT" in text.upper():
        idx = text.upper().find("SELECT")
        text = text[idx:]
    return text.strip()


def summarize_direct_result(question: str, sql: str, columns: list[str], rows: list[list[Any]], *, llm) -> tuple[str, list[str]]:
    """让 LLM 给一段简短的经营分析（不超 200 字）。失败返回兜底文案。"""
    preview = "\n".join(" | ".join(str(v) for v in r) for r in rows[:20])
    sys = ("你是飞鹤管理层的经营分析师。基于查询结果，用 ≤ 200 字给出经营结论。"
           "硬要求：中文；数字精确；不要展示 SQL；不要堆术语；先结论后展开。")
    user = (f"问题：{question}\n查询字段：{columns}\n数据（截取前 20 行）：\n{preview}\n\n"
            f"请输出 narrative 和 3-5 条 highlights（用换行分隔的 - bullet）。")
    try:
        res = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                       temperature=0.0, max_tokens=600)
        text = (res.text or "").strip()
        # 简易分段：第一段当 narrative，- 开头的当 highlights
        narrative_lines: list[str] = []
        highlights: list[str] = []
        for ln in text.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith("-") or ln.startswith("•"):
                highlights.append(ln.lstrip("-•·").strip())
            else:
                narrative_lines.append(ln)
        narrative = " ".join(narrative_lines) or text[:200]
        return narrative, highlights[:5]
    except Exception as exc:
        logger.warning("direct narrate failed: %s", exc)
        return (f"查询返回 {len(rows)} 行记录。", [])
