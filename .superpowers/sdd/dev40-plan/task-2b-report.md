# Task 2b report — comment evidence and model diagnostics

Status: implemented, self-reviewed, scoped verification passed. Commit recorded in the handoff.

## Scope

- `fixed_runner/execution_tasks.py`: each comment invocation uses a unique video/attempt identity for input, raw response, decision, constraint/assets and failure evidence. Decision/failure events carry `video_index`, input path and accurate generation evidence paths. A failed generation has no raw/decision attribution. No readers required the old generic filenames, so no aliases or historical rewrites were added.
- Failure JSON contains the current input, available pre-recovery screenshot/UI tree, and structured model metadata. Unknown upstream diagnostic fields and model exception bodies are excluded from new comment failure/event/incident output. Successful raw model text remains only in its existing local raw evidence role.
- Comment cleanup errors carry the original structured model cause and evidence. Outer session handling extracts/counts it before incident persistence and recovery outcomes; failed recovery no longer bypasses accounting. Topic handling remains single-counted. Waiting/device behavior and unknown-write non-replay remain unchanged.
- `fixed_runner/comment_ai.py`: preserve an already supplied stage. Distinguish `connect` for ConnectTimeout, `response_read` for ReadTimeout or a request exception while consuming response lines, and `transport` when the location cannot establish more. Existing deadline stages survive the business wrapper. No request count, timeout, repair, provider or retry strategy was changed.
- The OpenSpec delta adds the diagnostic timing interpretation and observed-stage rule. Root owns synchronization to the main spec, task checkboxes and final release acceptance.

`elapsed_ms` is TOTAL logical elapsed across physical requests, backoff and schema repair. A historical 50–60 second number is not the latency of physical request two. New metadata does not recover a raw response that the provider never exposed.

## Exact verification

Working directory for the commands below:
`C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/work/dev24-native`

The scoped runner `.superpowers/sdd/dev40-plan/task-2b-tests.py` uses the same temporary runtime-root, secret/profile and provider-store isolation as `scripts/test-python.py`, but loads only the named tests. All business/device/model work uses local fixtures; existing transport tests use their local fake server. No live device, provider, runtime database or historical evidence was changed.

Initial environment check used default `python -m unittest test_comment_evidence test_comment_ai.BusinessDiagnosticTest -q` from `fixed_runner`. That interpreter lacks uiautomator2; it was not used as the verification interpreter. Metadata tests already reproduced five stage failures there. The proper main `.venv` interpreter was used for all following red/green runs.

Red, after correcting missing-field assertions to fail explicitly:

```powershell
& C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/.venv/Scripts/python.exe -B .superpowers/sdd/dev40-plan/task-2b-tests.py test_comment_evidence test_comment_ai.BusinessDiagnosticTest
```

Result: `Ran 11 tests in 0.320s`, `FAILED (failures=10)`, exit 1. Failures cover missing video/evidence attribution, missing incident model cause, lost model count after cleanup/recovery, raw upstream error leakage, and overwritten connect/read/transport/deadline stages. Ordinary errors, topic single-counting and schema/two-attempt characterization passed. The ten failures include transport subtests.

First green (before additional self-review case): same command; `Ran 11 tests in 0.386s`, `OK`, exit 0.

Self-review red for failure-metadata disk errors (plus a passing lower-stage preservation characterization):

```powershell
& C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/.venv/Scripts/python.exe -B .superpowers/sdd/dev40-plan/task-2b-tests.py test_comment_evidence.CommentEvidenceTest.test_failure_metadata_write_error_does_not_replace_model_cause test_comment_ai.BusinessDiagnosticTest.test_lower_level_cloud_error_stage_is_preserved
```

Result: `Ran 2 tests in 0.069s`, `FAILED (failures=1)`, exit 1. The new JSON write could replace the original model cause with OSError. It is now best effort, records the write error type and a null missing-file path, and preserves the cause.

Final focused green: first command above; `Ran 13 tests in 0.401s`, `OK`, exit 0.

Full relevant modules, once after final production edits:

```powershell
& C:/Users/jerry/Documents/Codex/2026-08-19/wo-c/.venv/Scripts/python.exe -B .superpowers/sdd/dev40-plan/task-2b-tests.py test_comment_evidence test_comment_ai test_model_resilience test_long_batch_execution test_worker
```

Result: `Ran 130 tests in 25.257s`, `OK`, exit 0. Existing unknown-send, confirmed-send/cleanup, retry-sharing, logical deadline and long-batch safety cases pass.

```powershell
openspec validate refine-message-inspection-and-console --strict
git diff --check
```

Result: change valid; whitespace check exit 0. Git reports only its existing LF/CRLF normalization warnings. The complete repository suite and UI/API final QA remain root-owned.

## Self-review and limitations

- Removed shared evidence filenames only after checking source readers. Unique identities also prevent overwrites when the same video index is used again; model retry semantics are unchanged.
- Events/API metadata never embed newly obtained raw failed-provider content. The regression supplies a private upstream message/key and extra diagnostic fields and verifies they do not reach comment events or incidents.
- Counter placement happens before recovery failure returns, while topic errors stay in their existing dedicated handler; there is no second increment in the outer continuation branch.
- No UI, Skill, glyph, version, root documentation or runtime edits. No executor rewrite, new model invocation, device action or history migration.
- The verification-before-completion skill path disappeared from the plugin cache during this task; direct command evidence above was used. TDD and its writing-good-tests reference were read and followed for the changes.
- Remaining integration note: sync the changed `batch-evidence-integrity` delta wording to the main spec during root final integration. No implementation blocker remains in this bounded scope.
