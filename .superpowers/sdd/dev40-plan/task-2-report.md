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

## Review correction — 2026-09-09

Baseline: `c1689b591f12072e3df67a8b0c6d4221a92212d5` (includes root documentation/specification work). Implementation head: `caf0509c94404c1159bd8be8ff8443c513926518`. This addendum is a separate report-only commit; the original acceptance evidence above remains historical.

Read task-2-review-report.md completely and addressed both Important findings and the Minor mode-classification finding, plus the approved management-link naming correction. Receiving-code-review, TDD and verification-before-completion skills guided reproduction, bounded correction and fresh verification.

- Restored shared build_preview to preview the caller's frozen inspection mode. A dedicated build_workbench_preview boundary selects home_badge without modifying the saved draft, and the workbench API now imports that boundary. UI submission remains home_badge. Compatibility AgentPlatform preview and confirmation now use the same legacy configuration, hash and eligibility gates.
- Added real AgentPlatform temporary-store tests: unsupported legacy mode is blocked and confirmation creates no tasks/launcher calls; a standard isolated VM previews legacy with a distinct hash from home_badge, then confirmation freezes v3/legacy tasks with the previewed inspection count. Existing saved UI legacy draft preservation/home submission regression remains passing.
- Unified inspection summary text for list/detail and inspection-only status labels. Counts come from inspection counters; cancelled/stopped are terminal, active running/pending/waiting status takes priority over older failure/degraded counts. Mixed groups use inspection_status rather than the whole group's video state. Absent mixed inspection status explicitly remains unrecorded.
- Failure-only receipts without frozen mode remain unknown, display 检查失败/巡检模式未记录, retain the saved failure detail and never claim historical legacy. Receipt overview uses this diagnostic too. Management link now reads 消息巡检与历史记录; destination and behavior unchanged.

### Fresh red → green and validation

- Before fixes, both new AgentPlatform tests failed: unsupported legacy was ready, and legacy/home previews had identical hashes. After fixes, both pass through the real planning and confirmation methods using only a temporary SQLite store and mocked worker launcher.
- Before display fixes, the new resolver and rendered unknown-card tests showed 旧版详细巡检 · 历史只读; the new actual TaskGroupList test showed cancelled inspection as 等待检查. After fixes, all pass. Aggregate test exercises 18 pure/mixed combinations: cancelled, stopped, partial_failed, partial_degraded, running, pending and waiting_model/device/user, including older failure/degradation counters alongside active states.
- Python: `python -B -m unittest test_dev40_controls test_run_planning test_agent_platform test_automation.AutomationTests` — 55 passed, 0 failures. Same main .venv interpreter as original report, temporary stores only.
- Frontend: `node --test --test-reporter=spec tests/*.test.mjs` — 61 passed, 0 failures. Focused inspection files — 19 passed. `npm run typecheck`, `npm run lint`, `npm run build` — passed. Typecheck initially found the new nullable aggregate status guard; corrected before the final passing run. `git diff --check` passed.
- Rebuilt production candidate at the same `http://127.0.0.1:33040`. Stopped only owned prior session 45478; current exec session is **26637**, same standalone start command and working directory. Root has been notified and owns affected-view browser review and the already completed 144-case desktop matrix; this correction did not repeat that matrix or any 390px work.
- No live API/database writes, device/model actions, migrations, historical receipt modifications, packaging, version bump, push or broader redesign. Concurrent root specification changes were left unstaged and excluded from the implementation commit.
