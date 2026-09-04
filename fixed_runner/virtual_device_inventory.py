from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from task_store import TaskStore
from runtime_layout import BUNDLED_ADB, DATA_ROOT
from virtual_devices import STANDARD_RECIPE, MuMuProvider, resolve_mumu_manager


BACKUP_ROOT = DATA_ROOT / "virtual-device-backups"
STANDARD_LOCKED_SETTINGS = {
    "width": STANDARD_RECIPE["width"],
    "height": STANDARD_RECIPE["height"],
    "dpi": STANDARD_RECIPE["dpi"],
    "root": STANDARD_RECIPE["root"],
}
STANDARD_MUTABLE_SETTINGS = {"cpu", "memory_gb", "fps", "muted", "auto_rotate"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def manager_identity(path: Path) -> str:
    normalized = str(path.resolve()).casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:24]


def _instance_name(instance: dict[str, Any]) -> str:
    return str(
        instance.get("name")
        or instance.get("player_name")
        or f"MuMu 虚拟机 {instance.get('provider_instance_id', '')}"
    ).strip()


def _instance_adb_candidates(instance: dict[str, Any]) -> list[str]:
    """Return only explicit loopback transports reported by MuMu."""
    candidates: list[str] = []
    for key in ("adb_endpoint", "adb_serial", "adb_address"):
        value = str(instance.get(key) or "").strip()
        if value.startswith(("127.0.0.1:", "localhost:")):
            candidates.append(value)
    host = str(
        instance.get("adb_host_ip")
        or instance.get("adb_host")
        or "127.0.0.1"
    ).strip()
    port = instance.get("adb_port")
    if port not in (None, ""):
        candidate = f"{host}:{port}"
        if candidate.startswith(("127.0.0.1:", "localhost:")):
            candidates.append(candidate)
    return list(dict.fromkeys(candidates))


def _android_identity(adb_endpoint: str) -> str | None:
    adb = str(BUNDLED_ADB) if BUNDLED_ADB.is_file() else (
        shutil.which("adb.exe") or shutil.which("adb") or "adb.exe"
    )
    try:
        completed = subprocess.run(
            [
                adb,
                "-s",
                adb_endpoint,
                "shell",
                "settings",
                "get",
                "secure",
                "android_id",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    identity = completed.stdout.strip()
    if completed.returncode or not identity or identity.lower() in {"null", "unknown"}:
        return None
    return identity


class VirtualDeviceInventory:
    """Owns MuMu discovery, persistent identity and machine lifecycle reconciliation."""

    def __init__(
        self,
        store: TaskStore,
        *,
        manager_resolver: Callable[[str | None], Path | None] = resolve_mumu_manager,
        provider_factory: Callable[[Path | None], MuMuProvider] = MuMuProvider,
        identity_reader: Callable[[str], str | None] = _android_identity,
        identity_attempts: int = 10,
        identity_interval_seconds: float = 1.0,
    ) -> None:
        self.store = store
        self._manager_resolver = manager_resolver
        self._provider_factory = provider_factory
        self._identity_reader = identity_reader
        self._identity_attempts = max(1, int(identity_attempts))
        self._identity_interval_seconds = max(0.0, float(identity_interval_seconds))

    def _save_unavailable(self, presence_status: str, message: str) -> None:
        for stored in self.store.list_managed_virtual_devices():
            if stored.get("provider") != "mumu" or stored.get("state") == "retired":
                continue
            self.store.save_virtual_device(
                {
                    **stored,
                    "state": presence_status,
                    "presence_status": presence_status,
                    "adb_endpoint": None,
                    "last_error": message,
                }
            )

    def _provider(
        self, custom_path: str | None = None
    ) -> tuple[Path, MuMuProvider]:
        manager = self._manager_resolver(custom_path)
        if manager is None:
            raise RuntimeError("MuMu管理命令不可用")
        provider = self._provider_factory(manager)
        probe = provider.probe(custom_path)
        if not probe.get("compatible"):
            raise RuntimeError(str(probe.get("message") or "MuMu不可用"))
        return manager, provider

    @staticmethod
    def _canonical_name(virtual_device: dict[str, Any]) -> str | None:
        display_index = virtual_device.get("display_index")
        if display_index is None:
            return None
        return f"MediaFlow虚拟机{int(display_index)}"

    def _standard_assessment(
        self,
        virtual_device: dict[str, Any],
        provider: MuMuProvider,
    ) -> tuple[str, str | None, dict[str, str] | None]:
        canonical_name = self._canonical_name(virtual_device)
        if not canonical_name or str(virtual_device.get("name") or "") != canonical_name:
            return "nonstandard", "名称不符合MediaFlow永久编号规则", None
        recipe = virtual_device.get("recipe") or {}
        provider_snapshot = virtual_device.get("provider_snapshot") or {}
        android_version = (
            recipe.get("android_version")
            or provider_snapshot.get("android_version")
        )
        if self._normalized_version(android_version) != self._normalized_version(
            STANDARD_RECIPE["android_version"]
        ):
            return "nonstandard", "Android镜像不是MediaFlow Android 15标准", None
        try:
            actual = provider.read_settings(str(virtual_device["provider_instance_id"]))
            expected = {
                provider.SETTING_KEYS[key]: provider._setting_value(key, value)
                for key, value in STANDARD_LOCKED_SETTINGS.items()
            }
            mismatches = provider._settings_mismatches(actual, expected)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            return "requires_verification", f"暂时无法回读标准配置：{exc}", None
        if mismatches:
            return "nonstandard", "配置不符合MediaFlow标准：" + "；".join(mismatches[:5]), actual
        return "standard", None, actual

    @staticmethod
    def _normalized_version(value: Any) -> tuple[int, ...] | None:
        parts = str(value or "").strip().split(".")
        if not parts or any(not part.isdigit() for part in parts):
            return None
        numbers = [int(part) for part in parts]
        while len(numbers) > 1 and numbers[-1] == 0:
            numbers.pop()
        return tuple(numbers)

    @staticmethod
    def _management_status(virtual_device: dict[str, Any]) -> str:
        if virtual_device.get("presence_status") == "identity_conflict":
            return "identity_conflict"
        if not virtual_device.get("managed"):
            return "unmanaged"
        return (
            "managed_standard"
            if virtual_device.get("standard_status") == "standard"
            else "managed_nonstandard"
        )

    def _decorate(self, virtual_device: dict[str, Any]) -> dict[str, Any]:
        return {
            **virtual_device,
            "management_status": self._management_status(virtual_device),
        }

    def reconcile(
        self,
        custom_path: str | None = None,
        *,
        online_adb_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        manager = self._manager_resolver(custom_path)
        if manager is None:
            self._save_unavailable("engine_unavailable", "MuMu管理命令不可用")
            return {
                "provider": {"provider": "mumu", "status": "missing", "compatible": False},
                "devices": [self._decorate(item) for item in self.store.list_managed_virtual_devices()],
                "instances": [],
                "unmanaged_instances": [],
                "recipe": dict(STANDARD_RECIPE),
            }
        provider = self._provider_factory(manager)
        probe = provider.probe(custom_path)
        if not probe.get("compatible"):
            self._save_unavailable("engine_unavailable", str(probe.get("message") or "MuMu不可用"))
            return {
                "provider": probe,
                "devices": [self._decorate(item) for item in self.store.list_managed_virtual_devices()],
                "instances": [],
                "unmanaged_instances": [],
                "recipe": dict(STANDARD_RECIPE),
            }

        instances = provider.list_instances()
        now = _now_iso()
        install_id = manager_identity(manager)
        stored_by_instance = {
            str(item["provider_instance_id"]): item
            for item in self.store.list_managed_virtual_devices()
            if item.get("provider") == "mumu"
        }
        seen: set[str] = set()
        unmanaged_instances: list[dict[str, Any]] = []
        online = online_adb_ids or set()
        for instance in instances:
            instance_id = str(instance.get("provider_instance_id") or "")
            if not instance_id:
                continue
            seen.add(instance_id)
            stored = stored_by_instance.get(instance_id)
            if stored is None:
                unmanaged_instances.append(
                    {
                        **instance,
                        "managed": False,
                        "management_status": "unmanaged",
                    }
                )
                continue
            if stored and stored.get("state") == "retired":
                self.store.save_virtual_device(
                    {
                        **stored,
                        "presence_status": "identity_conflict",
                        "last_seen_at": now,
                        "last_error": "MuMu实例编号已被重新使用，需要人工确认后才能重新接入",
                    }
                )
                continue
            runtime_state = str(instance.get("state") or "stopped")
            current_endpoint = str((stored or {}).get("adb_endpoint") or "")
            last_endpoint = str((stored or {}).get("last_adb_endpoint") or "")
            endpoint_candidates = list(
                dict.fromkeys(
                    candidate
                    for candidate in (
                        current_endpoint,
                        last_endpoint,
                        *_instance_adb_candidates(instance),
                    )
                    if candidate
                )
            )
            recovered_endpoint = next(
                (candidate for candidate in endpoint_candidates if candidate in online),
                "",
            )
            endpoint_online = bool(recovered_endpoint)
            profile_status = str((stored or {}).get("profile_status") or "requires_verification")
            android_identity = stored.get("android_identity")
            identity_conflict = False
            identity_error: str | None = None
            if endpoint_online and recovered_endpoint != current_endpoint:
                observed_identity = self._identity_reader(recovered_endpoint)
                if not observed_identity:
                    endpoint_online = False
                    recovered_endpoint = ""
                    identity_error = "ADB已在线，但暂时无法核对Android身份；请稍后重试连接"
                elif android_identity and android_identity != observed_identity:
                    endpoint_online = False
                    recovered_endpoint = ""
                    identity_conflict = True
                    profile_status = "requires_verification"
                    identity_error = "设备身份与原记录不一致，需要确认后重新复验"
                else:
                    android_identity = observed_identity
            if identity_conflict:
                state = "degraded"
                adb_endpoint = None
            elif endpoint_online:
                prior_state = str(stored.get("state") or "")
                state = (
                    "ready"
                    if profile_status == "ready"
                    else prior_state
                    if prior_state in {"waiting_app", "waiting_login", "waiting_model", "initializing"}
                    else "adb_ready"
                )
                adb_endpoint = recovered_endpoint
            elif runtime_state == "stopped":
                state = "stopped"
                adb_endpoint = None
            else:
                state = "running"
                adb_endpoint = None
            payload = {
                **(stored or {}),
                "virtual_device_id": stored["virtual_device_id"],
                "provider": "mumu",
                "provider_instance_id": instance_id,
                # The provider's index-0 info response can keep reporting its
                # factory label even after rename and setting readback both
                # succeed.  MediaFlow's allocated name is the stable product
                # identity; keep the provider label only in the snapshot.
                "name": (
                    f"MediaFlow虚拟机{int(stored['display_index'])}"
                    if stored.get("display_index") is not None
                    else str(stored.get("name") or _instance_name(instance))
                ),
                "state": state,
                "recipe": stored.get("recipe") or {},
                "provider_snapshot": instance,
                "adb_endpoint": adb_endpoint,
                "last_adb_endpoint": recovered_endpoint or last_endpoint or current_endpoint or None,
                "android_identity": android_identity,
                "discovery_source": stored.get("discovery_source") or "mediaflow_created",
                "provider_install_id": install_id,
                "presence_status": "identity_conflict" if identity_conflict else "present",
                "profile_status": profile_status,
                "managed": True,
                "display_index": stored.get("display_index"),
                "last_seen_at": now,
                "last_connected_at": now if endpoint_online else stored.get("last_connected_at"),
                "last_error": identity_error,
            }
            standard_status, standard_message, actual_settings = self._standard_assessment(
                payload, provider
            )
            payload["standard_status"] = standard_status
            payload["standard_message"] = standard_message
            if actual_settings is not None:
                payload["provider_snapshot"] = {
                    **instance,
                    "settings": actual_settings,
                }
            if standard_status != "standard" and not identity_error:
                payload["last_error"] = standard_message
            self.store.save_virtual_device(payload)

        for instance_id, stored in stored_by_instance.items():
            if instance_id in seen or stored.get("state") == "retired":
                continue
            self.store.save_virtual_device(
                {
                    **stored,
                    "state": "missing",
                    "presence_status": "missing",
                    "adb_endpoint": None,
                    "last_error": "MuMu中暂未找到该实例；记录和历史证据已保留",
                }
            )
        return {
            "provider": probe,
            "devices": [self._decorate(item) for item in self.store.list_managed_virtual_devices()],
            "instances": instances,
            "unmanaged_instances": unmanaged_instances,
            "recipe": dict(STANDARD_RECIPE),
        }

    def unmanaged_candidates(self, custom_path: str | None = None) -> dict[str, Any]:
        result = self.reconcile(custom_path)
        return {
            "provider": result["provider"],
            "instances": result.get("unmanaged_instances", []),
        }

    def adopt(
        self,
        provider_instance_id: str,
        *,
        name: str,
        display_index: int,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        """Explicitly bring one existing MuMu instance under MediaFlow management."""
        manager = self._manager_resolver(custom_path)
        if manager is None:
            raise RuntimeError("MuMu管理命令不可用")
        provider = self._provider_factory(manager)
        probe = provider.probe(custom_path)
        if not probe.get("compatible"):
            raise RuntimeError(str(probe.get("message") or "MuMu不可用"))
        instance_id = str(provider_instance_id or "").strip()
        managed = next(
            (
                item
                for item in self.store.list_managed_virtual_devices()
                if item.get("provider") == "mumu"
                and str(item.get("provider_instance_id")) == instance_id
            ),
            None,
        )
        if managed is not None:
            return managed
        instance = next(
            (
                item
                for item in provider.list_instances()
                if str(item.get("provider_instance_id") or "") == instance_id
            ),
            None,
        )
        if instance is None:
            raise RuntimeError("MuMu中找不到要接管的实例")
        provider.rename(instance_id, name)
        legacy = next(
            (
                item
                for item in self.store.list_unmanaged_virtual_devices()
                if item.get("provider") == "mumu"
                and str(item.get("provider_instance_id")) == instance_id
            ),
            None,
        )
        is_running = bool(
            instance.get("is_process_started")
            or instance.get("is_android_started")
            or str(instance.get("state") or "") not in {"", "stopped"}
        )
        return self.store.save_virtual_device(
            {
                "virtual_device_id": (legacy or {}).get("virtual_device_id") or uuid.uuid4().hex,
                "provider": "mumu",
                "provider_instance_id": instance_id,
                "name": name,
                "state": "running" if is_running else "stopped",
                "recipe": {},
                "provider_snapshot": {**instance, "name": name},
                "adb_endpoint": None,
                "last_adb_endpoint": (legacy or {}).get("last_adb_endpoint"),
                "android_identity": None,
                "discovery_source": "mediaflow_adopted",
                "provider_install_id": manager_identity(manager),
                "presence_status": "present",
                "profile_status": "requires_verification",
                "standard_status": "requires_verification",
                "standard_message": "接管后必须应用标准配置并完成回读",
                "managed": True,
                "display_index": display_index,
                "last_seen_at": _now_iso(),
                "last_connected_at": None,
                "last_error": "已接管，需要连接并完成初始化复验",
            }
        )

    def reconcile_incomplete_operations(
        self,
        custom_path: str | None = None,
        *,
        online_adb_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Close orphaned lifecycle operations from observed state without replaying commands."""
        active = [
            item
            for item in self.store.list_active_virtual_operations()
            if item["status"] in {"queued", "running"}
        ]
        if not active:
            return []
        observable_actions = {"start", "stop", "restart"}
        for operation in active:
            if str(operation.get("operation_type") or "") in observable_actions:
                continue
            self.store.update_virtual_operation(
                operation["id"],
                status="waiting_user",
                stage="unknown_result_after_restart",
                progress=max(1, int(operation.get("progress") or 0)),
                result=operation.get("result"),
                error="后台曾在操作执行期间中断；结果需要确认，MediaFlow没有自动重放",
                message="请刷新虚拟机库存并确认实际结果",
                retryable=False,
            )
        active = [
            operation
            for operation in active
            if str(operation.get("operation_type") or "") in observable_actions
        ]
        if not active:
            return []
        manager = self._manager_resolver(custom_path)
        if manager is None:
            for operation in active:
                self.store.update_virtual_operation(
                    operation["id"],
                    status="failed",
                    stage="engine_unavailable_after_restart",
                    progress=100,
                    result=operation.get("result"),
                    error="MediaFlow重启后无法连接MuMu；没有重放原操作",
                    message="MuMu引擎当前不可用",
                    retryable=True,
                )
            return active
        provider = self._provider_factory(manager)
        instances = {
            str(item.get("provider_instance_id") or ""): item
            for item in provider.list_instances()
        }
        online = online_adb_ids or set()
        for operation in active:
            request = operation.get("request") or {}
            virtual_device_id = str(request.get("virtual_device_id") or "")
            if not virtual_device_id:
                self.store.update_virtual_operation(
                    operation["id"],
                    status="waiting_user",
                    stage="unknown_result_after_restart",
                    progress=max(1, int(operation.get("progress") or 0)),
                    result=operation.get("result"),
                    error="操作结果需要人工确认；MediaFlow没有自动重放",
                    message="请确认虚拟机当前状态",
                    retryable=False,
                )
                continue
            try:
                virtual_device = self.store.get_virtual_device(virtual_device_id)
            except KeyError:
                self.store.update_virtual_operation(
                    operation["id"],
                    status="failed",
                    stage="device_record_missing",
                    progress=100,
                    error="虚拟机记录不存在；没有重放原操作",
                    message="虚拟机记录不存在",
                    retryable=False,
                )
                continue
            instance = instances.get(str(virtual_device["provider_instance_id"]))
            action = str(operation.get("operation_type") or "")
            is_running = bool(
                instance
                and (
                    instance.get("is_process_started")
                    or instance.get("is_android_started")
                    or str(instance.get("state") or "") not in {"", "stopped"}
                )
            )
            if action == "stop" and not is_running:
                stopped = self.store.save_virtual_device(
                    {**virtual_device, "state": "stopped", "adb_endpoint": None, "last_error": None}
                )
                self.store.update_virtual_operation(
                    operation["id"], status="completed", stage="stopped", progress=100, result=stopped
                )
                continue
            if action in {"start", "restart"} and not is_running:
                stopped = self.store.save_virtual_device(
                    {
                        **virtual_device,
                        "state": "stopped",
                        "adb_endpoint": None,
                        "last_error": "启动在后台重启前未完成，可安全重试",
                    }
                )
                self.store.update_virtual_operation(
                    operation["id"],
                    status="failed",
                    stage="interrupted_before_start",
                    progress=100,
                    result=stopped,
                    error="启动在后台重启前未完成；没有重复发送启动命令，可重新点击启动",
                    message="启动未完成，可以安全重试",
                    retryable=True,
                )
                continue
            endpoint = str(virtual_device.get("adb_endpoint") or "")
            if not endpoint and instance:
                host = str(instance.get("adb_host_ip") or "127.0.0.1")
                port = instance.get("adb_port")
                endpoint = f"{host}:{port}" if port else ""
            if action in {"start", "restart"} and endpoint and endpoint in online:
                connected = self.store.save_virtual_device(
                    {
                        **virtual_device,
                        "state": "adb_ready",
                        "adb_endpoint": endpoint,
                        "last_adb_endpoint": endpoint,
                        "last_error": None,
                    }
                )
                self.store.update_virtual_operation(
                    operation["id"],
                    status="completed",
                    stage="adb_ready_after_restart",
                    progress=100,
                    result=connected,
                )
                continue
            self.store.save_virtual_device(
                {
                    **virtual_device,
                    "state": "running" if is_running else virtual_device.get("state", "failed"),
                    "adb_endpoint": None,
                    "last_error": "MuMu已运行，但ADB未连接；请重试连接",
                }
            )
            self.store.update_virtual_operation(
                operation["id"],
                status="failed",
                stage="adb_unavailable_after_restart",
                progress=100,
                result=virtual_device,
                error="MuMu已运行，但ADB未连接；没有重复启动实例，可重新点击连接",
                message="ADB连接失败，可以重试连接",
                retryable=True,
            )
        return active

    def assert_idle(self, virtual_device: dict[str, Any], *, exclude_operation_id: str | None = None) -> None:
        conflict = self.store.active_virtual_operation(
            virtual_device["virtual_device_id"], exclude_operation_id=exclude_operation_id
        )
        if conflict:
            raise ValueError(f"该虚拟机正在执行{conflict['operation_type']}操作")
        endpoints = {
            str(value)
            for value in (
                virtual_device.get("adb_endpoint"),
                virtual_device.get("last_adb_endpoint"),
            )
            if value
        }
        for endpoint in endpoints:
            if self.store.running_count([endpoint]):
                raise ValueError("该虚拟机仍有任务在执行，请先安全停止")
            if self.store.has_active_control_session(endpoint):
                raise ValueError("该虚拟机正在人工接管，请先结束控制会话")
            initialization = self.store.latest_initialization(endpoint)
            if initialization and initialization.status in {"queued", "running", "waiting_user"}:
                raise ValueError("该虚拟机正在初始化或等待人工处理")

    def start(
        self,
        virtual_device_id: str,
        operation_id: str,
        *,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        manager = self._manager_resolver(custom_path)
        if manager is None:
            raise RuntimeError("MuMu管理命令不可用")
        provider = self._provider_factory(manager)
        probe = provider.probe(custom_path)
        if not probe.get("compatible"):
            raise RuntimeError(str(probe.get("message") or "MuMu不可用"))
        current = next(
            (
                item
                for item in provider.list_instances()
                if str(item.get("provider_instance_id") or "")
                == str(virtual_device["provider_instance_id"])
            ),
            None,
        )
        if current is None:
            raise RuntimeError("MuMu中找不到该虚拟机实例")
        already_running = bool(
            current.get("is_process_started")
            or current.get("is_android_started")
            or str(current.get("state") or "") not in {"", "stopped"}
        )
        self.store.update_virtual_operation(
            operation_id,
            status="running",
            stage="connecting_adb" if already_running else "starting_mumu",
            progress=30 if already_running else 15,
        )
        virtual_device = self.store.save_virtual_device(
            {
                **virtual_device,
                "state": "starting",
                "presence_status": "present",
                "last_error": None,
            }
        )
        self.store.update_virtual_operation(
            operation_id,
            status="running",
            stage="connecting_adb" if already_running else "waiting_android",
            progress=40,
            result=virtual_device,
        )
        endpoint = provider.start_and_resolve_adb(
            virtual_device["provider_instance_id"], already_running=already_running
        )
        self.store.update_virtual_operation(
            operation_id,
            status="running",
            stage="verifying_identity",
            progress=65,
            result=virtual_device,
        )
        identity = None
        for attempt in range(self._identity_attempts):
            identity = self._identity_reader(endpoint)
            if identity:
                break
            if attempt + 1 < self._identity_attempts:
                time.sleep(self._identity_interval_seconds)
        previous_identity = str(virtual_device.get("android_identity") or "")
        if not identity or (previous_identity and identity != previous_identity):
            conflict_message = (
                "暂时无法读取Android身份，需要人工确认；旧档案没有启用"
                if not identity
                else "Android身份与原记录不一致，需要人工确认；旧档案没有启用"
            )
            conflicted = self.store.save_virtual_device(
                {
                    **virtual_device,
                    "state": "degraded",
                    "presence_status": "identity_conflict",
                    "adb_endpoint": None,
                    "last_adb_endpoint": endpoint,
                    "profile_status": "requires_verification",
                    "last_seen_at": _now_iso(),
                    "last_error": conflict_message,
                }
            )
            self.store.update_virtual_operation(
                operation_id,
                status="waiting_user",
                stage="identity_conflict",
                progress=70,
                result=conflicted,
                error=conflicted["last_error"],
            )
            return conflicted
        connected = self.store.save_virtual_device(
            {
                **virtual_device,
                "state": "adb_ready",
                "presence_status": "present",
                "adb_endpoint": endpoint,
                "last_adb_endpoint": endpoint,
                "android_identity": identity or previous_identity or None,
                "profile_status": virtual_device.get("profile_status") or "requires_verification",
                "provider_install_id": manager_identity(manager),
                "last_seen_at": _now_iso(),
                "last_connected_at": _now_iso(),
                "last_error": None,
            }
        )
        self.store.update_virtual_operation(
            operation_id,
            status="running",
            stage="adb_ready",
            progress=70,
            result=connected,
        )
        return connected

    def stop(
        self,
        virtual_device_id: str,
        operation_id: str,
        *,
        custom_path: str | None = None,
        finalize_operation: bool = True,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        manager = self._manager_resolver(custom_path)
        if manager is None:
            raise RuntimeError("MuMu管理命令不可用")
        self.store.update_virtual_operation(
            operation_id, status="running", stage="stopping", progress=30
        )
        self._provider_factory(manager).stop(virtual_device["provider_instance_id"])
        stopped = self.store.save_virtual_device(
            {
                **virtual_device,
                "state": "stopped",
                "presence_status": "present",
                "adb_endpoint": None,
                "last_error": None,
            }
        )
        self.store.update_virtual_operation(
            operation_id,
            status="completed" if finalize_operation else "running",
            stage="completed" if finalize_operation else "restarting",
            progress=100 if finalize_operation else 20,
            result=stopped,
        )
        return stopped

    def apply_settings(
        self,
        virtual_device_id: str,
        operation_id: str,
        settings: dict[str, Any],
        *,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        if virtual_device.get("state") != "stopped":
            raise ValueError("只有已停止的虚拟机可以修改资源配置")
        forbidden = sorted(set(settings) - STANDARD_MUTABLE_SETTINGS)
        if forbidden:
            raise ValueError(
                "分辨率、DPI、竖屏方向、Root、导航、输入方式和名称由MediaFlow锁定；"
                "只能修改CPU、内存、帧率、静音和自动旋转"
            )
        _, provider = self._provider(custom_path)
        self.store.update_virtual_operation(
            operation_id, status="running", stage="applying_settings", progress=35
        )
        actual = provider.apply_settings(
            str(virtual_device["provider_instance_id"]), settings
        )
        recipe = {**(virtual_device.get("recipe") or {}), **settings}
        updated = self.store.save_virtual_device(
            {
                **virtual_device,
                "name": virtual_device["name"],
                "recipe": recipe,
                "provider_snapshot": {
                    **(virtual_device.get("provider_snapshot") or {}),
                    "settings": actual,
                },
                "profile_status": virtual_device.get("profile_status"),
                "last_error": None,
            }
        )
        return updated

    def repair_standard(
        self,
        virtual_device_id: str,
        operation_id: str,
        *,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        if virtual_device.get("state") != "stopped":
            raise ValueError("修复标准配置前必须先停止虚拟机")
        _, provider = self._provider(custom_path)
        canonical_name = self._canonical_name(virtual_device)
        if not canonical_name:
            raise ValueError("虚拟机缺少MediaFlow永久编号")
        self.store.update_virtual_operation(
            operation_id, status="running", stage="applying_settings", progress=35
        )
        provider.rename(str(virtual_device["provider_instance_id"]), canonical_name)
        actual = provider.apply_settings(
            str(virtual_device["provider_instance_id"]), STANDARD_LOCKED_SETTINGS
        )
        return self.store.save_virtual_device(
            {
                **virtual_device,
                "name": canonical_name,
                "recipe": {**STANDARD_RECIPE, **{
                    key: (virtual_device.get("recipe") or {}).get(key, STANDARD_RECIPE[key])
                    for key in STANDARD_MUTABLE_SETTINGS
                }},
                "provider_snapshot": {
                    **(virtual_device.get("provider_snapshot") or {}),
                    "settings": actual,
                },
                "standard_status": "standard",
                "standard_message": None,
                "profile_status": "requires_verification",
                "last_error": "标准配置已修复，需要重新复验",
            }
        )

    def pool_plan(
        self,
        mode: str,
        target_count: int,
        *,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"supplement", "reset"}:
            raise ValueError("标准池模式必须是补齐或重建")
        if not 1 <= int(target_count) <= 50:
            raise ValueError("标准池目标数量必须是1到50台")
        result = self.reconcile(custom_path)
        all_instances = list(result.get("instances") or [])
        standard_devices = [
            item
            for item in result.get("devices") or []
            if item.get("management_status") == "managed_standard"
            and item.get("presence_status") == "present"
        ]
        deletion_items = [
            {
                "provider_instance_id": str(item.get("provider_instance_id") or ""),
                "name": _instance_name(item),
                "state": str(item.get("state") or "unknown"),
                "managed": any(
                    str(device.get("provider_instance_id")) == str(item.get("provider_instance_id"))
                    for device in result.get("devices") or []
                ),
            }
            for item in all_instances
        ] if normalized_mode == "reset" else []
        create_count = (
            int(target_count)
            if normalized_mode == "reset"
            else max(0, int(target_count) - len(standard_devices))
        )
        return {
            "mode": normalized_mode,
            "target_count": int(target_count),
            "standard_count": len(standard_devices),
            "create_count": create_count,
            "deletion_items": deletion_items,
            "confirmation_phrase": (
                f"删除全部并重建{int(target_count)}台"
                if normalized_mode == "reset"
                else None
            ),
            "no_backup": normalized_mode == "reset",
            "provider": result.get("provider"),
        }

    def clone(
        self,
        source_virtual_device_id: str,
        operation_id: str,
        *,
        name: str,
        display_index: int,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        source = self.store.get_virtual_device(source_virtual_device_id)
        self.assert_idle(source, exclude_operation_id=operation_id)
        if source.get("state") != "stopped":
            raise ValueError("克隆前必须先停止来源虚拟机")
        manager, provider = self._provider(custom_path)
        self.store.update_virtual_operation(
            operation_id, status="running", stage="cloning", progress=25
        )
        instance = provider.clone(str(source["provider_instance_id"]))
        instance_id = str(instance["provider_instance_id"])
        provider.rename(instance_id, name)
        created = self.store.save_virtual_device(
            {
                "virtual_device_id": uuid.uuid4().hex,
                "provider": "mumu",
                "provider_instance_id": instance_id,
                "name": name,
                "state": "stopped",
                "recipe": dict(source.get("recipe") or {}),
                "provider_snapshot": {**instance, "name": name},
                "adb_endpoint": None,
                "last_adb_endpoint": None,
                "android_identity": None,
                "discovery_source": "mediaflow_created",
                "provider_install_id": manager_identity(manager),
                "presence_status": "present",
                "profile_status": "requires_verification",
                "managed": True,
                "display_index": display_index,
                "last_seen_at": _now_iso(),
                "last_connected_at": None,
                "last_error": "克隆已完成，需要登录检查和初始化复验",
            }
        )
        return created

    def backup(
        self,
        virtual_device_id: str,
        operation_id: str,
        *,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        if virtual_device.get("state") != "stopped":
            raise ValueError("备份前必须先停止虚拟机")
        _, provider = self._provider(custom_path)
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(BACKUP_ROOT).free < 2 * 1024**3:
            raise RuntimeError("备份磁盘可用空间不足2GB")
        self.store.update_virtual_operation(
            operation_id, status="running", stage="backing_up", progress=20
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = provider.export_backup(
            str(virtual_device["provider_instance_id"]),
            BACKUP_ROOT,
            f"{virtual_device['name']}-{stamp}",
        )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        backup = self.store.save_virtual_device_backup(
            {
                "virtual_device_id": virtual_device_id,
                "source_name": virtual_device["name"],
                "provider": "mumu",
                "provider_instance_id": virtual_device["provider_instance_id"],
                "path": str(path),
                "sha256": digest.hexdigest(),
                "size_bytes": path.stat().st_size,
                "status": "ready",
                "metadata": {
                    "provider_install_id": virtual_device.get("provider_install_id"),
                    "recipe": virtual_device.get("recipe") or {},
                },
            }
        )
        return backup

    def restore(
        self,
        backup_id: str,
        operation_id: str,
        *,
        name: str,
        display_index: int,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        backup = self.store.get_virtual_device_backup(backup_id)
        backup_path = Path(backup["path"]).resolve(strict=True)
        digest = hashlib.sha256()
        with backup_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != backup["sha256"]:
            raise RuntimeError("备份校验失败，已停止恢复")
        manager, provider = self._provider(custom_path)
        self.store.update_virtual_operation(
            operation_id, status="running", stage="restoring", progress=20
        )
        instance = provider.import_backup(backup_path)
        instance_id = str(instance["provider_instance_id"])
        provider.rename(instance_id, name)
        return self.store.save_virtual_device(
            {
                "virtual_device_id": uuid.uuid4().hex,
                "provider": "mumu",
                "provider_instance_id": instance_id,
                "name": name,
                "state": "stopped",
                "recipe": dict(backup.get("metadata", {}).get("recipe") or {}),
                "provider_snapshot": {**instance, "name": name},
                "adb_endpoint": None,
                "last_adb_endpoint": None,
                "android_identity": None,
                "discovery_source": "mediaflow_created",
                "provider_install_id": manager_identity(manager),
                "presence_status": "present",
                "profile_status": "requires_verification",
                "managed": True,
                "display_index": display_index,
                "last_seen_at": _now_iso(),
                "last_connected_at": None,
                "last_error": "备份已恢复为新实例，需要登录检查和初始化复验",
            }
        )

    def delete(
        self,
        virtual_device_id: str,
        operation_id: str,
        *,
        confirmation_name: str,
        custom_path: str | None = None,
    ) -> dict[str, Any]:
        virtual_device = self.store.get_virtual_device(virtual_device_id)
        self.assert_idle(virtual_device, exclude_operation_id=operation_id)
        if confirmation_name != virtual_device["name"]:
            raise ValueError("确认名称与虚拟机名称不一致")
        if virtual_device.get("state") != "stopped":
            raise ValueError("删除前必须先停止虚拟机")
        _, provider = self._provider(custom_path)
        self.store.update_virtual_operation(
            operation_id, status="running", stage="deleting", progress=60
        )
        provider.delete(str(virtual_device["provider_instance_id"]))
        return self.store.save_virtual_device(
            {
                **virtual_device,
                "state": "retired",
                "presence_status": "retired",
                "adb_endpoint": None,
                "managed": True,
                "last_error": None,
            }
        )
