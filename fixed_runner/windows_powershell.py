from __future__ import annotations

import os
from pathlib import Path


def native_windows_powershell_environment() -> dict[str, str]:
    """Prevent foreign PowerShell modules from shadowing Windows inbox modules."""

    environment = dict(os.environ)
    windows = Path(environment.get("WINDIR", r"C:\Windows"))
    user_profile = Path(environment.get("USERPROFILE", str(Path.home())))
    program_files = Path(environment.get("ProgramFiles", r"C:\Program Files"))
    module_paths = [
        user_profile / "Documents" / "WindowsPowerShell" / "Modules",
        program_files / "WindowsPowerShell" / "Modules",
        windows / "System32" / "WindowsPowerShell" / "v1.0" / "Modules",
    ]
    environment["PSModulePath"] = os.pathsep.join(str(path) for path in module_paths)
    return environment
