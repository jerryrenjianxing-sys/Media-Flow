"""Shared preparation policy and bounded checks run only inside the Worker lease."""
from __future__ import annotations

RULE_VERSION = "mediaflow-engagement-v3-r2"
PREPARATION_VERSION = "on-demand-v1"


class PreparationWaitingForUser(RuntimeError):
    pass


def engagement_rule():
    return {"rule_version": RULE_VERSION, "source": "bundled",
            "controls": {"aggregate": ["互动消息", "互动通知", "全部互动", "赞评收藏"]}}


def maintenance_may_run(store, device_id):
    return (not store.is_stop_requested(device_id)
            and not (store.get_profile("automation-stop") or {}).get("stopped")
            and not store.has_active_control_session(device_id)
            and store.has_initialization_ready(device_id))


def preparation_presentation(status, message=""):
    if status == "running":
        return {"busy": True, "message": message or "正在准备设备",
                "actions": ["open_screen", "cancel_initialization"]}
    if status == "queued":
        return {"busy": False, "message": "设备准备排队中；可取消或先人工操作，任务队列无需恢复",
                "actions": ["open_screen", "manual_control", "cancel_initialization"]}
    if status == "waiting_user":
        return {"busy": False, "message": message or "等待你操作，完成后继续",
                "actions": ["open_screen", "manual_control", "continue_initialization", "cancel_initialization"]}
    return {"busy": False, "message": "按任务自动准备", "actions": ["open_screen", "manual_control", "add_to_draft"]}


def task_requirements(config, inspection=False):
    if inspection:
        if config.get("inspection_workflow_version") == "home_badge" or config.get("inspection_mode") == "home_badge":
            return ["connection", "display", "application", "home_badge"]
        return ["connection", "display", "application", "engagement_v3"]
    requirements = ["connection", "display", "application", "browse_home"]
    if config.get("content_mode") in {"search", "hybrid", "mixed"}:
        requirements.append("search_input")
    if any(float(config.get(key) or 0) > 0 for key in ("comment_probability", "matched_comment_probability")):
        requirements.append("search_input")
    return list(dict.fromkeys(requirements))


def inspection_suspension_key(device_id, mode="legacy"):
    return "inspection-paused:" + ("home_badge:" if mode == "home_badge" else "") + device_id


def effective_density(output):
    import re
    values = re.findall(r"(?:Physical|Override) density:\s*(\d+)", str(output), re.I)
    return int(values[-1]) if values else None


def require_business_instance(store, device_id):
    instance = store.get_virtual_device_for_adb(device_id)
    if not instance or instance.get("provider") != "mumu" or (instance.get("recipe") or {}).get("is_template"):
        raise ValueError("任务必须使用已连接的MuMu业务实例；本机模板不能运行任务")
    return instance


def record_inspection_outcome(store, task, result):
    if task.task_type != "douyin_engagement_inspection" or result.get("skipped"):
        return
    if result.get("status") not in {"failed", "degraded"}:
        return
    reason = str(result.get("failure_reason") or (result.get("degraded_reason") or {}).get("code") or "inspection_incomplete")
    store.save_profile(inspection_suspension_key(task.device_id, result.get("workflow_version")),
                       {"suspended": True, "reason": reason, "task_id": task.id, "rule_version": RULE_VERSION})
    if (getattr(task, 'payload', None) or {}).get('resilience_version') == 'v1':
        result['inspection_status'] = 'failed'
        if result.get('restored') is not True:
            from task_resilience import TaskWaiting
            raise TaskWaiting('waiting_device', 'inspection_home_restore_failed', result=result)
        result['status'] = 'degraded'
        result['degraded_reason'] = {'code': reason, 'scope': 'message_check_only'}
        return
    if result.get("restored") is not True:
        store.request_stop([task.device_id])
        store.save_profile("preparation-issue:" + task.device_id,
                           {"task_id": task.id, "message": "巡检后未能确认安全主页，该设备业务已暂停；请打开画面检查"})


def prepare_capabilities(device, device_id, requirements, checkpoint):
    """Run inside the existing Worker lease, saving each actually checked capability.

    Navigation is validated by the business executor itself, not by replaying a
    second calibration workflow.  Merely requesting a capability is not proof.
    """
    from device_initialization import probe_device, _enable_fast_input, _preferred_original_ime
    from device_profiles import load_device_profile_payloads, merge_device_probe, upsert_device_profile
    from datetime import datetime

    allowed = {"connection", "display", "application", "browse_home", "search_input", "engagement_v3", "home_badge"}
    if not set(requirements) <= allowed:
        raise ValueError("未知设备准备能力")
    checkpoint("connection", "正在检查当前连接与画面")
    probe = probe_device(device, device_id)
    image = device.screenshot(format="pillow")
    try:
        source = device.dump_hierarchy(compressed=True, pretty=False)
    except Exception:
        if "home_badge" not in requirements:
            raise
        source = ""
    display = probe["display"]
    if (display["width"], display["height"], display["density"]) != (900, 1600, 320):
        raise RuntimeError("显示环境不符：需要900×1600、320 DPI，请停止后修复配置")
    if image.size != (900, 1600) or (not source and "home_badge" not in requirements):
        raise RuntimeError("无法确认当前画面或控制通道，请重试连接")
    observed = datetime.now().astimezone().isoformat(timespec="milliseconds")
    old = load_device_profile_payloads().get(device_id) or {}
    profile = merge_device_probe(device_id, probe, existing=old, observed_at=observed, portable_virtual=True)
    profile["capabilities"] = {**old.get("capabilities", {}), "screenshot": True, "ui_tree": bool(source)}
    upsert_device_profile(device_id, profile)
    checkpoint("display", "连接、控制读取与标准显示已确认")
    if "application" in requirements:
        if not device.app_info("com.ss.android.ugc.aweme"):
            raise PreparationWaitingForUser("抖音尚未安装，请安装后继续；连接与看屏不受影响")
        profile["capabilities"]["application_installed"] = True
        upsert_device_profile(device_id, profile)
        checkpoint("application", "抖音已安装；登录由用户自行处理")
    if "search_input" in requirements:
        checkpoint("search_input", "正在按需准备中文输入组件")
        state = _enable_fast_input(device, device_id, original_ime=_preferred_original_ime(device))
        profile["input_mode"] = state.get("input_mode")
        profile["capabilities"]["chinese_input"] = bool(state.get("chinese_input_verified"))
        upsert_device_profile(device_id, profile)
    return {"checked": ["connection", "display"] + (["application"] if "application" in requirements else []),
            "preparation_version": PREPARATION_VERSION, "device_profile": profile}
