from __future__ import annotations

import json
import uuid
from pathlib import Path

from runtime_layout import DATA_ROOT


IDENTITY_PATH = DATA_ROOT / "installation.json"


def installation_id(path: Path = IDENTITY_PATH) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = str(payload.get("installation_id") or "").strip()
        if value:
            return value
    except (OSError, ValueError, TypeError):
        pass
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "installation_id": value}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return value
