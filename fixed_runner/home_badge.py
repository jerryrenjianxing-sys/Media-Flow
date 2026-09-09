"""Homepage-only observation. No message navigation or unread-count inference."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
from badge_glyphs import RULE_VERSION, glyph_ink_mask, read_glyphs
from task_store import now_iso


def is_badge_home(inspector, source: str) -> bool:
    """Homepage proof local to this mode, not a change to legacy navigation."""
    from engagement_inspection import LOGIN_MARKERS, IDENTITY_BLOCK_MARKERS, CONVERSATION_SURFACE_MARKERS
    if any(marker in source for marker in (*LOGIN_MARKERS, *IDENTITY_BLOCK_MARKERS, *CONVERSATION_SURFACE_MARKERS)):
        return False
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return False
    recommendation = False
    home_tab = False
    for node in root.iter("node"):
        if node.get("visible-to-user") == "false":
            continue
        text = (node.get("text") or node.get("content-desc") or "").strip()
        bounds = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
        if len(bounds) != 4:
            continue
        if text in {"推荐", "精选"} and bounds[3] <= inspector._height*.25:
            recommendation = True
        if bounds[1] >= inspector._height*.8:
            label = re.match(r"^(首页|消息|朋友|我|商城)(?=$|[\s\d，,:：]|未读|按钮|已选中)", text)
            if label:
                home_tab = home_tab or label[1] == "首页"
                if label[1] != "首页" and (node.get("selected") == "true" or node.get("checked") == "true"):
                    return False
    return recommendation and home_tab


def analyze_badge(image: Image.Image, source: str) -> dict:
    """Require an unambiguous visible message tab; inspect only its badge region."""
    answer = dict(state="unknown", reason_code="message_tab_not_confirmed",
                  message="检查失败：未能确认消息角标", source="local", target_bounds=None,
                  evidence_missing=[], badge_text=None, message_count=None, count_is_lower_bound=False,
                  quantity_status="unreadable", quantity_source=None, badge_bounds=None, rule_version=RULE_VERSION)
    width, height = image.size
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return answer
    candidates = set()
    semantics = []
    for node in root.iter("node"):
        if node.get("visible-to-user") == "false":
            continue
        text = " ".join((node.get("text", ""), node.get("content-desc", ""))).strip()
        bounds = tuple(map(int, re.findall(r"\d+", node.get("bounds", ""))))
        if len(bounds) != 4:
            continue
        x1, y1, x2, y2 = bounds
        if "Dialog" in node.get("class", "") and y2 > height * .8:
            answer["reason_code"] = "message_tab_obscured"
            return answer
        if (re.match(r"^消息(?:\s|\d|[,，:：]|未读|$)", text)
                and 0 <= x1 < x2 <= width and height * .8 <= y1 < y2 <= height
                and x2-x1 < width * .4):
            candidates.add(bounds)
            semantics.append(text)
    if len(candidates) != 1:
        return answer
    x1, y1, x2, y2 = next(iter(candidates))
    tab_width, tab_height = x2-x1, y2-y1
    center = (x1+x2)/2
    region = [max(0, int(center-width*.02)), max(int(height*.8), int(y1-height*.03)),
              min(width, int(center+width*.14)), min(height, int(y1+height*.045))]
    answer["target_bounds"] = region
    crop = image.convert("RGB").crop(region)
    # Connected red/pink components reject scattered compression noise and large
    # adjacent video content. Absolute thresholds scale with screenshot width.
    pixels = crop.load()
    red = {(x, y) for y in range(crop.height) for x in range(crop.width)
           if (lambda c: c[0] > 150 and c[0]-c[1] > 65 and c[0]-c[2] > 30)(pixels[x,y])}
    total_red = len(red)
    scale = width / 900
    badges = []
    while red:
        stack = [red.pop()]
        component = []
        while stack:
            x, y = stack.pop()
            component.append((x,y))
            for neighbor in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if neighbor in red:
                    red.remove(neighbor)
                    stack.append(neighbor)
        xs, ys = zip(*component)
        cw, ch = max(xs)-min(xs)+1, max(ys)-min(ys)+1
        if (4*scale <= ch <= 65*scale and 4*scale <= cw <= 130*scale
                and .25 <= cw/ch <= 5 and len(component) >= 10*scale*scale
                and len(component)/(cw*ch) >= .35
                and min(xs)>0 and min(ys)>0 and max(xs)<crop.width-1 and max(ys)<crop.height-1):
            badges.append([region[0]+min(xs),region[1]+min(ys),region[0]+max(xs)+1,region[1]+max(ys)+1])
    # Red islands in closed glyph counters (0/6/8/9) belong to the enclosing
    # badge; they are not additional notification candidates.
    badges=[box for box in badges if not any(other!=box and other[0]<=box[0] and other[1]<=box[1]
        and other[2]>=box[2] and other[3]>=box[3] for other in badges)]
    if len(badges)>1:
        answer.update(reason_code="badge_evidence_conflict",quantity_status="conflict")
    elif badges:
        answer["badge_bounds"]=badges[0]
        answer.update(state="present", reason_code="visible_message_badge", message="有消息")
        counts = set(re.findall(r"(?<!\d)([1-9]\d{0,5}\+?)(?!\d)", " ".join(semantics)))
        badge_crop=image.crop(badges[0])
        local_text=read_glyphs(badge_crop)
        # A pure dot needs positive solid-red interior evidence. Ink below the
        # reader's brightness threshold must still be unreadable, never a dot.
        interior=badge_crop.convert("RGB").crop((int(badge_crop.width*.2),int(badge_crop.height*.2),
                                                int(badge_crop.width*.8),int(badge_crop.height*.8)))
        channels=iter(interior.tobytes())
        solid_red=all(r>150 and r-g>65 and r-b>30 for r,g,b in zip(channels,channels,channels))
        answer["numeric_appearance"] = bool(glyph_ink_mask(badge_crop).getbbox()) or not solid_red or badge_crop.width/badge_crop.height>1.4
        if local_text or answer["numeric_appearance"]:
            if len(counts)>1 or (counts and local_text and local_text not in counts):
                answer.update(reason_code="badge_evidence_conflict",quantity_status="conflict",message="有消息，角标数量证据冲突")
            elif local_text or len(counts)==1:
                apply_badge_text(answer,local_text or next(iter(counts)))
                answer.update(quantity_status="recognized",quantity_source="local_glyph" if local_text else "ui_tree")
            else:
                answer.update(reason_code="badge_quantity_unreadable",quantity_status="unreadable",message="有消息，数字未能确认")
        else:
            answer["quantity_status"]="dot"
    elif total_red > 10*scale*scale:
        answer["reason_code"] = "badge_region_ambiguous"
    elif any(re.search(r"未读|[1-9]\d*", text) for text in semantics):
        answer["reason_code"] = "badge_evidence_conflict"
        answer["quantity_status"] = "conflict"
    else:
        # A uniform/blank button area is not proof of absence.
        low, high = image.crop((x1,y1,x2,y2)).convert("L").getextrema()
        if high-low >= 35:
            answer.update(state="absent", reason_code="visible_tab_without_badge", message="无消息",quantity_status="none")
        else:
            answer["reason_code"] = "message_tab_not_visible"
    return answer


def apply_badge_text(answer, text):
    """Preserve displayed lower bounds (99+) instead of inventing exact counts."""
    if isinstance(text, str) and re.fullmatch(r"[1-9]\d{0,5}\+?", text):
        answer.update(badge_text=text, message_count=None if text.endswith("+") else int(text),
                      count_is_lower_bound=text.endswith("+"), message=f"有消息，{text}条")


def inspect_home_badge(inspector, policy) -> dict:
    """Reuse the caller's device lease, bounded home recovery and incident sink."""
    from engagement_inspection import DOUYIN_PACKAGE, foreground_package
    from control_vision import VisionCandidateLocator, visual_navigation_enabled

    started = now_iso()
    inspector._evidence_workflow = "home_badge"
    inspector._inspection_deadline = inspector._clock() + 60
    source, home, fatal = "", False, None
    missing, evidence = [], []
    result = dict(state="unknown", reason_code="home_not_confirmed", message="检查失败：未能确认首页",
                  source="local", target_bounds=None, evidence_missing=missing,
                  badge_text=None, message_count=None, count_is_lower_bound=False,
                  quantity_status="unreadable", quantity_source=None, badge_bounds=None, rule_version=RULE_VERSION)
    root = Path(inspector._recorder.run_dir)
    root.mkdir(parents=True, exist_ok=True)
    image_path = root / "inspection-home_badge-home.png"
    try:
        if foreground_package(inspector._device) != DOUYIN_PACKAGE:
            if not inspector._navigation_lock():
                raise RuntimeError("device_lock_not_owned")
            # Reuse the established launch/recovery path, without visiting messages.
            source = inspector._prepare_feed()
            home = is_badge_home(inspector, source)
        try:
            if not source:
                source = inspector._dump()
            if not source and "ui_tree" not in missing:
                missing.append("ui_tree")
        except Exception as exc:
            reason = inspector._public_reason(exc)
            if reason in {"login_required", "identity_verification_required", "douyin_not_foreground", "foreground_package_changed"}:
                raise
            missing.append("ui_tree")
        if source:
            home = is_badge_home(inspector, source)
            if not home:
                if not inspector._navigation_lock():
                    raise RuntimeError("device_lock_not_owned")
                home = inspector._cold_restart_home()
                if not home:
                    raise RuntimeError("main_feed_not_ready")
                source = inspector._dump()
                home = is_badge_home(inspector, source)
                if not home:
                    raise RuntimeError("main_feed_not_ready")
    except Exception as exc:
        fatal = inspector._public_reason(exc)
        result["reason_code"] = fatal
    entry = dict(id=inspector._inspection_id(), name="home", label="首页消息角标", section="home_badge", captured_at=now_iso())
    if source:
        try:
            tree_name = "inspection-home_badge-home.xml.gz"
            with gzip.open(root/tree_name, "wt", encoding="utf-8") as stream:
                stream.write(source)
            entry["ui_tree_name"] = tree_name
        except Exception:
            missing.append("ui_tree")
    image = None
    try:
        image = inspector._recorder.screenshot(inspector._device, image_path.stem)
        if not isinstance(image, Image.Image) or not image_path.is_file():
            raise RuntimeError("screenshot_unavailable")
        entry.update(image_name=image_path.name, image_sha256=hashlib.sha256(image_path.read_bytes()).hexdigest())
        if image.size != (inspector._width, inspector._height):
            raise RuntimeError("screenshot_size_changed")
        if foreground_package(inspector._device) != DOUYIN_PACKAGE:
            raise RuntimeError("douyin_not_foreground")
        if home and not fatal:
            result = analyze_badge(image, source)
    except Exception:
        missing.append("screenshot")
        result.update(state="unknown", reason_code="screenshot_unavailable", message="检查失败：截图不可用")
        image = None
    if (image is not None and not fatal and visual_navigation_enabled(policy)
            and result.get("quantity_status") != "conflict"
            and (result["state"] == "unknown" or (result["state"] == "present" and result.get("numeric_appearance") and not result.get("badge_text")))):
        try:
            observer = inspector._vision_locator or VisionCandidateLocator()
            observed = observer.read_home_badge(image_path, timeout_seconds=min(20, inspector._remaining()))
            inspector._remaining()  # Late responses cannot become successful observations.
            if observed["page_type"] == "home" and observed["state"] in {"present", "absent"}:
                region = observed["region"]
                if (result["state"] == "present" or result.get("badge_bounds")) and observed["state"] != "present":
                    raise RuntimeError("badge_evidence_conflict")
                result.update(state=observed["state"], source="vision", reason_code="visual_home_badge",
                              message="有消息" if observed["state"] == "present" else "无消息",
                              target_bounds=[int(region[i]*image.size[i%2]) for i in range(4)])
                home = True
                if observed["state"] == "present":
                    apply_badge_text(result, observed.get("badge_text"))
                    if result.get("badge_text"):
                        result.update(quantity_status="recognized",quantity_source="vision")
                    elif result.get("quantity_status") == "dot":
                        result.update(quantity_status="dot",quantity_source="vision")
                    else:
                        # Vision's null contract means either dot or illegible
                        # digits; only prior positive local dot evidence can
                        # distinguish them. Presence alone supplies no quantity.
                        result.update(reason_code="badge_quantity_unreadable",quantity_status="unreadable",message="有消息，数字未能确认")
                else:
                    result.update(quantity_status="none",quantity_source="vision")
        except Exception as exc:
            if result["state"] == "unknown":
                result["reason_code"] = inspector._public_reason(exc)
            else:
                result["quantity_reason"] = "数字未能确认，保留有消息状态"
    result["evidence_missing"] = sorted(set(missing))
    result.pop("numeric_appearance", None)
    entry["target_bounds"] = result["target_bounds"]
    entry["evidence_missing"] = result["evidence_missing"]
    evidence.append(entry)
    if image is not None and result.get("badge_bounds"):
        try:
            crop_path=root/"inspection-home_badge-crop.png"
            image.crop(result["badge_bounds"]).save(crop_path)
            evidence.append(dict(id=entry["id"]+"-badge",name="badge_crop",label="消息角标原始裁剪",
                section="home_badge",captured_at=entry["captured_at"],image_name=crop_path.name,
                image_sha256=hashlib.sha256(crop_path.read_bytes()).hexdigest(),
                target_bounds=result["badge_bounds"],rule_version=RULE_VERSION))
        except Exception:
            result["evidence_missing"].append("badge_crop")
    status = "failed" if fatal else "degraded" if result["state"] == "unknown" else "completed"
    summary = dict(conclusion=result["message"], home_badge=result, inspection_id=inspector._inspection_id(),
                   evidence_count=len(evidence), last_checked_at=now_iso())
    metadata = dict(workflow_version="home_badge", alert_sources=["home_badge"] if result["state"]=="present" else [],
                    inspection_id=inspector._inspection_id(), result_kind={"present":"alert","absent":"clear","unknown":"incomplete"}[result["state"]],
                    evidence_count=len(evidence), conclusion=result["message"], alert_created=False, alert_id=None)
    output = dict(status=status, task_type="douyin_engagement_inspection", workflow_version="home_badge",
                  restored=home, failure_reason=result["reason_code"] if status!="completed" else None,
                  home_badge=result, sections={}, evidence=evidence, inspection_metadata=metadata)
    if inspector._store is not None:
        try:
            inspector._store.record_interaction_inspection(inspection_id=metadata["inspection_id"], task_id=inspector._task_id,
                device_id=inspector._device_id, workflow_version="home_badge", status=status, result_kind=metadata["result_kind"],
                restored=home, summary=summary, evidence=evidence, run_dir=str(root), started_at=started, finished_at=now_iso())
            device = inspector._store.get_virtual_device_for_adb(inspector._device_id)
            permanent_id = device.get("virtual_device_id") if device else None
            if not permanent_id:
                from device_profiles import load_device_profiles
                serial = inspector._device_id
                profile = load_device_profiles().get(serial)
                # Physical USB identity is stable across app/service restarts.
                # Never turn a missing emulator binding into a physical phone.
                if profile and profile.verified and not (":" in serial or serial.startswith("emulator-")):
                    permanent_id = "physical:" + hashlib.sha256(serial.encode("utf-8")).hexdigest()
            if not permanent_id:
                raise RuntimeError("permanent_device_identity_unavailable")
            metadata.update(inspector._store.record_home_badge_observation(permanent_device_id=permanent_id,
                device_id=inspector._device_id, task_id=inspector._task_id, state=result["state"], summary=summary))
        except Exception as exc:
            output.update(status="degraded", failure_reason="home_badge_persistence_failed")
            metadata.update(result_kind="incomplete", conclusion="检查记录保存失败")
            summary["conclusion"] = metadata["conclusion"]
            try:
                inspector._store.record_interaction_inspection(inspection_id=metadata["inspection_id"], task_id=inspector._task_id,
                    device_id=inspector._device_id, workflow_version="home_badge", status="degraded", result_kind="incomplete",
                    restored=home, summary=summary, evidence=evidence, run_dir=str(root), started_at=started, finished_at=now_iso())
            except Exception:
                pass  # The file receipt and shared incident sink remain fallback evidence.
            inspector._recorder.emit("home_badge_persistence_failed", error_type=type(exc).__name__)
    # File receipt remains available even when database storage fails.
    (root/"home-badge-receipt.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if output["status"] != "completed":
        inspector._record_failure(stage="home_badge", reason=output["failure_reason"], error_type="HomeBadgeCheck",
            section="home_badge", workflow_version="home_badge", outcome="device_fatal" if fatal else "inspection_failed",
            recovery_action="check_device" if fatal else "retry_home_badge", source=source or None,
            context={"home_badge":result,"restored":home})
    inspector._recorder.emit("home_badge_finished", **output)
    return output
