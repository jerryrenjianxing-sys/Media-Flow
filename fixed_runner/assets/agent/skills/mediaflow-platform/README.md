# MediaFlow Platform Skill

This package connects an agent host to a local MediaFlow automation service. It is
host-neutral: no particular chat UI, model provider, or agent runtime is required.
The bundled client uses only the Python standard library and accepts loopback HTTP
addresses only.

## Install

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
`MEDIAFLOW_PYTHON`, the `python` value in local config, then `python3` or `python`
from `PATH`. Any host may invoke `scripts/mediaflow.py` with its configured Python
3 runtime directly.

```powershell
./scripts/mediaflow.ps1 platform_status
./scripts/mediaflow.ps1 plan_tasks --arguments-file ./examples/plan-arguments.json --request-id plan-example-001
```

The example plan contains a placeholder device ID and must not be executed as-is.
Read `SKILL.md` before performing any operation. Write requests are never retried
automatically; preserve the receipt database and query uncertain results.
