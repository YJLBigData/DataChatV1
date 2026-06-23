---
name: claude-datachat-audit-fix
description: Strict DataChatV1 repair workflow for Claude Code. Use when fixing the local DataChatV1 repository from the bundled audit workbook, NL2SQL positive/negative examples, UI/API smoke-test evidence, project-structure findings, and required verification gates after a strict user-perspective audit.
---

# Claude DataChat Audit Fix

## Goal

Fix DataChatV1 strictly against the bundled audit evidence, not from memory or guesswork. Treat intelligent question answering accuracy as the release blocker: SQL that returns `ok=true` but answers the wrong business question is a P0 defect.

Work from the DataChatV1 repo root, normally:

```bash
cd /Users/yangjinlong/app/PythonProject/DataChatV1
```

## Required Evidence

Before editing code, read these files from this skill:

- `references/DataChatV1_strict_audit_20260622.xlsx`: human-facing audit workbook with issue list, function testing, sample question judgments, and structure recommendations.
- `references/audit_report_data.json`: structured audit data. Use it as the machine-readable source of issue severity, function-test status, and question rows.
- `references/chat_sample_raw_results.json`: raw intelligent-question-answering responses, SQL, and data returned by the app.
- `references/positive_sql_examples.json`: executable positive SQL examples for failed or risky sample cases.
- `references/source_ai智能问数测试样例.xlsx`: original user-provided sample questions.

If the workbook and JSON disagree, prefer the JSON for exact fields and preserve the workbook wording in the final report.

## Guardrails

- Do not push, deploy, or change production configuration.
- Do not commit secrets, `.env`, runtime SQLite/MySQL data, logs, screenshots, or generated caches unless the user explicitly asks.
- Preserve existing user changes. Start with `git status --short` and inspect relevant diffs before touching a file.
- Do not patch `backend/web/static/...` by hand. Change frontend source and run the frontend build.
- Do not suppress failures by weakening tests, hiding SQL, or converting backend errors into fake success states.
- Keep data semantics rigorous. Local verification runs against the configured `chatbi` MySQL sample store, but exposed SQL/business logic must remain compatible with the product's intended Dataphin/MaxCompute data-development usage.

## Repair Order

1. Reproduce and freeze failing behavior with tests.
2. Fix P0 intelligent-question-answering correctness defects.
3. Fix P0/P1 runtime safety and test-suite defects.
4. Fix P1/P2 dependency, frontend error, and structure defects with minimal safe refactors.
5. Re-run the full verification gate and write a final fix report.

Do not start broad refactors until P0/P1 behavior is covered by tests.

## Mandatory Findings To Close

### P0: Wrong SQL Marked Successful

The Excel sample audit had 26 turns: 11 PASS, 2 WARN, 13 FAIL. All HTTP calls were technically successful, so backend success is not enough. The app must distinguish answer correctness from request completion.

Add regression tests that read either the bundled Excel or a derived fixture from `audit_report_data.json` and assert expected SQL intent, result shape, and follow-up context behavior.

Close these known failures:

- Difference questions must compute the difference, not just list two metrics.
  - Positive pattern: `SUM(terminal_sale_amount) - SUM(reduction_gd_sale_amount) AS diff_amount`.
  - Sort by absolute difference for "差异最大/排名" questions.
- "只看1段、2段、3段" must use all three requested stages.
  - Positive pattern: `item_dan_name IN ('1段','2段','3段')`.
  - Include `item_dan_name` in selected columns and grouping.
- "鹤礼3.0" questions must use the dedicated 鹤礼3.0 fields.
  - Positive fields: `heli30_new_customer_num`, `heli30_repurchase_in_60_days_num`.
  - Rate pattern: `SUM(heli30_repurchase_in_60_days_num) / NULLIF(SUM(heli30_new_customer_num),0)`.
- "转新率" questions must calculate the rate and sort/filter by the rate.
  - Positive pattern: `SUM(potential_to_new_num) / NULLIF(SUM(potential_num),0) AS potential_to_new_rate`.
  - "最低/倒数" requires ascending rate order, not ordering by potential count.
  - Follow-ups like "这3个大区" must retain the prior bottom/top region set.
  - "潜客数超过50且转新率低于5%" must use `SUM(potential_num) > 50` and rate `< 0.05`.
- "2025年1月东一区销售额TOP20门店" must return 20 store-level rows.
  - Use `ads_bi_hs_sale_info_df`, `acc_month='2025-01'`, `lev2_name='东一区'`.
  - Group by `shop_name, dealer_name, official_city, guide_name`.
  - Order by `SUM(shop_sale_amount)` descending and limit 20.
- "导购店和非导购店销售额" must group by guide-shop flag.
  - Group by `is_guide_shop`.
  - Return amount, quantity, GD amount, and ratio over total sales amount.
- "本月" must not silently use a month with no data. Resolve table-specific latest partitions or validate against available data range before querying.

Use `references/positive_sql_examples.json` as the exact positive-example source.

### P0: SSE Cancellation And Concurrency

Audit finding: `/api/chat/stream` releases in-flight guards on client disconnect while the worker can continue LLM/DB work in an executor. A burst of aborted requests can bypass concurrency protection.

Fix requirements:

- Tie guard release to worker terminal state, or pass a cancellation signal through the streaming worker.
- Ensure client disconnect cancels or marks the task so no orphaned LLM/DB work continues unchecked.
- Add a backend regression test with a mocked slow worker/future and disconnect path.

### P1: Full Pytest Collection Fails

Audit finding: `backend/app/core/llm/test_runner.py` exposes functions named `test_bailian`, `test_feihe`, and `test_preset_config`, causing `pytest -q` to collect them as tests and error on missing fixtures.

Fix by renaming the probe module/functions or setting `__test__ = False`. Do not ignore the full-suite error.

### P1: Dependency Install Drift

Audit finding: `start.sh` only import-checks a small subset of dependencies; `backend/requirements.txt` can change without reinstalling. The venv had drift for required packages.

Fix requirements:

- Track a requirements hash or run a stronger requirement validation.
- Ensure `bash scripts/start_dev.sh` installs missing/changed backend dependencies predictably.
- Add a focused script/test check if practical.

### P1: Semantic Data Range Drift

Audit finding: `backend/config/semantic.yaml` declared latest data as `2026-05`, while local core sample tables had max data around `2026-04`. "本月各大区销售额排名" generated a no-row query.

Fix requirements:

- Use table-specific max month/partition metadata when resolving relative periods.
- If the requested period has no data, return a clear data-range message or adjust only when the business rule permits it.
- Add regression tests for "本月" with sample max-month fixtures.

### P2: Project Structure

The structure audit flagged concentrated files:

- `backend/app/main.py` around 1100 lines.
- `backend/app/core/nl2sql/planner.py` around 1249 lines.
- `backend/app/core/orchestrator.py` around 717 lines.

Do not do a risky rewrite. Split only where it supports the fixes and tests:

- Move chat routes, streaming/SSE control, and conversation/export concerns into focused routers/services.
- Split NL2SQL planning into time parsing, metric resolution, dimension/context carryover, ranking/filtering, and validation modules.
- Keep public API compatibility and import paths stable where possible.

### P2: Frontend Error Hygiene

Audit finding: `frontend/src/api/http.ts` surfaces backend `body.error` too directly. Avoid leaking raw downstream/technical errors.

Fix requirements:

- Map known errors to user-safe messages.
- Preserve trace IDs or details for logs/debug panels, not ordinary toast text.
- Add a small frontend/unit-level or API-client regression where feasible.

## Required User-Perspective Retest

After fixes, start the local service and retest as a user, not only by unit tests:

```bash
bash scripts/start_dev.sh
```

Verify at least:

- Login with local admin credentials from local configuration; do not print secrets.
- Chat question submission, SQL details, feedback button, export button.
- SmartQ dataset button and dataset dialog.
- Expert page bootstrap and one controlled job submission.
- Conversations and folders CRUD.
- Logs, permissions, users, LLM settings pages.
- Mobile viewport nonblank and no major overlap.
- `smartq_cube_ids: null` and empty/omitted cases still work.

Capture concise evidence in the final report. Screenshots are optional unless a visual defect was changed.

## Verification Gate

A fix is not complete until these pass or the final report explicitly explains why a command could not run:

```bash
git diff --check
cd backend && APP_ENV=test ../backend/.venv/bin/python -m pytest tests -q
cd backend && APP_ENV=test ../backend/.venv/bin/python -m pytest -q
cd frontend && npm run build
bash scripts/start_dev.sh
```

Then re-run the Excel intelligent-question-answering sample evaluation. Acceptance target:

- No known FAIL remains.
- WARN entries are allowed only if the final report gives a business reason and the returned SQL/data still answers the user's intent.
- Raw HTTP `ok=true` alone never counts as pass.

Stop the local service after retesting if you started it only for verification.

## Final Fix Report

Write a short artifact under:

```text
output/claude_fix_report/
```

Include:

- Before/after intelligent-question-answering counts.
- Closed issue list by severity.
- Files changed and why.
- Tests and smoke checks run, with pass/fail results.
- Remaining risks or intentionally deferred structural work.
- Any screenshots or trace IDs that prove UI behavior changed.

The user expects strict enterprise-level repair. If a finding is intentionally not fixed, document the concrete reason, residual risk, and the exact follow-up work needed.
