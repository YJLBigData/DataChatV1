---
name: claude-datachat-audit-fix
description: Strict DataChatV1 accuracy-first repair workflow for Claude Code. Use when fixing ordinary intelligent question answering / NL2SQL accuracy, especially wrong SQL returned as ok=true, false clarification prompts, multi-metric intent mistakes, semantic-table mismatch, follow-up context drift, or when the target is to raise DataChatV1 question-answering correctness toward 90%+ with regression evidence.
---

# Claude DataChat Accuracy Fix

## Goal

Fix DataChatV1 with accuracy as the release blocker. The target is not "API ok=true"; the target is that the generated plan, SQL, result columns, filters, ordering, and final answer match the user's business intent.

Work from the repo root:

```bash
cd /Users/yangjinlong/app/PythonProject/DataChatV1
```

Treat the user's stated baseline, about 50% correctness, as a production-quality failure. The repair is complete only when known failures are closed and an expanded accuracy set reaches at least 90% business-correct results.

## Required Evidence

Before editing code, read the current code paths and these bundled references:

- `references/DataChatV1_strict_audit_20260622.xlsx`: human-facing audit workbook with issue list, function testing, sample question judgments, and structure recommendations.
- `references/audit_report_data.json`: machine-readable audit source for issue severity, function-test status, and question rows.
- `references/chat_sample_raw_results.json`: raw intelligent-question-answering responses, SQL, and returned data.
- `references/positive_sql_examples.json`: positive SQL examples for failed or risky sample cases.
- `references/source_ai智能问数测试样例.xlsx`: original user-provided sample questions.

If evidence conflicts, prefer JSON for exact fields and preserve workbook wording in the final report.

Also inspect the live code before changing anything:

- `backend/app/core/nl2sql/planner.py`
- `backend/app/core/nl2sql/planner_support.py`
- `backend/app/core/nl2sql/plan.py`
- `backend/app/core/nl2sql/compiler.py`
- `backend/app/core/orchestrator.py`
- `backend/app/core/answerer.py`
- `backend/config/semantic.yaml`
- `backend/tests/test_audit_nl2sql_0622.py`
- `backend/tests/test_context_and_security.py`
- `backend/tests/test_scope_accuracy.py`

## Non-Negotiable Guardrails

- Do not push, deploy, or change production configuration unless the user explicitly asks.
- Do not commit secrets, `.env`, runtime SQLite/MySQL data, logs, screenshots, generated caches, or built web assets unless explicitly required.
- Preserve user changes. Start with `git status --short` and inspect relevant diffs before touching files.
- Do not hand-edit `backend/web/static/...`; change frontend source and build if frontend changes are required.
- Do not hide failures by weakening tests, suppressing SQL, or converting backend errors into fake success states.
- Do not let any Agent directly write final SQL or bypass `QueryPlan`, compiler, SQL Guard, permissions, or read-only execution.
- Keep data-development rigor: schema, grain, metric口径, partition, filters, and aggregation level must be explicit and auditable.
- Local tests may use MySQL-shaped SQL, but generated logic must remain compatible with the product's intended Dataphin/MaxCompute data-development usage where applicable.

## Accuracy-First Architecture

Keep ordinary question answering deterministic at its core:

```text
question
-> retrieval / semantic matching
-> QueryPlan
-> deterministic plan repair
-> Accuracy Critic Agent (structured review only)
-> optional one-pass plan repair
-> compiler
-> SQL Guard
-> read-only execution
-> Result Validator
-> answer
```

The Agent is a critic, not an author. It may inspect the question, semantic candidates, plan, compiled SQL, and result shape. It may return structured repair hints. It must not produce free-form final SQL for execution.

## Mandatory First Check: Version Drift

Before implementing new logic, verify whether the reported failure is already fixed locally but absent from the running service.

Run the focused local regression:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_audit_nl2sql_0622.py::test_case9_top20_shops_detail -q
```

If it passes locally but the app still asks the user to choose between "门店销售金额" and "过单金额（明细）", investigate deployment/index drift:

- Is the running process using the same repo revision?
- Was the service restarted after code changes?
- Was `backend/retrieval_index/` rebuilt when semantic aliases changed?
- Does the deployed `backend/config/semantic.yaml` match local?
- Does the deployed planner include the multi-metric and ambiguity-skip fixes?

Do not stop at "local test passes" if the user-reported app behavior is still wrong. Find and close the actual path that the user is hitting.

## Core P0 Case

This exact question must never trigger a "choose one" clarification:

```text
2026年1月东一区销售金额最高的20个门店是哪些？列出门店名称、经销商、城市、导购姓名、销售数量、销售金额和过单金额。
```

Expected intent:

- table: `ads_bi_hs_sale_info_df`
- time filter: `acc_month='2026-01'`
- region filter: `lev2_name='东一区'`
- dimensions: `shop_name`, `dealer_name`, `official_city AS city`, `guide_name`
- metrics: `SUM(shop_sale_qty)`, `SUM(shop_sale_amount)`, `SUM(gd_amount)`
- order: `shop_sale_amount_total DESC`
- limit: `20`
- clarification: `needs_clarify=false`

Positive SQL shape:

```sql
SELECT shop_name,
       dealer_name,
       official_city AS city,
       guide_name,
       SUM(shop_sale_qty) AS shop_sale_qty_total,
       SUM(shop_sale_amount) AS shop_sale_amount_total,
       SUM(gd_amount) AS gd_amount_total
FROM chatbi.ads_bi_hs_sale_info_df
WHERE acc_month='2026-01'
  AND lev2_name='东一区'
GROUP BY shop_name, dealer_name, official_city, guide_name
ORDER BY shop_sale_amount_total DESC
LIMIT 20;
```

If generated SQL quotes identifiers, omits schema, or formats whitespace differently, that is fine. The intent, table, filters, selected fields, grouping, ordering, and limit are not negotiable.

## Deterministic Planner Fixes

Fix these before adding or expanding Agent logic.

### Multi-Metric Is Not Ambiguity

When the question contains "列出 / 列举 / 显示 / 展示 / 包含 / 包括 / 分别 / 和 / 以及 / 同时" and multiple metric aliases appear, treat them as requested output metrics, not competing口径.

Required behavior:

- Parse `requested_metrics`.
- Parse `ranking_metric` separately from output metrics.
- Put non-primary requested metrics into `extra_metrics`.
- Keep `needs_clarify=false` when the plan is structurally executable.
- Never ask the user to choose one when the user explicitly asked for both.

### Ranking Metric Rules

For "销售金额最高的20个门店", the ranking metric is the metric closest to "最高/最低/最多/最少/TopN/前N".

Examples:

- "销售金额最高的20个门店" -> order by sales amount desc, limit 20.
- "转新率最低的5个大区" -> order by rate asc, limit 5.
- "差异最大的前10个大区" -> calculate difference and order by absolute difference desc.

### Grain And Table Selection Rules

Use table grain to select the table, not only the metric alias.

- If the user asks for 门店、导购、经销商、城市 plus sales detail fields, use `ads_bi_hs_sale_info_df`.
- If the user asks for concrete store ranking but not guide/dealer detail, use a store-level detail table that has store fields.
- If the user asks high-level region/province/city management sales, prefer summary tables.
- If the user asks target/achievement, use target table or a supported join/derived plan only when dimensions match.
- Do not use a table that cannot provide requested dimensions.

### Sales And GD Amount Role Mapping

"销售金额" is not always the same physical field. Resolve it by grain:

- Detail table with shop/guide/dealer context -> `shop_sale_amount_total`.
- Summary management context -> `terminal_sale_amount_total`.
- Target context -> `shop_sale_amount_actual_total`.

"过单金额" is also grain-sensitive:

- Detail table context -> `gd_amount_total`.
- Summary management context / high-level restored order amount -> `reduction_gd_sale_amount_total`.
- Target context -> `gd_amount_actual_total` or `gd_target_total` depending on actual vs target wording.

Add or adjust aliases in `backend/config/semantic.yaml` only if the deterministic planner and tests prove the change improves disambiguation. Rebuild or refresh retrieval artifacts if semantic text changes affect runtime retrieval.

### Clarification Rules

Clarify only when answering would be materially unsafe.

Do clarify when:

- No metric can be inferred.
- The user asks a single vague question and top metrics are genuinely indistinguishable.
- Required time or entity scope is missing and no safe default exists.
- The requested dimensions cannot exist together in any supported table.

Do not clarify when:

- The user explicitly lists multiple metrics to output.
- The plan has a valid metric, valid table, valid dimensions, valid filters, and valid time range.
- The issue is that LLM returned `needs_clarify=true` despite a structurally complete plan.
- The top-2 retrieval scores are close but the user named both metrics or requested both columns.

Revise `_maybe_ambiguity_clarify` so it skips ambiguity prompts for explicit multi-metric questions and for structurally complete field-list questions.

### Derived Metrics And Comparisons

Close these known high-risk patterns:

- Difference questions must compute the difference, not just list two metrics.
- "差异最大/差距最大" must sort by absolute difference unless the wording says otherwise.
- Rates must use numerator / denominator, not one component metric.
- Percent thresholds must normalize `90%` and `90` to `0.9` for rate metrics.
- "同比/环比" must include the correct comparison period and must not silently compare to missing data.
- "本月/上月/最近" must resolve to table-specific available data, or return a clear no-data/data-range message.

### Follow-Up Context

Maintain multi-turn correctness:

- Follow-ups like "继续下钻到省区" must inherit metric, time, filters, calculation, and ordering unless the new question explicitly changes them.
- Follow-ups like "这3个大区" must carry over the actual previous result set, not just the number 3.
- A new explicit time/entity/metric should intentionally switch context and record why.

## Accuracy Critic Agent

Add a controlled critic after deterministic plan repair. Prefer a module name like `backend/app/core/nl2sql/accuracy_critic.py`.

The critic must be structured and auditable.

Input:

- original question
- retrieval candidates
- semantic table/metric/dimension definitions relevant to the plan
- QueryPlan
- compiled SQL preview when available
- optional previous plan/result shape

Output JSON:

```json
{
  "ok": true,
  "severity": "none|warn|fail",
  "reason": "",
  "missing_metrics": [],
  "missing_dimensions": [],
  "wrong_table": "",
  "wrong_filters": [],
  "wrong_order_by": "",
  "wrong_limit": "",
  "clarify_should_be_suppressed": false,
  "repair_hints": []
}
```

Rules:

- Use deterministic checks first; call LLM only for ambiguous semantic review.
- Trigger critic only for risky cases at first: multi-metric, field-list, TopN, derived metric, follow-up, low confidence, or planned clarification.
- Permit at most one automatic repair pass.
- If the critic says `fail` after one repair, return a clear safe error or clarification; do not guess.
- Log critic output for audit, but do not expose raw internal prompts to users.

## Result Validator

Add post-execution validation before final answer generation.

Validate:

- Required requested columns are present.
- Result row count matches TopN expectation when enough data exists.
- Sort order matches requested direction.
- Required metrics are non-null or explain why they are null.
- Zero-row results distinguish no data, wrong time range, permission filtering, and overly narrow filters.
- The answer narrative references the returned data and does not invent unsupported numbers.

For no-data caused by stale relative periods, return an explicit data-range message or use the approved table-specific latest partition rule. Never silently answer from an empty result as if it were true.

## Accuracy Benchmark

Create or extend a machine-readable benchmark under `backend/tests/fixtures/` or `output/audit/` with at least:

- all 26 existing strict-audit turns
- the core P0 case above for both 2025-01 and 2026-01
- at least 100 high-frequency business questions covering:
  - sales amount, sales quantity, GD amount
  - target, actual, achievement rate
  - region, sub-region, city, shop, guide, dealer
  - TopN and BottomN
  - multi-metric output
  - ratios and differences
  - YoY/MoM/trend
  - multi-value filters
  - follow-up drilldown and "these N items" carryover
  - no-data / data-range cases

Each case must assert business intent, not just HTTP success:

- expected table or allowed table set
- expected selected metrics
- expected dimensions
- expected filters
- expected time range
- expected order by
- expected limit
- expected no-clarify / clarify reason
- expected SQL fragments or forbidden fragments

Acceptance:

- 100% pass on known P0 audit failures.
- At least 90% pass on the expanded accuracy benchmark.
- No known regression in existing unit/API tests.

## Required Regression Tests

At minimum, add or preserve tests for:

- Core P0 case: East 1 region January Top20 shops with shop/dealer/city/guide/qty/sales/GD amount, no clarification.
- Multi-metric explicit output must suppress ambiguity prompts.
- Sales amount role mapping across summary/detail/target tables.
- GD amount role mapping across detail/summary/target contexts.
- Field-list dimension extraction from "列出/包含/包括".
- Ranking metric separate from extra output metrics.
- Low-confidence LLM plan with complete structure must execute, not clarify.
- Detail-dimension request must not select a summary table missing requested fields.
- "本月" on a table with no current data must not return an empty success as a real answer.
- Result Validator must catch missing output columns and wrong ordering.

## Existing Audit Failures To Keep Closed

Do not regress these known failures:

- Difference questions compute and sort by difference.
- "只看1段、2段、3段" uses all requested stages and groups by stage.
- "鹤礼3.0" uses dedicated fields.
- "转新率" calculates and sorts/filters by rate.
- "2025年1月东一区销售额TOP20门店" returns store-level rows from detail data.
- "有导/非导门店" groups by guide-shop flag and returns amount, quantity, GD amount, and ratio.
- Relative period questions do not silently query unavailable data.

Use `references/positive_sql_examples.json` as the positive-example source.

## User-Facing Behavior

When clarification is truly needed, ask a business-specific question. Avoid misleading binary choices.

Bad:

```text
门店销售金额 / 过单金额，请选择其一
```

Good only when needed:

```text
您是想按销售金额排序，同时展示过单金额，还是想按过单金额排序？
```

For the core P0 case, do not ask this. The wording says sales amount is the ranking metric and GD amount is an output metric.

## Required User-Perspective Retest

After fixes, start the local service and retest as a user, not only through unit tests:

```bash
bash scripts/start_dev.sh
```

Verify at least:

- Login with local admin credentials from local configuration; do not print secrets.
- Ordinary chat question submission.
- SQL details show the correct table, filters, grouping, order, and limit.
- Core P0 case returns answer data without clarification.
- A genuine ambiguous question still asks a useful clarification.
- Feedback, export, conversations, and folders still work.
- SmartQ dataset button and dataset dialog still work.
- Expert page bootstrap and one controlled job submission still work.
- Logs, permissions, users, LLM settings pages still load.
- Mobile viewport is nonblank and has no major overlap.

Capture concise evidence in the final report.

## Verification Gate

A fix is not complete until these pass or the final report explains exactly why a command could not run:

```bash
git diff --check
backend/.venv/bin/python -m pytest backend/tests/test_audit_nl2sql_0622.py::test_case9_top20_shops_detail -q
cd backend && APP_ENV=test ../backend/.venv/bin/python -m pytest tests/test_audit_nl2sql_0622.py tests/test_context_and_security.py tests/test_scope_accuracy.py -q
cd backend && APP_ENV=test ../backend/.venv/bin/python -m pytest tests -q
cd backend && APP_ENV=test ../backend/.venv/bin/python -m pytest -q
cd frontend && npm run build
bash scripts/start_dev.sh
```

Then run the expanded accuracy benchmark. The report must include:

- total cases
- pass count
- fail count
- warn count
- accuracy percentage
- list of remaining failed cases with exact cause

Stop the local service after retesting if it was started only for verification.

## Final Fix Report

Write a short artifact under:

```text
output/claude_fix_report/
```

Include:

- Before/after intelligent-question-answering accuracy.
- Whether the reported core P0 case was reproduced locally and through the running app.
- Closed issue list by severity.
- Files changed and why.
- Tests and smoke checks run, with pass/fail results.
- Expanded benchmark result and whether it reached at least 90%.
- Remaining risks or intentionally deferred structural work.
- Any trace IDs or screenshots that prove user-facing behavior changed.

The user expects strict enterprise-level repair. If a finding is intentionally not fixed, document the concrete reason, residual risk, and exact follow-up work needed.
