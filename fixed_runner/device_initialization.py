from __future__ import annotations

import json
import hashlib
import re
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import uiautomator2
from adb_runtime import resolve_adb_executable

from control_vision import VisionCandidateLocator
from device_profiles import (
    load_device_profile_payloads,
    merge_device_probe,
    upsert_device_profile,
)
from douyin_adapter import DouyinAdapter
from douyin_uia2_runner import Uia2RunRecorder
from engagement_calibration import calibrate_engagement_v3
from platform_adapters import PlatformAdapter
from platform_profiles import (
    ADAPTER_VERSION,
    display_signature,
    profile_key,
    save_platform_profile,
)
from runtime_layout import INITIALIZATION_ARTIFACTS_ROOT
from task_store import InitializationRecord, TaskStore
from task_preparation import PreparationWaitingForUser
from worker_runtime import device_preflight
from brand import PRODUCT_NAME


INITIALIZATION_COMMENT = f"{PRODUCT_NAME} 初始化连通性测试，请忽略"
STAGE_TOTAL = 8
U2_INPUT_IME_COMPONENTS = {
    "com.github.uiautomator/.AdbKeyboard",
    # Kept for profiles and older uiautomator2 builds already deployed on devices.
    "com.github.uiautomator/.FastInputIME",
}


class InitializationWaitingForUser(RuntimeError):
    pass


class InitializationCancelled(RuntimeError):
    pass


def _shell_output(device, command: list[str] | str) -> str:
    response = device.shell(command)
    output = getattr(response, "output", response)
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return str(output or "").strip()


def _integer(text: str) -> int | None:
    match = re.search(r"\d+", str(text))
    return int(match.group(0)) if match else None


def probe_device(device, device_id: str) -> dict[str, Any]:
    width, height = (int(value) for value in device.window_size())
    from task_preparation import effective_density
    density_text = _shell_output(device, ["wm", "density"])
    density = effective_density(density_text) or _integer(density_text)
    navigation_raw = _shell_output(
        device, ["settings", "get", "secure", "navigation_mode"]
    )
    navigation_mode = {
        "0": "three_button",
        "1": "two_button",
        "2": "gesture",
    }.get(navigation_raw, navigation_raw or "unknown")
    try:
        info = dict(device.info or {})
    except Exception:
        info = {}
    rotation = int(info.get("displayRotation", 0) or 0)
    return {
        "device_id": device_id,
        "manufacturer": _shell_output(device, ["getprop", "ro.product.manufacturer"]),
        "model": _shell_output(device, ["getprop", "ro.product.model"]),
        "android_version": _shell_output(device, ["getprop", "ro.build.version.release"]),
        "sdk": _shell_output(device, ["getprop", "ro.build.version.sdk"]),
        "build_fingerprint": _shell_output(device, ["getprop", "ro.build.fingerprint"]),
        "system_image_fingerprint": _shell_output(
            device, ["getprop", "ro.system.build.fingerprint"]
        ),
        "display": {
            "width": width,
            "height": height,
            "density": density,
            "orientation": rotation,
            "navigation_mode": navigation_mode,
        },
    }


def emulator_standard_signature(
    probe: dict[str, Any],
    *,
    app_version: str,
    input_mode: str,
    adapter_version: str,
) -> dict[str, Any]:
    """Build an auditable emulator environment signature, not a device action."""
    display = dict(probe.get("display") or {})
    details = {
        "android_image": str(
            probe.get("system_image_fingerprint")
            or probe.get("build_fingerprint")
            or "unknown"
        ),
        "android_version": str(probe.get("android_version") or "unknown"),
        "sdk": str(probe.get("sdk") or "unknown"),
        "resolution": [display.get("width"), display.get("height")],
        "density": display.get("density"),
        "navigation_mode": display.get("navigation_mode"),
        "douyin_version": str(app_version or "unknown"),
        "input_mode": str(input_mode or "unknown"),
        "adapter_version": str(adapter_version or "unknown"),
    }
    digest = hashlib.sha256(
        json.dumps(details, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return {"version": 1, "fingerprint": digest, **details}


def _current_ime(device) -> str:
    try:
        return str(device.current_ime() or "").strip()
    except Exception:
        return _shell_output(device, ["settings", "get", "secure", "default_input_method"])


def _is_u2_input_ime(component: str) -> bool:
    return str(component or "").strip() in U2_INPUT_IME_COMPONENTS


def _device_is_rooted(device) -> bool:
    return "uid=0(root)" in _shell_output(device, ["id"])


def _u2_input_package_installed(device) -> bool:
    return "package:" in _shell_output(
        device, ["pm", "path", "com.github.uiautomator"]
    )


def _preferred_original_ime(device) -> str:
    current = _current_ime(device)
    if not _is_u2_input_ime(current):
        return current
    installed = _shell_output(device, ["ime", "list", "-s"])
    return next(
        (
            line.strip()
            for line in installed.splitlines()
            if line.strip()
            and not _is_u2_input_ime(line)
            and re.fullmatch(
                r"[A-Za-z0-9._]+/[A-Za-z0-9._$]+", line.strip()
            )
        ),
        "",
    )


def _adb_executable() -> str:
    discovered = resolve_adb_executable()
    if discovered:
        return discovered
    raise RuntimeError("未找到 ADB；请修复 MediaFlow 安装或将 adb.exe 加入 PATH")


def _install_fast_input_apk(device_id: str) -> dict[str, Any]:
    asset = Path(uiautomator2.__file__).resolve().parent / "assets" / "app-uiautomator.apk"
    if not asset.is_file() or asset.stat().st_size <= 0:
        raise RuntimeError("MediaFlow 缺少 FastInputIME 安装资源")
    command = [_adb_executable(), "-s", device_id, "install", "-r", "-t", str(asset)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise InitializationWaitingForUser(
            "FastInputIME 安装等待超时；请在对应手机上允许安装后点击继续"
        ) from exc
    detail = "\n".join(
        value.strip() for value in (completed.stdout, completed.stderr) if value.strip()
    )
    if completed.returncode != 0 or "success" not in detail.lower():
        concise = detail.replace("\r", " ").replace("\n", " ")[:240]
        raise InitializationWaitingForUser(
            "FastInputIME 未安装；请处理手机上的安装或安全确认后点击继续"
            + (f"（{concise}）" if concise else "")
        )
    return {"asset": str(asset), "adb_result": detail[:240]}


def _enable_fast_input(
    device,
    device_id: str,
    *,
    original_ime: str,
) -> dict[str, Any]:
    installed_before = False
    install_result = None
    package_installed = _u2_input_package_installed(device)
    try:
        # Some ColorOS builds deny `ime list -s` to the shell user even though
        # the uiautomator package and its IME are already installed.  Treat the
        # package-manager result as the installation source of truth so a
        # reboot cannot turn a read restriction into a needless reinstall.
        installed_before = bool(device.is_input_ime_installed()) or package_installed
        if not installed_before:
            install_result = _install_fast_input_apk(device_id)
        device.set_input_ime(True)
        active = _current_ime(device)
    except Exception as exc:
        if isinstance(exc, InitializationWaitingForUser):
            raise
        if _device_is_rooted(device) and _u2_input_package_installed(device):
            return {
                "original_ime": original_ime,
                "fast_input_installed_before": installed_before,
                "fast_input_install": install_result,
                "fast_input_component": "",
                "input_mode": "uiautomator_selector",
                "chinese_input_verified": False,
                "fallback_reason": type(exc).__name__,
            }
        if package_installed:
            raise InitializationWaitingForUser(
                "MediaFlow 输入组件已安装，但手机系统禁止 ADB 切换输入法；"
                "请重新开启 USB 调试（安全设置）或关闭权限监控后点击继续"
            ) from exc
        raise InitializationWaitingForUser(
            f"输入组件准备未完成（{type(exc).__name__}）；请查看诊断详情后重试"
        ) from exc
    if not _is_u2_input_ime(active):
        if _device_is_rooted(device) and _u2_input_package_installed(device):
            return {
                "original_ime": original_ime,
                "fast_input_installed_before": installed_before,
                "fast_input_install": install_result,
                "fast_input_component": active,
                "input_mode": "uiautomator_selector",
                "chinese_input_verified": False,
                "fallback_reason": "input_method_switch_rejected",
            }
        raise InitializationWaitingForUser(
            "MediaFlow 输入组件未成功启用；请完成手机上的安装确认后点击继续"
        )
    return {
        "original_ime": original_ime,
        "fast_input_installed_before": installed_before,
        "fast_input_install": install_result,
        "fast_input_component": active,
        "input_mode": "uiautomator_ime",
        "chinese_input_verified": True,
    }


def _restore_ime(device, original_ime: str) -> bool:
    if not original_ime:
        return False
    try:
        _shell_output(device, ["ime", "set", original_ime])
        return _current_ime(device) == original_ime
    except Exception:
        return False


def _report(
    record: InitializationRecord,
    recorder: Uia2RunRecorder,
    result: dict[str, Any],
) -> Path:
    json_path = recorder.run_dir / "initialization-report.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path = recorder.run_dir / "initialization-report.md"
    capabilities = result.get("platform_profile", {}).get("capabilities", {})
    rows = "\n".join(
        f"- {name}: {'通过' if passed else '未通过'}"
        for name, passed in capabilities.items()
    )
    markdown_path.write_text(
        f"# {PRODUCT_NAME} 设备初始化报告\n\n"
        f"- 初始化 ID：`{record.id}`\n"
        f"- 设备：`{record.device_id}`\n"
        f"- 状态：`{result.get('status', 'unknown')}`\n"
        f"- 写入验收：`{bool(record.options.get('write_acceptance', False))}`\n\n"
        "## 能力\n\n"
        + (rows or "- 暂无")
        + "\n",
        encoding="utf-8",
    )
    return markdown_path


def execute_initialization(
    device,
    record: InitializationRecord,
    store: TaskStore,
    *,
    artifacts_root: Path = INITIALIZATION_ARTIFACTS_ROOT,
    adapter_factory: Callable[..., PlatformAdapter] = DouyinAdapter,
    engagement_calibrator: Callable[..., dict[str, Any]] = calibrate_engagement_v3,
    vision_locator: VisionCandidateLocator | None = None,
) -> dict[str, Any]:
    """Run from the device's existing worker, which already owns DeviceLock."""
    recorder = Uia2RunRecorder(artifacts_root, record.device_id)
    result: dict[str, Any] = {
        "status": "running",
        "initialization_id": record.id,
        "device_id": record.device_id,
        "run_dir": str(recorder.run_dir),
        "stages": [],
    }
    original_ime = ""
    deadline = time.monotonic() + 120

    def checkpoint(stage: str, current: int, message: str, **details: Any) -> None:
        if time.monotonic() > deadline:
            raise RuntimeError("设备准备超过120秒，已停止；可取消后接管或重试")
        if store.initialization_cancel_requested(record.id):
            raise InitializationCancelled("初始化已取消")
        item = {"stage": stage, "message": message, **details}
        result["stages"].append(item)
        recorder.emit("initialization_stage", **item)
        store.update_initialization(
            record.id,
            stage=stage,
            progress_current=current,
            progress_total=STAGE_TOTAL,
            message=message,
            result=result,
        )

    try:
        from task_preparation import PREPARATION_VERSION, prepare_capabilities
        if record.options.get("preparation_version") == PREPARATION_VERSION:
            prepared = prepare_capabilities(
                device, record.device_id,
                record.options.get("requirements") or ["connection", "display", "application"],
                lambda stage, message: checkpoint(stage, len(result["stages"]) + 1, message),
            )
            if record.options.get("inspection_recheck"):
                from engagement_inspection import EngagementInspector
                from task_preparation import engagement_rule, inspection_suspension_key
                checkpoint("engagement_v3", 4, "正在只读检查互动消息；不会发送互动")
                inspected = EngagementInspector(
                    device, recorder, store=store, device_id=record.device_id, task_id=record.id,
                    incident_sink=lambda incident: store.record_incident(task_id=record.id, device_id=record.device_id, **incident),
                ).inspect({"inspection_workflow_version": "v3", "preparation_version": PREPARATION_VERSION,
                           "inspection_calibration": engagement_rule(), "expected_display_signature": "900x1600x320x0xunknown",
                           "max_items_per_section": 100})
                result["inspection"] = inspected
                if inspected.get("restored") is not True:
                    store.request_stop([record.device_id])
                if inspected.get("status") != "completed" or not (inspected.get("unified_activity") or {}).get("complete"):
                    raise InitializationWaitingForUser("巡检仍未完整完成，现场已保存；可人工查看后再检查")
                store.save_profile(inspection_suspension_key(record.device_id), {"suspended": False, "last_check_id": record.id})
            result.update(prepared, status="ready")
            report_path = _report(record, recorder, result)
            store.finish_initialization(record.id, status="ready", stage="ready",
                                        message="设备基础准备完成；选择任务后按需检查，无需手工校准",
                                        result=result, report_path=str(report_path))
            return result
        checkpoint("preflight", 1, "设备在线、已授权且由独占 Worker 接管")
        if not device_preflight(device):
            raise RuntimeError("设备预检失败，请保持手机在线并解锁")
        device.screenshot(format="pillow")
        device.dump_hierarchy(compressed=True, pretty=False)

        probe = probe_device(device, record.device_id)
        checkpoint("device_probe", 2, "已读取设备与显示信息", probe=probe)

        original_ime = _preferred_original_ime(device)
        try:
            input_state = _enable_fast_input(
                device,
                record.device_id,
                original_ime=original_ime,
            )
        except InitializationWaitingForUser as exc:
            input_state = {
                "input_mode": "unavailable",
                "chinese_input_verified": False,
                "message": str(exc),
            }
        input_message = (
            "uiautomator2、截图和UI树已就绪；中文输入暂不可用，不影响首页浏览"
            if input_state.get("input_mode") == "unavailable"
            else "uiautomator2、截图和UI树已就绪，中文输入将在搜索框复验"
            if input_state.get("input_mode") == "uiautomator_selector"
            else "uiautomator2、截图、UI树和中文输入已就绪"
        )
        checkpoint("control_setup", 3, input_message, input=input_state)

        adapter = adapter_factory(
            device,
            recorder,
            device_id=record.device_id,
            vision_locator=vision_locator,
        )
        try:
            adapter.ensure_ready()
        except RuntimeError as exc:
            if any(marker in str(exc) for marker in ("登录", "验证", "安全确认")):
                raise InitializationWaitingForUser(str(exc)) from exc
            raise
        app_version = adapter.app_version()
        checkpoint("app_check", 4, "抖音已安装、可启动且账号页面可用", app_version=app_version)

        query = str(record.options.get("search_query") or "人工智能").strip()[:80]
        calibration = (
            adapter.calibrate_browsing()
            if input_state.get("input_mode") == "unavailable"
            and hasattr(adapter, "calibrate_browsing")
            else adapter.calibrate_navigation(query)
        )
        if input_state.get("input_mode") == "uiautomator_selector":
            # enter_topic_search only returns after set_text wrote the exact
            # Unicode query and get_text read the same value back.
            input_state["chinese_input_verified"] = True
        calibration_payload = asdict(calibration)
        checkpoint(
            "navigation_calibration",
            5,
            "安全导航校准已完成",
            calibration=calibration_payload,
        )
        missing = [
            name for name, passed in calibration.capabilities.items() if not passed
        ]
        if not calibration.capabilities.get("main_feed"):
            raise InitializationWaitingForUser(
                "首页浏览尚未通过固定程序复验：" + "、".join(missing)
            )
        if missing:
            checkpoint(
                "capability_summary",
                5,
                "首页浏览已可用；部分附加能力需要按任务继续复验",
                unavailable_capabilities=missing,
            )

        write_result: dict[str, Any] | None = None
        if bool(record.options.get("write_acceptance", False)):
            write_result = adapter.run_write_acceptance(INITIALIZATION_COMMENT)
            checkpoint(
                "write_acceptance",
                6,
                "完整写入验收已完成；评论已真实发送且不会自动删除",
                write_acceptance=write_result,
            )
        else:
            checkpoint("write_acceptance", 6, "已按默认设置跳过写入验收")

        observed_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        device_profile = merge_device_probe(
            record.device_id,
            probe,
            existing=load_device_profile_payloads().get(record.device_id),
            observed_at=observed_at,
            portable_virtual=bool(record.options.get("auto_onboarding")),
        )
        device_profile.update(
            verified=True,
            verified_at=observed_at,
            initialization_status="ready",
            capabilities={
                "screenshot": True,
                "ui_tree": True,
                "click": True,
                "swipe": True,
                "chinese_input": bool(input_state.get("chinese_input_verified")),
                **dict(calibration.capabilities),
            },
        )
        if bool(record.options.get("auto_onboarding")):
            engagement_signature = display_signature(probe["display"])
            try:
                engagement_calibration = engagement_calibrator(
                    device=device,
                    store=store,
                    device_id=record.device_id,
                    app_version=app_version,
                    display_signature=engagement_signature,
                    artifacts_root=artifacts_root,
                    origin_id=record.id,
                )
            except RuntimeError as exc:
                device_profile["capabilities"]["engagement_v3"] = False
                device_profile.update(
                    engagement_inspection_version="v3",
                    engagement_status="requires_verification",
                    engagement_error=str(exc),
                )
                checkpoint(
                    "engagement_calibration",
                    7,
                    "首页浏览已保留；互动巡检v3需要单独复验",
                    engagement_error=str(exc),
                )
            else:
                device_profile["capabilities"]["engagement_v3"] = True
                device_profile.update(
                    engagement_app_version=app_version,
                    engagement_display_signature=engagement_signature,
                    engagement_inspection_version="v3",
                    engagement_calibration=engagement_calibration,
                )
                checkpoint(
                    "engagement_calibration",
                    7,
                    "互动消息聚合页已完成三次只读语义复验",
                    engagement_calibration=engagement_calibration,
                )
        else:
            checkpoint(
                "engagement_calibration", 7, "真机暂不启用标准虚拟机互动巡检v3档案"
            )
        upsert_device_profile(record.device_id, device_profile)

        key = profile_key(
            record.device_id,
            adapter.package_name,
            app_version,
            probe["display"],
            adapter.adapter_version,
        )
        platform_profile = {
            "key": key,
            "device_id": record.device_id,
            "platform_id": adapter.platform_id,
            "package_name": adapter.package_name,
            "app_version": app_version,
            "display_signature": display_signature(probe["display"]),
            "adapter_version": adapter.adapter_version,
            "status": "ready",
            "controls": calibration.controls,
            "capabilities": calibration.capabilities,
            "verified_at": observed_at,
            "initialization_id": record.id,
        }
        if bool(record.options.get("auto_onboarding")):
            signature = emulator_standard_signature(
                probe,
                app_version=app_version,
                input_mode=str(input_state.get("input_mode") or "unknown"),
                adapter_version=adapter.adapter_version,
            )
            platform_profile["emulator_standard_signature"] = signature
            result["emulator_standard_signature"] = signature
        save_platform_profile(platform_profile)
        result.update(
            status="ready",
            device_profile=device_profile,
            platform_profile=platform_profile,
            write_acceptance=write_result,
        )
        report_path = _report(record, recorder, result)
        checkpoint("ready", 8, "设备初始化完成并已写入设备与平台档案")
        store.finish_initialization(
            record.id,
            status="ready",
            stage="ready",
            message="已就绪",
            result=result,
            report_path=str(report_path),
        )
        return result
    except (InitializationWaitingForUser, PreparationWaitingForUser) as exc:
        result.update(status="waiting_user", error=str(exc))
        report_path = _report(record, recorder, result)
        store.finish_initialization(
            record.id,
            status="waiting_user",
            stage="waiting_user",
            message=str(exc),
            result=result,
            error=f"{type(exc).__name__}: {exc}",
            report_path=str(report_path),
        )
        return result
    except InitializationCancelled as exc:
        result.update(status="cancelled", error=str(exc))
        report_path = _report(record, recorder, result)
        store.finish_initialization(
            record.id,
            status="cancelled",
            stage="cancelled",
            message="初始化已取消",
            result=result,
            error=str(exc),
            report_path=str(report_path),
        )
        return result
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        try:
            recorder.screenshot(device, "initialization-failure")
        except Exception:
            pass
        report_path = _report(record, recorder, result)
        store.finish_initialization(
            record.id,
            status="failed",
            stage="failed",
            message="初始化失败，已保留截图和日志",
            result=result,
            error=f"{type(exc).__name__}: {exc}",
            report_path=str(report_path),
        )
        return result
    finally:
        restored = _restore_ime(device, original_ime)
        recorder.emit(
            "initialization_input_method_restore",
            original_ime=original_ime,
            restored=restored,
        )
