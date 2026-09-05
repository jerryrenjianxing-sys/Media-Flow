from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import shlex
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Protocol

from adb_runtime import connect_loopback_adb
from runtime_layout import DATA_ROOT


MUMU_DOWNLOAD_URL = "https://www.mumuplayer.com/download/"
TESTED_MUMU_VERSIONS = {"6.5.7.0"}
REQUIRED_COMMANDS = {
    "version", "info", "create", "rename", "setting", "control", "adb"
}
STANDARD_RECIPE = {
    "recipe_version": "android15-phone-v1",
    "android_version": "15",
    "cpu": 2,
    "memory_gb": 1.75,
    "width": 900,
    "height": 1600,
    "dpi": 320,
    "fps": 30,
    "root": True,
    "auto_rotate": False,
    "muted": True,
}
PROVIDER_SETTINGS_PATH = DATA_ROOT / "virtualization" / "provider-settings.json"


class VirtualDeviceProvider(Protocol):
    def probe(self, custom_path: str | None = None) -> dict[str, Any]: ...
    def list_instances(self) -> list[dict[str, Any]]: ...
    def create_from_recipe(self, name: str) -> dict[str, Any]: ...
    def read_settings(self, instance_id: str) -> dict[str, str]: ...
    def apply_settings(self, instance_id: str, settings: dict[str, Any]) -> dict[str, str]: ...
    def clone(self, instance_id: str) -> dict[str, Any]: ...
    def export_backup(self, instance_id: str, directory: Path, name: str) -> Path: ...
    def import_backup(self, backup_path: Path) -> dict[str, Any]: ...
    def delete(self, instance_id: str) -> None: ...


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def _no_window_flag() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _registry_value(key: Any, name: str) -> str:
    try:
        value, _ = __import__("winreg").QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value or "").strip().strip('"')


def _path_from_command(value: str) -> Path | None:
    """Extract an executable path from a registry command without running it."""
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith('"'):
        closing = cleaned.find('"', 1)
        if closing > 1:
            return Path(cleaned[1:closing])
    try:
        first = shlex.split(cleaned, posix=False)[0].strip('"')
    except (ValueError, IndexError):
        first = cleaned.split(" ", 1)[0].strip('"')
    return Path(first) if first else None


def _registry_install_candidates() -> list[tuple[Path, str]]:
    if os.name != "nt":
        return []
    discovered: list[tuple[Path, str]] = []
    try:
        import winreg
        roots = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        )
        for hive, root_name in roots:
            try:
                with winreg.OpenKey(hive, root_name) as root:
                    index = 0
                    while True:
                        try:
                            child_name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, child_name) as child:
                                display_name = _registry_value(child, "DisplayName")
                                publisher = _registry_value(child, "Publisher")
                                identity = f"{child_name} {display_name} {publisher}".lower()
                                if "mumu" not in identity and "网易" not in identity and "netease" not in identity:
                                    continue
                                for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    raw = _registry_value(child, value_name)
                                    candidate = Path(raw) if value_name == "InstallLocation" and raw else _path_from_command(raw)
                                    if candidate:
                                        discovered.append((candidate, f"registry:{root_name}:{child_name}:{value_name}"))
                        except OSError:
                            continue
            except OSError:
                continue
    except (ImportError, OSError):
        return []
    return discovered


def _app_path_candidates() -> list[tuple[Path, str]]:
    if os.name != "nt":
        return []
    discovered: list[tuple[Path, str]] = []
    try:
        import winreg
        roots = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
        )
        for hive, root_name in roots:
            try:
                with winreg.OpenKey(hive, root_name) as root:
                    index = 0
                    while True:
                        try:
                            child_name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        if "mumu" not in child_name.lower() and "nemu" not in child_name.lower():
                            continue
                        try:
                            with winreg.OpenKey(root, child_name) as child:
                                candidate = _path_from_command(_registry_value(child, ""))
                                if candidate:
                                    discovered.append((candidate, f"app_paths:{child_name}"))
                        except OSError:
                            continue
            except OSError:
                continue
    except (ImportError, OSError):
        return []
    return discovered


def _start_menu_candidates() -> list[tuple[Path, str]]:
    if os.name != "nt":
        return []
    script = (
        "$shell=New-Object -ComObject WScript.Shell; "
        "$roots=@([Environment]::GetFolderPath('StartMenu'),[Environment]::GetFolderPath('CommonStartMenu')); "
        "foreach($root in $roots){if(Test-Path -LiteralPath $root){Get-ChildItem -LiteralPath $root -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Name -match 'MuMu|网易MuMu'} | ForEach-Object {try {$shell.CreateShortcut($_.FullName).TargetPath} catch {}}}}"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=_no_window_flag(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [(Path(line.strip()), "start_menu_shortcut") for line in completed.stdout.splitlines() if line.strip()]


def _saved_install_candidate() -> tuple[Path, str] | None:
    try:
        payload = json.loads(PROVIDER_SETTINGS_PATH.read_text(encoding="utf-8"))
        value = str(payload.get("mumu_install_root") or "").strip()
        return (Path(value), "last_confirmed") if value else None
    except (OSError, ValueError, TypeError):
        return None


def _save_install_root(manager_path: Path, source: str | None) -> None:
    install_root = manager_path.parent.parent if manager_path.parent.name.lower() in {"shell", "nx_main"} else manager_path.parent
    PROVIDER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROVIDER_SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"mumu_install_root": str(install_root), "detection_source": source}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, PROVIDER_SETTINGS_PATH)


def _registry_install_dir() -> Path | None:
    """Legacy helper retained for callers and tests."""
    candidates = _registry_install_candidates()
    return candidates[0][0] if candidates else None


def _running_process_candidates() -> list[tuple[Path, str]]:
    if os.name != "nt":
        return []
    script = (
        "Get-Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ProcessName -match 'MuMu|Nemu' } | "
        "ForEach-Object { try { $_.Path } catch {} }"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=_no_window_flag(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        (Path(line.strip()), "running_process")
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def _candidate_manager_paths(path: Path) -> list[Path]:
    if path.name.lower() == "mumumanager.exe":
        return [path]
    base = path.parent if path.suffix.lower() == ".exe" else path
    ancestors = [base, *list(base.parents)[:3]]
    suffixes = (
        Path("MuMuManager.exe"),
        Path("nx_main") / "MuMuManager.exe",
        Path("shell") / "MuMuManager.exe",
    )
    return [ancestor / suffix for ancestor in ancestors for suffix in suffixes]


def discover_mumu_manager(custom_path: str | None = None) -> tuple[Path | None, str | None]:
    candidates: list[tuple[Path, str]] = []
    saved = _saved_install_candidate()
    if saved:
        candidates.append(saved)
    if custom_path:
        candidates.append((Path(custom_path).expanduser(), "user_selected"))
    candidates.extend(_registry_install_candidates())
    candidates.extend(_app_path_candidates())
    candidates.extend(_start_menu_candidates())
    candidates.extend(_running_process_candidates())

    environment_roots = {
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    }
    known_relative = (
        Path("Netease") / "MuMuPlayerGlobal-12.0",
        Path("MuMuPlayer-12.0"),
        Path("Netease") / "MuMuPlayer-12.0",
        Path("MuMuPlayerGlobal-12.0"),
    )
    for root in environment_roots:
        if root:
            for relative in known_relative:
                candidates.append((Path(root) / relative, "standard_location"))
    if os.name == "nt":
        try:
            import ctypes
            get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
            fixed_drive_relative = (
                *known_relative,
                Path("Program Files") / "Netease" / "MuMu",
                Path("Program Files (x86)") / "Netease" / "MuMu",
                Path("Program Files") / "Netease" / "MuMuPlayer-12.0",
                Path("Program Files (x86)") / "Netease" / "MuMuPlayer-12.0",
                Path("Netease") / "MuMu",
                Path("Program Files") / "MuMuPlayer",
            )
            for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = f"{letter}:\\"
                if get_drive_type(drive) != 3:
                    continue
                for relative in fixed_drive_relative:
                    candidates.append((Path(drive) / relative, "fixed_drive_standard_location"))
                candidates.append((Path(drive) / "MuMuPlayer", "fixed_drive_standard_location"))
        except (AttributeError, OSError):
            pass

    seen: set[str] = set()
    for supplied, source in candidates:
        for candidate in _candidate_manager_paths(supplied):
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and resolved.name.lower() == "mumumanager.exe":
                return resolved, source
    return None, None


def resolve_mumu_manager(custom_path: str | None = None) -> Path | None:
    return discover_mumu_manager(custom_path)[0]


class MuMuProvider:
    SETTING_KEYS = {
        "name": "player_name",
        "cpu": "performance_cpu.custom",
        "memory_gb": "performance_mem.custom",
        "width": "resolution_width.custom",
        "height": "resolution_height.custom",
        "dpi": "resolution_dpi.custom",
        "fps": "max_frame_rate",
        "root": "root_permission",
        "auto_rotate": "window_auto_rotate",
        "muted": "system_volume_close",
    }

    def __init__(self, manager_path: Path | None = None) -> None:
        self.manager_path = manager_path

    def _run(self, *args: str, timeout: int = 30, allow_nonzero: bool = False) -> CommandResult:
        if not self.manager_path or not self.manager_path.is_file():
            raise RuntimeError("MuMu尚未安装或安装目录无效")
        completed = subprocess.run(
            [str(self.manager_path), *args],
            cwd=str(self.manager_path.resolve().parent),
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=_no_window_flag(),
        )
        def decode(value: bytes) -> str:
            for encoding in ("utf-8", "gb18030"):
                try:
                    return value.decode(encoding).strip()
                except UnicodeDecodeError:
                    continue
            return value.decode("utf-8", errors="replace").strip()

        result = CommandResult(list(args), completed.returncode, decode(completed.stdout), decode(completed.stderr))
        if result.returncode and not allow_nonzero:
            detail = result.stderr or result.stdout or f"exit {result.returncode}"
            raise RuntimeError(f"MuMu命令执行失败：{detail[:500]}")
        return result

    @staticmethod
    def _json(text: str) -> Any:
        start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not start_candidates:
            raise RuntimeError("MuMu返回了无法识别的数据")
        return json.loads(text[min(start_candidates):])

    def probe(self, custom_path: str | None = None) -> dict[str, Any]:
        source: str | None = "configured" if self.manager_path else None
        if custom_path or not self.manager_path:
            path, source = discover_mumu_manager(custom_path)
        else:
            path = self.manager_path
        if path is None:
            return {
                "provider": "mumu",
                "status": "missing",
                "compatible": False,
                "detection_source": None,
                "action_required": "install_mumu",
                "download_url": MUMU_DOWNLOAD_URL,
                "message": "尚未检测到已安装的MuMu。下载完成后还需要运行安装程序。",
            }
        self.manager_path = path
        try:
            version_payload = self._json(self._run("version", timeout=8).stdout)
            version = str(version_payload.get("version") or "")
            help_text = self._run("--help", timeout=8, allow_nonzero=True).stdout
            capabilities = sorted(command for command in REQUIRED_COMMANDS if re.search(rf"\b{re.escape(command)}\b", help_text))
            compatible = set(capabilities) == REQUIRED_COMMANDS
            tested = version in TESTED_MUMU_VERSIONS
            if compatible and path.is_file():
                _save_install_root(path, source)
            return {
                "provider": "mumu",
                "status": "ready" if compatible else "incompatible",
                "compatible": compatible,
                "version": version,
                "manager_path": str(path),
                "install_root": str(path.parent.parent if path.parent.name.lower() in {"shell", "nx_main"} else path.parent),
                "detection_source": source,
                "compatibility_status": "tested" if tested else "capability_verified" if compatible else "unsupported",
                "action_required": None if compatible else "choose_install_directory",
                "capabilities": capabilities,
                "download_url": MUMU_DOWNLOAD_URL,
                "message": (
                    "MuMu已连接，可以添加虚拟机"
                    if tested and compatible
                    else "已通过管理能力验证，可以添加虚拟机"
                    if compatible
                    else f"已检测到MuMu {version or '未知版本'}，但缺少所需管理能力"
                ),
            }
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return {
                "provider": "mumu",
                "status": "failed",
                "compatible": False,
                "manager_path": str(path),
                "detection_source": source,
                "action_required": "choose_install_directory",
                "download_url": MUMU_DOWNLOAD_URL,
                "message": f"MuMu检测失败：{exc}",
            }

    def list_instances(self) -> list[dict[str, Any]]:
        payload = self._json(self._run("info", "-v", "all", timeout=12).stdout)
        if not isinstance(payload, (dict, list)):
            raise RuntimeError("MuMu实例列表格式无效")
        raw_items = list(payload.values()) if isinstance(payload, dict) else list(payload)
        instances: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            instance = dict(raw)
            raw_index = raw.get("index")
            if raw_index is None or str(raw_index).strip() == "":
                raw_index = raw.get("id")
            if raw_index is None or str(raw_index).strip() == "":
                raw_index = raw.get("vm_index")
            if raw_index is None or str(raw_index).strip() == "":
                raw_index = position
            instance["provider_instance_id"] = str(raw_index).strip()
            running_value = raw.get("is_process_started")
            if running_value is None:
                running_value = raw.get("is_android_started")
            if running_value is None:
                running_value = raw.get("state") or raw.get("status")
            running = (
                running_value is True
                or running_value == 1
                or str(running_value).strip().lower()
                in {"1", "true", "running", "started", "booting", "online"}
            )
            instance["state"] = "running" if running else "stopped"
            instances.append(instance)
        return sorted(
            instances,
            key=lambda item: (
                0,
                int(item["provider_instance_id"]),
            )
            if str(item["provider_instance_id"]).isdigit()
            else (1, str(item["provider_instance_id"])),
        )

    def read_settings(self, instance_id: str) -> dict[str, str]:
        payload = self._json(
            self._run("setting", "-v", str(instance_id), "-aw", timeout=20).stdout
        )
        if not isinstance(payload, dict):
            raise RuntimeError("MuMu配置回读格式无效")
        return {str(key): str(value) for key, value in payload.items()}

    @staticmethod
    def _setting_value(key: str, value: Any) -> str:
        if key in {"root", "auto_rotate", "muted"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key}必须是布尔值")
            return "true" if value else "false"
        if key == "memory_gb":
            numeric = float(value)
            if not 0.5 <= numeric <= 64:
                raise ValueError("内存配置超出允许范围")
            return f"{numeric:.6f}"
        if key in {"cpu", "width", "height", "dpi", "fps"}:
            numeric = int(value)
            ranges = {
                "cpu": (1, 32),
                "width": (320, 7680),
                "height": (320, 7680),
                "dpi": (120, 960),
                "fps": (10, 240),
            }
            minimum, maximum = ranges[key]
            if not minimum <= numeric <= maximum:
                raise ValueError(f"{key}配置超出允许范围")
            return str(numeric)
        if key == "name":
            cleaned = " ".join(str(value).split()).strip()
            if not 1 <= len(cleaned) <= 40:
                raise ValueError("虚拟机名称需为1到40个字符")
            return cleaned
        raise ValueError(f"不支持的虚拟机配置：{key}")

    def apply_settings(self, instance_id: str, settings: dict[str, Any]) -> dict[str, str]:
        if not settings:
            raise ValueError("没有要修改的虚拟机配置")
        unknown = sorted(set(settings) - set(self.SETTING_KEYS))
        if unknown:
            raise ValueError(f"不支持的虚拟机配置：{', '.join(unknown)}")
        normalized = {
            self.SETTING_KEYS[key]: self._setting_value(key, value)
            for key, value in settings.items()
        }
        command = ["setting", "-v", str(instance_id)]
        for key, value in normalized.items():
            command.extend(("-k", key, "-val", value))
        self._run(*command, timeout=60)
        actual = self.read_settings(str(instance_id))
        mismatches = self._settings_mismatches(actual, normalized)
        if mismatches:
            raise RuntimeError(
                "虚拟机配置回读不一致：" + "；".join(mismatches[:8])
            )
        return actual

    @staticmethod
    def _settings_mismatches(
        actual: dict[str, str], expected: dict[str, str]
    ) -> list[str]:
        mismatches: list[str] = []
        numeric_keys = {
            "performance_cpu.custom",
            "performance_mem.custom",
            "resolution_width.custom",
            "resolution_height.custom",
            "resolution_dpi.custom",
            "max_frame_rate",
        }
        for key, wanted in expected.items():
            current = actual.get(key)
            if current is None:
                mismatches.append(f"{key}=缺失")
                continue
            if key in numeric_keys:
                try:
                    matches = float(current) == float(wanted)
                except ValueError:
                    matches = False
            else:
                matches = current.strip().lower() == wanted.strip().lower()
            if not matches:
                mismatches.append(f"{key}={current}（期望{wanted}）")
        return mismatches

    def create_from_recipe(self, name: str) -> dict[str, Any]:
        cleaned_name = " ".join(str(name).split()).strip()
        if not 1 <= len(cleaned_name) <= 40:
            raise ValueError("虚拟机名称需为1到40个字符")
        before = {item["provider_instance_id"] for item in self.list_instances()}
        self._run("create", "--number", "1", "--version", STANDARD_RECIPE["android_version"], timeout=180)
        after = self.list_instances()
        created = [item for item in after if item["provider_instance_id"] not in before]
        if len(created) != 1:
            raise RuntimeError(f"创建结果无法唯一确认，发现{len(created)}个新增候选；不会自动重复创建")
        instance_id = created[0]["provider_instance_id"]
        self._run("rename", "-v", instance_id, "-n", cleaned_name, timeout=30)
        actual_settings = self.apply_settings(
            instance_id,
            {"name": cleaned_name, **{key: STANDARD_RECIPE[key] for key in self.SETTING_KEYS if key != "name"}},
        )
        refreshed = next((item for item in self.list_instances() if item["provider_instance_id"] == instance_id), None)
        if refreshed is None:
            raise RuntimeError("虚拟机已创建但回读失败；不会自动重复创建")
        return {
            "virtual_device_id": uuid.uuid4().hex,
            "provider": "mumu",
            "provider_instance_id": instance_id,
            "name": cleaned_name,
            "state": "stopped",
            "recipe": dict(STANDARD_RECIPE),
            "provider_snapshot": {**refreshed, "settings": actual_settings},
        }

    def start_and_resolve_adb(
        self,
        instance_id: str,
        timeout_seconds: int = 180,
        *,
        already_running: bool = False,
    ) -> str:
        if not already_running:
            self.launch(instance_id)
        return self.resolve_adb_endpoint(instance_id, timeout_seconds=timeout_seconds)

    def launch(self, instance_id: str) -> CommandResult:
        """Start exactly one instance without opening MuMu's management hall."""
        return self._run("control", "-v", str(instance_id), "launch", timeout=30)

    def rename(self, instance_id: str, name: str) -> CommandResult:
        return self._run("rename", "-v", str(instance_id), "-n", name, timeout=30)

    def clone(self, instance_id: str) -> dict[str, Any]:
        before = {item["provider_instance_id"] for item in self.list_instances()}
        self._run("clone", "-v", str(instance_id), "-n", "1", timeout=300)
        after = self.list_instances()
        created = [item for item in after if item["provider_instance_id"] not in before]
        if len(created) != 1:
            raise RuntimeError(
                f"克隆结果无法唯一确认，发现{len(created)}个新增候选；不会自动重复克隆"
            )
        return created[0]

    def export_backup(self, instance_id: str, directory: Path, name: str) -> Path:
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < 2 * 1024**3:
            raise RuntimeError("备份磁盘可用空间不足2GB")
        cleaned_name = re.sub(r"[^0-9A-Za-z._-]+", "-", str(name)).strip("-.") or "mediaflow-backup"
        before = {
            path.resolve(): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in directory.glob("*.mumudata")
        }
        from mumu_archive import accept_dispatch, wait_export
        deadline = time.monotonic() + 1800
        result = self._run(
            "export", "-v", str(instance_id), "-d", str(directory), "-n", cleaned_name,
            "-z", "-ver", "auto", timeout=1800, allow_nonzero=True,
        )
        accept_dispatch(result)
        return wait_export(self.manager_path, directory, before, deadline=deadline)

    def import_backup(self, backup_path: Path) -> dict[str, Any]:
        backup_path = backup_path.resolve(strict=True)
        if backup_path.suffix.lower() != ".mumudata" or backup_path.stat().st_size <= 0:
            raise ValueError("MuMu备份文件无效")
        before = {item["provider_instance_id"] for item in self.list_instances()}
        from mumu_archive import accept_dispatch, wait_import, test_archive
        test_archive(self.manager_path, backup_path)
        deadline = time.monotonic() + 1800
        result = self._run("import", "-p", str(backup_path), "-n", "1", "-ver", "auto", timeout=1800, allow_nonzero=True)
        accept_dispatch(result)
        return wait_import(self, backup_path, before, deadline=deadline)

    def delete(self, instance_id: str) -> None:
        before = {
            item["provider_instance_id"] for item in self.list_instances()
        }
        if str(instance_id) not in before:
            raise RuntimeError("MuMu中找不到要删除的虚拟机")
        self._run("delete", "-v", str(instance_id), "-ver", "auto", timeout=300)
        after = {
            item["provider_instance_id"] for item in self.list_instances()
        }
        if str(instance_id) in after:
            raise RuntimeError("MuMu仍返回该实例，删除结果未确认")

    def resolve_adb_endpoint(self, instance_id: str, timeout_seconds: int = 180) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            current = next(
                (item for item in self.list_instances() if item["provider_instance_id"] == str(instance_id)),
                None,
            )
            if current and current.get("is_android_started"):
                result = self._run(
                    "adb", "-v", str(instance_id), "-c", "connect", timeout=20,
                    allow_nonzero=True,
                )
                matches = re.findall(
                    r"(?:127\.0\.0\.1|localhost):\d+",
                    f"{result.stdout}\n{result.stderr}",
                )
                if matches:
                    endpoint = matches[-1].replace("localhost", "127.0.0.1")
                    if connect_loopback_adb(endpoint):
                        return endpoint
                host = str(current.get("adb_host_ip") or "127.0.0.1")
                port = current.get("adb_port")
                endpoint = f"{host}:{port}" if port else ""
                if endpoint and connect_loopback_adb(endpoint):
                    return endpoint.replace("localhost", "127.0.0.1")
            time.sleep(2)
        raise RuntimeError("虚拟机启动超时，已停止自动继续；不会重复创建实例")

    def stop(self, instance_id: str) -> CommandResult:
        return self._run("control", "-v", str(instance_id), "shutdown", timeout=45)

    def set_window_visible(self, instance_id: str, visible: bool) -> CommandResult:
        """Show only the target instance window; never launch the manager UI."""
        return self._run(
            "control",
            "-v",
            str(instance_id),
            "show_window" if visible else "hide_window",
            timeout=20,
        )


def onboarding_snapshot(custom_path: str | None = None) -> dict[str, Any]:
    provider = MuMuProvider()
    probe = provider.probe(custom_path)
    instances: list[dict[str, Any]] = []
    if probe.get("compatible"):
        try:
            instances = provider.list_instances()
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            probe = {**probe, "status": "failed", "compatible": False, "message": f"MuMu实例读取失败：{exc}"}
    return {"provider": probe, "instances": instances, "recipe": dict(STANDARD_RECIPE)}
