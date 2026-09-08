# MediaFlow Platform Skill

This package connects an agent host to a local MediaFlow automation service. It is
host-neutral: no particular chat UI, model provider, or agent runtime is required.
The bundled client uses only the Python standard library and accepts loopback HTTP
addresses only.

## Install

请先识别用户目标。仅收到本技能包、没有其他明确请求时完成 MediaFlow 首次只读接入检查；用户明确只阅读、解释、修改材料，或只定制主题、搜索词、评论文案时，不运行检查，也不要求平台、设备或模型。

Read `SKILL.md`, then run `./scripts/mediaflow.ps1 check` on the user's Windows
computer. This needs no Python, performs one bounded GET, and starts nothing.
Report the connection, registered snapshot with timestamps, and next step. If
unreachable, ask before starting MediaFlow; a timeout does not prove it is stopped.
If local tools are unavailable, explain that and give the command to the user.
After consent, follow [startup guidance](references/setup.md#启动服务仅在用户同意后).

MediaFlow is already installed before this Skill is obtained from its homepage.
For MuMu, display, apps and input preparation, follow [配置引导](references/setup.md).
This section installs the Skill into an external agent, not MediaFlow itself.

1. Extract the archive so the `mediaflow-platform` directory remains intact.
2. Copy or link that directory into your agent host's skills directory. Consult the
   host's documentation for its skill discovery location.
3. Copy `config.example.json` to `config.json` in this directory when configuration
   is needed. Do not put `config.json`, receipts, credentials, device data, or
   screenshots into a redistributed archive.
4. Restart or reload the agent host, then ask it to use `mediaflow-platform`.

## Configure

`api_url` is optional and defaults to `http://127.0.0.1:48138`. Only loopback HTTP
is accepted. `python` is an optional Python 3 command or local path used by the
PowerShell launcher. Environment variables take precedence:

| Setting | Purpose |
| --- | --- |
| `MEDIAFLOW_PYTHON` | Explicit Python 3 executable; highest runtime priority |
| `MEDIAFLOW_SKILL_CONFIG` | Alternate path to the local `config.json` |
| `MEDIAFLOW_API_URL` | Override the local automation service address |
| `MEDIAFLOW_RECEIPT_DB` | Store durable client write receipts outside the package |

On Windows, invoke `scripts/mediaflow.ps1`; it resolves Python in this order:
`MEDIAFLOW_PYTHON`, the `python` value in local config, then the running platform's
`GET /api/automation` client_runtime. No system Python or developer tools are needed.
Discovery uses loopback HTTP without redirects or proxies. Ask the user before starting
MediaFlow if disconnected; update an older platform lacking discovery or retain an explicitly
configured Python. The launcher does not overwrite config. Any host may still invoke
`scripts/mediaflow.py` directly with its configured Python 3 runtime.

```powershell
./scripts/mediaflow.ps1 check
./scripts/mediaflow.ps1 plan_tasks --arguments-file ./examples/plan-arguments.json --request-id plan-example-001
```

The example plan contains a placeholder device ID and must not be executed as-is.
Read `SKILL.md` before performing any operation. Write requests are never retried
automatically; preserve the receipt database and query uncertain results.

## Included business guides and examples

- `references/content-guide.md`: content plans, topic evidence, four content modes,
  search trust, round rotation, comment material, and a complete nut/dried-fruit
  factory example.
- `references/api.md`: exact automation actions, arguments, response fields, and
  nullable task result summary.
- `references/workflows.md`: save/run separation, presets, models, notifications,
  recovery, review, and repair.
- `examples/content-plan-nut-factory.json`: runnable `content_plan_save` arguments.
- `examples/preset-zero-write.json`: runnable zero-interaction `preset_save`
  arguments. Saving either example does not create or run a task.

Examples contain placeholders or reusable fixtures only. They contain no machine
paths, credentials, device IDs, receipts, or runtime data. Use a unique stable
`request_id` for each real write, and never replace it to replay an unknown result.
