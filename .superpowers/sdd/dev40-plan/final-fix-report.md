# dev40 final bounded fix report

Starting commit: `0cf66da`. Scope: the one Important and two Minor findings in `final-review-report.md`, `task-2b-review-report.md`, and `task-3-review-report.md`. Root's acceptance document and synchronized main specifications are excluded from this commit. Root owns final whole-suite QA, re-review, specification closure and local delivery.

## Changes

- Both full/public initialization and compact status now expose optional `legacy_inspection_recheck`: true only when the stored options request an inspection recheck and do not explicitly select `home_badge`. This matches the existing executor's missing-mode legacy default. Device guidance and the final active-preparation presentation omit legacy continuation actions. Both inventory and screen-card continuation controls, plus the shared onboarding dispatch/continuation handler, use this marker. Waiting legacy screen cards retain safe cancel; report links and management remain available. Fresh ordinary initialization and home_badge preparation retain existing behavior. No stored options, continuation API or historical records change.
- Current comment raw-response evidence is published after the raw write succeeds. Decision JSON includes its prospective path, but shared evidence receives that decision path only after successful writing. A decision-write failure therefore keeps current input/raw evidence and a null unavailable decision path, without a send or another generation attempt.
- Skill crop guidance now selects the actual public `section=home_badge` plus `label=消息角标原始裁剪`, then uses the returned ID and URL. The integration fixture obtains both evidence entries from the formal `EngagementInspector` on an offline homepage screenshot. Those entries pass through the stored inspection and real public handler; the exact documented rule finds the crop, preserves a separate original, and downloads both returned URLs as PNG. Synthetic quantity/state cases remain serialization checks, not new recognition-accuracy claims. No production evidence API fields were added.

## Regression evidence

Before production fixes, focused tests failed on the missing legacy marker, the non-null decision path after a forced failed decision write, and the old documented crop discriminator. The rendered production device page exposed legacy continuation controls. Initial test-fixture issues (frozen dataclass mutation and an omitted HTTP header delegate) were corrected; neither required production changes. The supported-mode render assertion was adjusted to cover both existing desktop device-card labels.

Final focused run: 11 Python tests passed, plus 2 real rendered-component tests. The backend matrix covers queued, running, waiting_user, failed, cancelled and ready; options cover implicit legacy, explicit legacy, home_badge and ordinary preparation. Direct compatibility continuation keeps the original ID and options. Rendering seeds only initial read state through a Vite test transform, then renders the real devices page and onboarding dialog across those states. Missing optional marker remains compatible. The write-failure test checks raw/current-video identity, null decision path, preserved attached structured model error, one generation and zero sends.

## Validation

- `openspec validate refine-message-inspection-and-console --strict`: passed.
- `openspec validate --all --strict`: 48 passed, 0 failed.
- Main-venv isolated runner `.superpowers/sdd/dev40-plan/task-2b-tests.py` with `test_control_api test_device_initialization test_comment_evidence test_comment_ai test_model_resilience test_long_batch_execution test_worker test_home_badge test_skill_bundle test_platform_skill_download`: **261 tests passed in 64.327s**.
- Main bundled Node: `npm run typecheck`, `npm run lint`, `npm run build`: passed.
- `node --test tests/*.test.mjs`: **63 passed, 0 failed** (includes the new rendered regression and existing built-page tests).
- `git diff --check`: passed; Git reports only normal working-copy LF/CRLF conversion notices.

No child agents, running-service changes, device actions, live model requests, runtime-record edits, push or release package operations. Test HTTP handlers use temporary stores and ephemeral loopback ports. Windows desktop only; browser and local-update acceptance remain with root. No remaining known finding within this three-item fix scope; whole-branch integration judgment remains with re-review.
