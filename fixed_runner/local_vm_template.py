"""Machine-local, APK-only clean template built through the existing MuMu provider."""
from __future__ import annotations

import shutil
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

from runtime_layout import DATA_ROOT
from template_apks import adb, package_installed, extract_installation, import_installation, install_bundle, file_hash
from virtual_device_inventory import VirtualDeviceInventory, manager_identity
from virtual_devices import MuMuProvider, resolve_mumu_manager

PROFILE = "local-vm-template-v1"
ROOT = DATA_ROOT / "local-vm-templates"
_LOCK = threading.Lock()


class TemplateCancelled(RuntimeError):
    pass


def public_status(store):
    value = store.get_profile(PROFILE) or {}
    status = {key: value.get(key) for key in ("status", "template_version", "app_version", "message", "operation_id", "source", "sha256", "private_data_possible")}
    status["operation"] = next((op for op in store.list_active_virtual_operations()
                                if op["operation_type"] in {"template_prepare", "template_create"}), None)
    return status


def disk_seal(directory):
    directory = Path(directory).resolve(strict=True)
    paths = sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in {".vdi", ".raw", ".vmdk", ".qcow2", ".img"})
    if not paths or any(path.is_symlink() or not path.resolve().is_relative_to(directory) for path in paths):
        raise ValueError("无法确认模板磁盘范围，不会使用未经确认的模板")
    return {str(path.relative_to(directory)): file_hash(path) for path in paths}


class LocalVmTemplate:
    def __init__(self, store, custom_path=None):
        self.store = store
        self.manager = resolve_mumu_manager(custom_path)
        self.provider = MuMuProvider(self.manager)
        self.inventory = VirtualDeviceInventory(store)
        self.custom_path = custom_path

    def _stop_confirmed(self, instance_id):
        self.provider.stop(str(instance_id))
        limit = time.monotonic() + 60
        while time.monotonic() < limit:
            actual = next((item for item in self.provider.list_instances() if str(item["provider_instance_id"]) == str(instance_id)), None)
            if actual and actual.get("state") == "stopped":
                return
            time.sleep(1)
        raise RuntimeError("模板未能确认停止，不会继续启动或克隆")

    def _prepare_input(self, operation_id, template, endpoint):
        # Only the newly-created account-free template may be restarted here.
        if not template.get("recipe", {}).get("is_template"):
            raise ValueError("输入组件注册重启仅用于本次新模板")
        from device_initialization import _install_fast_input_apk, U2_INPUT_IME_COMPONENTS
        _install_fast_input_apk(endpoint)
        def registered(address):
            return bool(set(adb(address, "shell", "ime", "list", "-a", "-s").splitlines()) & U2_INPUT_IME_COMPONENTS)
        if registered(endpoint):
            return endpoint
        self._checkpoint(operation_id, "template_registering_input", 45, "输入组件已安装，正在重启新模板完成首次注册；不重启现有设备")
        self._stop_confirmed(template["provider_instance_id"])
        endpoint = self.provider.start_and_resolve_adb(str(template["provider_instance_id"]))
        if not registered(endpoint):
            raise RuntimeError("新模板输入组件已安装，但重启后仍未注册；已停止准备，不会反复安装")
        return endpoint

    def _checkpoint(self, operation_id, stage, progress, message):
        operation = self.store.get_virtual_operation(operation_id)
        from datetime import datetime
        if operation["status"] not in {"queued", "running"}:
            raise RuntimeError("操作已停止，不会继续模板创建")
        request = operation.get("request") or {}
        if request.get("cancel_requested") or (request.get("cancel_file") and Path(request["cancel_file"]).exists()):
            raise TemplateCancelled("模板操作已安全取消，已创建的实例和文件保留；不会自动重放")
        if datetime.now().astimezone() >= datetime.fromisoformat(operation["deadline_at"]):
            raise RuntimeError("模板操作已超时，不会重复创建；请检查已保留的实例")
        if (self.store.get_profile("automation-stop") or {}).get("stopped"):
            raise RuntimeError("所有自动操作已停止；模板现场已保留")
        self.store.update_virtual_operation(operation_id, status="running", stage=stage,
                                            progress=progress, message=message)
        if request.get("progress_file"):
            import json
            target = Path(request["progress_file"])
            temporary = target.with_suffix('.partial')
            temporary.write_text(json.dumps({"stage": stage, "progress": progress, "message": message}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(target)

    def _build(self, operation_id):
        ROOT.mkdir(parents=True, exist_ok=True)
        if not self.manager or not self.provider.probe(self.custom_path).get("compatible"):
            raise RuntimeError("MuMu管理能力不可用，请检查安装目录")
        install_root = self.manager.parent.parent if self.manager.parent.name.lower() in {"nx_main", "shell"} else self.manager.parent
        vm_root = install_root / "vms"
        if not vm_root.is_dir():
            raise RuntimeError("当前MuMu无法核对模板磁盘位置，暂不能建立可信模板")
        if shutil.disk_usage(vm_root).free < 12 * 1024**3:
            raise RuntimeError("MuMu所在磁盘不足12GB，请释放空间后重试")
        candidates = [item for item in self.store.list_managed_virtual_devices() if item.get("adb_endpoint")]
        candidates.sort(key=lambda item: item.get("name") != "MediaFlow虚拟机1")
        source = None
        for item in candidates:
            try:
                if package_installed(item["adb_endpoint"]):
                    source = item
                    break
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                continue
        imported = (self.store.get_virtual_operation(operation_id).get("request") or {}).get("import_directory")
        if source is None and not imported:
            raise RuntimeError("没有在线且已安装抖音的来源，请导入完整安装包")
        version = uuid.uuid4().hex
        directory = ROOT / version
        directory.mkdir()
        state = {"status": "preparing", "template_version": version, "operation_id": operation_id,
                 "message": "正在提取安装文件；不会复制账号和应用数据"}
        self.store.save_profile(PROFILE, state)
        self._checkpoint(operation_id, "template_apks", 10, state["message"])
        manifest = (import_installation(imported, directory / "apks") if imported
                    else extract_installation(source["adb_endpoint"], directory / "apks"))
        self._checkpoint(operation_id, "template_creating", 20, "正在创建全新、未登录的模板实例")
        before = {path.resolve() for path in vm_root.iterdir() if path.is_dir()}
        before_instance_ids = [str(item["provider_instance_id"]) for item in self.provider.list_instances()]
        template = self.provider.create_from_recipe("MediaFlow本机模板")
        template.update(recipe={**template["recipe"], "is_template": True, "template_version": version},
                        managed=True, discovery_source="local_template", presence_status="present",
                        provider_install_id=manager_identity(self.manager), profile_status="template_only")
        self.store.save_virtual_device(template, fresh_creation={"operation_id": operation_id,
                                      "before_ids": before_instance_ids,
                                      "created_ids": [str(template["provider_instance_id"])]})
        # Retain the exact newly-created identity immediately, even if a later step fails.
        state.update(virtual_device_id=template["virtual_device_id"], manifest=manifest)
        self.store.save_profile(PROFILE, state)
        new_dirs = {path.resolve() for path in vm_root.iterdir() if path.is_dir()} - before
        if len(new_dirs) != 1:
            raise RuntimeError("无法唯一确认新模板磁盘；已保留现场，不会重复创建")
        storage = new_dirs.pop()
        self._checkpoint(operation_id, "template_installing", 35, "正在安装已校验的抖音及控制组件")
        endpoint = self.provider.start_and_resolve_adb(str(template["provider_instance_id"]))
        if package_installed(endpoint):
            raise RuntimeError("新模板已存在抖音数据，无法证明干净；不会继续使用")
        manifest.update(install_bundle(endpoint, directory / "apks", manifest))
        endpoint = self._prepare_input(operation_id, template, endpoint)
        import uiautomator2 as u2
        from device_initialization import _enable_fast_input, _preferred_original_ime
        device = u2.connect(endpoint)
        _enable_fast_input(device, endpoint, original_ime=_preferred_original_ime(device))
        screenshot = device.screenshot(format="pillow")
        if screenshot.size != (900, 1600) or not device.dump_hierarchy(compressed=True, pretty=False):
            raise RuntimeError("模板画面或控制读取未通过，不会用于克隆")
        screenshot.save(directory / "template-control-check.png")
        from task_preparation import effective_density
        if effective_density(adb(endpoint, "shell", "wm", "density")) != 320:
            raise RuntimeError("模板DPI回读不一致")
        self._checkpoint(operation_id, "template_sealing", 50, "正在停止并封存未登录模板")
        self._stop_confirmed(template["provider_instance_id"])
        seal = disk_seal(storage)
        self.store.save_virtual_device({**template, "state": "stopped", "adb_endpoint": None})
        state.update(status="ready", storage=str(storage), disk_seal=seal,
                     app_version=manifest["version_name"], message="本机模板已就绪；不包含登录账号")
        self.store.save_profile(PROFILE, state)
        self.store.save_profile(PROFILE + ":" + version, state)
        return state

    def create(self, operation_id, name, display_index, *, rebuild=False, prepare_only=False, private_manifest=None, resume_instance_id=None):
        if not _LOCK.acquire(blocking=False):
            raise ValueError("已有模板或新增虚拟机操作，请等待当前操作结束")
        try:
            self._checkpoint(operation_id, "template_verifying", 1, "正在检查本机模板状态")
            if private_manifest:
                from private_vm_template import import_private
                state = import_private(self, operation_id, private_manifest, resume_instance_id=resume_instance_id)
                return {key: state.get(key) for key in ("template_version", "virtual_device_id", "source", "sha256", "status")}
            state = self.store.get_profile(PROFILE) or {}
            if state.get("status") not in {None, "ready"} and not rebuild:
                raise ValueError("上次模板准备未完成，请检查现场后点击重建本机模板；不会自动重放")
            if rebuild or state.get("status") != "ready":
                state = self._build(operation_id)
            template = self.store.get_virtual_device(state["virtual_device_id"])
            self._checkpoint(operation_id, "template_verifying", 60, "正在核对模板未被外部修改")
            actual = next((item for item in self.provider.list_instances() if str(item["provider_instance_id"]) == str(template["provider_instance_id"])), None)
            if not actual or actual.get("state") != "stopped" or disk_seal(state["storage"]) != state["disk_seal"]:
                raise ValueError("模板已被打开、修改或移除，请重建本机模板；不会复制未知账号数据")
            if prepare_only:
                return {"template_version": state["template_version"], "name": "本机标准模板"}
            clone = self.inventory.clone(template["virtual_device_id"], operation_id,
                                         name=name, display_index=display_index, custom_path=self.custom_path)
            if disk_seal(state["storage"]) != state["disk_seal"]:
                raise ValueError("复制期间模板磁盘发生变化；新实例已保留但不会启动，请检查并重建模板")
            clone["recipe"] = {**clone["recipe"], "is_template": False}
            clone["last_error"] = None
            self.store.save_virtual_device(clone)
            self._checkpoint(operation_id, "starting_mumu", 75, "克隆完成，正在连接新实例")
            clone = self.inventory.start(clone["virtual_device_id"], operation_id, custom_path=self.custom_path)
            if not clone.get("adb_endpoint"):
                raise RuntimeError("实例已创建但连接尚未完成，请在设备卡片重试连接；不会再次克隆")
            return {**clone, "template_version": state["template_version"]}
        except Exception as exc:
            logging.getLogger(__name__).exception("Local template operation failed: %s", operation_id)
            state = self.store.get_profile(PROFILE) or {}
            if state.get("status") == "preparing":
                self.store.save_profile(PROFILE, {**state, "status": "failed", "message": str(exc)})
            raise
        finally:
            _LOCK.release()
