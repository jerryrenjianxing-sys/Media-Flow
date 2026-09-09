# Task 2 report — 消息巡检与控制台纠错

Status: implemented; focused desktop QA passed. Base: 86bb1f9. No device calls, paid model calls, live DB/service writes, packaging, version bump, push, or Skill/glyph edits.

## Changes

- Shared inspection resolver combines frozen nested/flattened payload, actual receipt, task status and workflow version. Pending has 尚未检查, running uses observed phase or explicitly awaits a phase receipt, missing start timestamps are not 尚未开始, final failures retain actual task.error. Conflicts expose diagnostics and never mix result layouts. Missing results never create legacy failed sections.
- New UI submissions freeze home_badge in workbench planning/submission; legacy normalization and executor APIs remain compatible. Existing drafts are returned untouched. Loading any preset stages it locally with a visible migration explanation; only explicit 保存为当前草稿 resumes saving. Old stored presets and task snapshots remain unchanged.
- Active feature labels use 消息巡检, including navigation and receipt-detail heading. Removed old detailed-mode switch, visitor-preflight controls, recovery/reverification buttons, and device legacy recheck entry. Historical detailed sections remain read-only and include only actually saved sections.
- Homepage inspection cards/live tasks do not render video progress/capability counters. Inspection-only group summaries ignore leftover video/model aggregates. Video capability messages use Chinese and specify 本设备/本批次 scope.
- Quantity presentation preserves 3 and literal 99+, distinguishes dot/unreadable/conflict metadata, supports quantity_source/badge_bounds/rule_version and badge_crop evidence labels. Missing historical quantity metadata says the receipt did not record reliable quantity, not that the image has no visible number.
- Run page begins with —/未读取 and disabled controls. Failed refresh retains explicitly stale observed data, does not claim service is stopped or a healthy empty queue, and provides working retry. Records/receipt initial reads no longer claim empty results. Receipt detail fetch failures are caught with feedback.
- Restored record-row column ownership: processed summary stays in the main column; status pills retain horizontal text. Compact metric warning chips override the global warning-box style. Notification label/count/content are immediately visible and clickable, with no offscreen marquee. Existing styles/framework preserved.
- Operation-aware stop success: stop_batch cancelled is successful only for that operation; unknown and unrelated cancelled operations remain unsuccessful. Repeated stopped/cancelled stop_task reports the original terminal state without replay. Session/batch stop removes task_waits only for task IDs terminalized by that transaction, preserving checkpoints and historical/other task waits.

## Red → green evidence

- inspection-display tests first failed on old active label, absent resolver and unreadable quantity rendered merely 有消息. Later conflict-receipt test failed with 有消息 instead of 版本冲突. All pass after implementation.
- Real React rendered tests first showed pending home_badge displaying video progress, like/favorite codes and four fabricated old failed sections; historical card synthesized absent categories and recovery controls. Additional failing tests caught initial healthy run/empty record claims, unknown start times and leftover model counters. Final component tests pass.
- Isolated real SQLite/request-store tests first returned ok=false for successful cancelled batch and repeated task stop; waiting metadata remained 3 rows instead of 0; old UI draft preview incorrectly applied v3 device constraints. All pass. Stop tests additionally verify preserved checkpoints, historical/running waits, same-request cached receipts and different-request repeated stop without new work.
- Desktop Playwright geometry assertion caught global .warning:not(.dot) specificity leaving model chips 41px high. Corrected selector; final chips <30px and status pills 26px high.

## Validation

- openspec validate refine-message-inspection-and-console --strict: passed.
- Python: main .venv/Scripts/python.exe -m unittest test_dev40_controls test_run_planning test_agent_platform test_automation.AutomationTests: 53 passed. Tests use temporary stores and fixture launchers only.
- Frontend: main work/agent-runtime/node/node.exe --test tests/*.test.mjs: 58 passed; npm run typecheck, npm run lint, npm run build: passed.
- Frontend-testing-debugging, React best practices and TDD skills used. Browser plugin/browser skill absent, so regular Playwright used. Initial vinext dev dynamic browser-entry import failed; stopped only our dev process and switched to the built standalone production server. Component test Vite cache now uses its own directory.
- Current candidate: http://127.0.0.1:33040, production standalone, exec session 45478. Start command: PORT=33040 HOST=127.0.0.1 node dist/standalone/server.js. Left running for root desktop matrix.
- Targeted flow: records → detail → pending/running/failed/completed/conflict cards; receipts → 3/99+/dot/unreadable → crop detail → close; workbench → old preset load (0 writes) → explicit save (1 intercepted write, home_badge, original preset unchanged); run → refresh failure with stale values → reload unread → successful retry.
- Final desktop run: ui-task2.cjs, 1366×900, light theme, all API requests intercepted. Passed page content, meaningful rendered state, no framework overlay, no page errors, quantity/card assertions, row/chip geometry, dialogs, preset persistence and retry controls. No real API writes; only one in-memory draft save per run. Final results: C:/Users/jerry/Documents/MediaFlow-Task-Prep/dev40-audit/ui-task2/results.json.
- Screenshots reviewed: ui-task2/records-1366.png, inspection-states-1366.png, quantities-1366.png, quantity-detail-1366.png, preset-staged-1366.png, run-stale-1366.png, run-unread-1366.png. Private script/fixtures/images remain outside Git.

## Limits and handoff

- User subsequently narrowed UI scope to desktop. Earlier completed 390px checks remain evidence only; no further mobile work after clarification. Final rerun is desktop only. Root owns 1366/1920, light/dark, 125%/150% full-site matrix and formal review.
- Isolated synthetic new badge/state receipts exercise UI branches; saved real historical receipts remain unchanged. This is not a claim of live device availability or new badge recognition accuracy.
- Existing source-presence tests that required removed old UI switches/recovery actions were removed or updated to the new active labels; rendered behavioral coverage replaces their obsolete assertions. Historical acceptance documents were not rewritten.
- Root-owned batch-evidence-integrity specification remains unstaged; no root files included in this scoped implementation commit.
