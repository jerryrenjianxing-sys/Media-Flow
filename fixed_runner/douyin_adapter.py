from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from control_vision import VisionCandidateLocator
from douyin_fixed_runner import DOUYIN_PACKAGE, PROFILE, color_ratio, crop_relative
from douyin_uia2_runner import (
    Uia2DouyinRunner,
    Uia2RunRecorder,
    comment_input_activation_source_confirmed,
    comment_panel_source_visible,
    classify_douyin_page_source,
    find_bottom_navigation_bounds,
    find_comment_input_bounds,
    find_control_bounds,
    find_editable_bounds,
    find_search_result_bounds,
    foreground_package,
)
from execution_tasks import send_comment
from platform_adapters import CalibrationResult
from platform_profiles import ADAPTER_VERSION, normalize_bounds


CONTROL_KEYWORDS = {
    "like": "喜欢",
    "favorite": "收藏",
    "comments": "评论",
}


class DouyinAdapter:
    platform_id = "douyin"
    package_name = DOUYIN_PACKAGE
    adapter_version = ADAPTER_VERSION

    def __init__(
        self,
        device,
        recorder: Uia2RunRecorder,
        *,
        device_id: str,
        vision_locator: VisionCandidateLocator | None = None,
    ) -> None:
        self.device = device
        self.recorder = recorder
        self.runner = Uia2DouyinRunner(
            device, recorder, PROFILE, max_gate_skips=2, device_id=device_id
        )
        self.vision_locator = vision_locator

    def app_version(self) -> str:
        try:
            info = self.device.app_info(self.package_name) or {}
        except Exception:
            info = {}
        return str(
            info.get("versionName")
            or info.get("version_name")
            or info.get("versionCode")
            or "unknown"
        )

    def ensure_ready(self) -> None:
        try:
            if not self.device.app_info(self.package_name):
                raise RuntimeError("抖音尚未安装")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("无法确认抖音安装状态") from exc
        self.runner.ensure_app_ready()
        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        if foreground_package(self.device) != self.package_name:
            raise RuntimeError("抖音未处于前台")
        login_markers = ("登录即可", "手机号登录", "验证码登录", "一键登录")
        if any(marker in source for marker in login_markers):
            raise RuntimeError("抖音账号需要人工登录或验证")

    def classify_page(self) -> str:
        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        current = foreground_package(self.device)
        if current != self.package_name:
            return "external"
        if comment_panel_source_visible(source):
            return "comment_panel"
        if find_editable_bounds(source, *self.device.window_size()) is not None:
            return "search_input"
        if "视频" in source and find_search_result_bounds(
            source, *self.device.window_size()
        ):
            return "search_results"
        return "feed"

    def _semantic_control(
        self,
        name: str,
        bounds: tuple[int, int, int, int] | None,
        *,
        rule: dict[str, Any],
        evidence: str,
    ) -> dict[str, Any] | None:
        if bounds is None:
            return None
        width, height = self.device.window_size()
        return {
            "semantic_name": name,
            "locator": rule,
            "normalized_region": normalize_bounds(bounds, width, height),
            "source": "semantic_rule",
            "confidence": 1.0,
            "verified": True,
            "evidence": evidence,
        }

    def _record_missing_candidate(
        self,
        *,
        name: str,
        screenshot_path: Path,
        page_hint: str,
        warnings: list[str],
        evidence: list[str],
    ) -> None:
        if self.vision_locator is None:
            warnings.append(f"missing:{name}:cloud_vision_unavailable")
            return
        try:
            candidate = self.vision_locator.locate(
                screenshot_path, semantic_name=name, page_hint=page_hint
            )
        except Exception as exc:
            warnings.append(f"missing:{name}:vision_failed:{type(exc).__name__}")
            return
        # AI candidates are evidence only until a fixed semantic state change
        # verifies them.  They never enter the executable control map directly.
        evidence.append(f"ai_candidate:{name}:{candidate.public_dict()}")
        warnings.append(f"missing:{name}:candidate_requires_fixed_verification")

    def _seek_home_video_for_calibration(
        self,
        image,
        source: str,
        *,
        warnings: list[str],
        evidence: list[str],
        max_image_notes: int = 20,
    ):
        """Advance only past confirmed home image notes before control calibration."""
        source_path = self.recorder.run_dir / "initialization-main-feed.xml"
        for skipped in range(max_image_notes):
            page_type = classify_douyin_page_source(
                source,
                foreground_package(self.device),
                image.width,
                image.height,
            )
            if page_type != "home_image_note":
                return image, source, source_path
            self.recorder.emit(
                "initialization_non_video_feed_item",
                skipped=skipped + 1,
                page_type=page_type,
            )
            self.runner.swipe_next(skipped, skipped + 1)
            image = self.recorder.screenshot(
                self.device, f"initialization-home-video-{skipped + 1}"
            )
            self.runner.ensure_profile(image)
            source = self.device.dump_hierarchy(compressed=True, pretty=False)
            source_path = (
                self.recorder.run_dir
                / f"initialization-home-video-{skipped + 1}.xml"
            )
            source_path.write_text(source, encoding="utf-8")
            evidence.extend(
                (
                    str(source_path),
                    str(
                        self.recorder.run_dir
                        / f"initialization-home-video-{skipped + 1}.png"
                    ),
                )
            )
        warnings.append("home_video_supply_insufficient_during_calibration")
        return image, source, source_path

    def calibrate_browsing(self) -> CalibrationResult:
        """Verify the read-only home-feed plane without requiring text input."""
        self.ensure_ready()
        image = self.recorder.screenshot(self.device, "initialization-main-feed")
        self.runner.ensure_profile(image)
        width, height = image.size
        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        source_path = self.recorder.run_dir / "initialization-main-feed.xml"
        source_path.write_text(source, encoding="utf-8")
        warnings: list[str] = []
        evidence = [
            str(source_path),
            str(self.recorder.run_dir / "initialization-main-feed.png"),
        ]
        image, source, source_path = self._seek_home_video_for_calibration(
            image,
            source,
            warnings=warnings,
            evidence=evidence,
        )
        width, height = image.size
        controls: dict[str, dict[str, Any]] = {}
        home = self._semantic_control(
            "home",
            find_bottom_navigation_bounds(source, "首页", width, height),
            rule={"kind": "bottom_navigation", "label": "首页"},
            evidence=str(source_path),
        )
        if home:
            controls["home"] = home
        for name, keyword in CONTROL_KEYWORDS.items():
            _before, gate = self.runner.capture_gate(0, name)
            bounds = self.runner.control_bounds.get(name) if gate.allowed else None
            control = self._semantic_control(
                name,
                bounds,
                rule={"kind": "content_description", "contains": keyword, "button": True},
                evidence=str(self.recorder.run_dir / f"video-0-{name}-before.png"),
            )
            if control:
                controls[name] = control
        return CalibrationResult(
            page_type="home_feed",
            controls=controls,
            capabilities={
                "main_feed": "home" in controls,
                "search": False,
                "like": "like" in controls,
                "favorite": "favorite" in controls,
                "comment": False,
                "search_feed": False,
            },
            evidence=evidence,
            warnings=warnings + ["input_component_unavailable:search_and_comment_not_verified"],
        )

    def calibrate_navigation(self, search_query: str) -> CalibrationResult:
        self.ensure_ready()
        image = self.recorder.screenshot(self.device, "initialization-main-feed")
        self.runner.ensure_profile(image)
        width, height = image.size
        source = self.device.dump_hierarchy(compressed=True, pretty=False)
        source_path = self.recorder.run_dir / "initialization-main-feed.xml"
        source_path.write_text(source, encoding="utf-8")
        controls: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        evidence = [str(source_path), str(self.recorder.run_dir / "initialization-main-feed.png")]
        image, source, source_path = self._seek_home_video_for_calibration(
            image,
            source,
            warnings=warnings,
            evidence=evidence,
        )
        width, height = image.size

        initial_controls = {
            "home": self._semantic_control(
                "home",
                find_bottom_navigation_bounds(source, "首页", width, height),
                rule={"kind": "bottom_navigation", "label": "首页"},
                evidence=str(source_path),
            ),
            "search": self._semantic_control(
                "search",
                find_control_bounds(
                    source, "搜索", width, height, require_button_label=False
                ),
                rule={"kind": "content_description", "contains": "搜索"},
                evidence=str(source_path),
            ),
        }
        controls.update({name: value for name, value in initial_controls.items() if value})

        for name, keyword in CONTROL_KEYWORDS.items():
            before, gate = self.runner.capture_gate(0, name)
            bounds = self.runner.control_bounds.get(name) if gate.allowed else None
            control = self._semantic_control(
                name,
                bounds,
                rule={"kind": "content_description", "contains": keyword, "button": True},
                evidence=str(self.recorder.run_dir / f"video-0-{name}-before.png"),
            )
            if control:
                controls[name] = control
            elif name == "comments":
                # The runner names the comment action directly for this calibration.
                warnings.append("missing:comments:semantic_gate")

        if "comments" in controls:
            self.runner.tap_control("comments", "initialization_open_comments")
            time.sleep(0.8)
            panel_image = self.recorder.screenshot(
                self.device, "initialization-comment-panel"
            )
            panel_source = self.device.dump_hierarchy(compressed=True, pretty=False)
            panel_source_path = self.recorder.run_dir / "initialization-comment-panel.xml"
            panel_source_path.write_text(panel_source, encoding="utf-8")
            evidence.extend((str(panel_source_path), str(self.recorder.run_dir / "initialization-comment-panel.png")))
            input_bounds = find_comment_input_bounds(panel_source, width, height)
            input_control = self._semantic_control(
                "comment_input",
                input_bounds,
                rule={
                    "kind": "comment_input",
                    "class_or_hint_or_clickable_parent": True,
                },
                evidence=str(panel_source_path),
            )
            close_control = self._semantic_control(
                "comment_close",
                find_control_bounds(
                    panel_source, "关闭", width, height, require_button_label=False
                ),
                rule={"kind": "content_description", "contains": "关闭"},
                evidence=str(panel_source_path),
            )
            if input_control and input_bounds is not None:
                left, top, right, bottom = input_bounds
                self.device.click(round((left + right) / 2), round((top + bottom) / 2))
                time.sleep(0.5)
                activated_source = self.device.dump_hierarchy(
                    compressed=True, pretty=False
                )
                activated_path = (
                    self.recorder.run_dir
                    / "initialization-comment-input-activated.xml"
                )
                activated_path.write_text(activated_source, encoding="utf-8")
                self.recorder.screenshot(
                    self.device, "initialization-comment-input-activated"
                )
                evidence.append(str(activated_path))
                if comment_input_activation_source_confirmed(
                    activated_source, width, height
                ):
                    input_control["evidence"] = str(activated_path)
                    controls["comment_input"] = input_control
                    self.device.press("back")
                    time.sleep(0.3)
                else:
                    warnings.append("missing:comment_input:activation_not_verified")
            if close_control:
                controls["comment_close"] = close_control
            if not comment_panel_source_visible(panel_source):
                warnings.append("comment_panel:semantic_confirmation_failed")
            self.runner.close_comment_panel(0, "initialization-comment-panel-closed")

        self.runner.enter_topic_search(search_query)
        # A single navigation-only swipe verifies the input channel without
        # performing any account mutation.
        self.runner.swipe_next(0, 1)
        self.recorder.screenshot(self.device, "initialization-search-feed-after-swipe")
        search_image_path = self.recorder.run_dir / "topic-search-results.png"
        results_source_path = self.recorder.run_dir / "topic-search-results.xml"
        if results_source_path.is_file():
            results_source = results_source_path.read_text(encoding="utf-8")
            result_control = self._semantic_control(
                "search_result",
                find_search_result_bounds(results_source, width, height),
                rule={"kind": "semantic_video_result", "caption_or_cover": True},
                evidence=str(results_source_path),
            )
            if result_control:
                controls["search_result"] = result_control
        controls["video_tab"] = {
            "semantic_name": "video_tab",
            "locator": {"kind": "text", "equals": "视频"},
            "normalized_region": None,
            "source": "semantic_rule",
            "confidence": 1.0,
            "verified": True,
            "evidence": str(results_source_path),
        }

        required = {
            "home",
            "search",
            "video_tab",
            "search_result",
            "like",
            "favorite",
            "comments",
            "comment_input",
            "comment_close",
        }
        for missing in sorted(required - controls.keys()):
            candidate_image = search_image_path if search_image_path.is_file() else Path(evidence[1])
            self._record_missing_candidate(
                name=missing,
                screenshot_path=candidate_image,
                page_hint="douyin initialization calibration",
                warnings=warnings,
                evidence=evidence,
            )

        capabilities = {
            "main_feed": "home" in controls,
            "search": all(name in controls for name in ("search", "video_tab", "search_result")),
            "like": "like" in controls,
            "favorite": "favorite" in controls,
            "comment": all(name in controls for name in ("comments", "comment_input", "comment_close")),
            "search_feed": bool(self.runner.allow_search_feed and self.runner.search_query),
        }
        return CalibrationResult(
            page_type="search_feed",
            controls=controls,
            capabilities=capabilities,
            evidence=evidence,
            warnings=warnings,
        )

    def _restore_reaction(self, action: str, color: str, threshold: float) -> None:
        _before, gate = self.runner.capture_gate(0, action)
        if not gate.allowed:
            raise RuntimeError(f"Cannot safely restore {action}")
        self.runner.tap_control(action, f"initialization_restore_{action}")
        time.sleep(0.8)
        after = self.recorder.screenshot(
            self.device, f"initialization-{action}-restored"
        )
        bounds = self.runner.control_bounds[action]
        patch = crop_relative(
            after,
            (
                bounds[0] / after.width,
                bounds[1] / after.height,
                bounds[2] / after.width,
                bounds[3] / after.height,
            ),
        )
        if color_ratio(patch, color) >= threshold:
            raise RuntimeError(f"{action} restore verification failed")

    def run_write_acceptance(self, comment: str) -> dict[str, Any]:
        # This method is only reached after a per-run explicit API option.  No
        # state-changing outcome is replayed when verification is unknown.
        if not self.runner.allow_search_feed:
            raise RuntimeError("Write acceptance requires a verified search feed")
        results: dict[str, Any] = {
            "like_changed_and_restored": False,
            "favorite_changed_and_restored": False,
            "comment_verified": False,
        }
        before, gate = self.runner.capture_gate(0, "like")
        if not gate.allowed:
            raise RuntimeError("Current video is not safe for write acceptance")
        changed = self.runner.like_verified(0, before)
        if changed:
            self._restore_reaction("like", "red", 0.035)
            results["like_changed_and_restored"] = True

        before, gate = self.runner.capture_gate(0, "favorite")
        if not gate.allowed:
            raise RuntimeError("Favorite acceptance gate failed")
        changed = self.runner.favorite_verified(0, before)
        if changed:
            self._restore_reaction("favorite", "yellow", 0.025)
            results["favorite_changed_and_restored"] = True

        _before, gate = self.runner.capture_gate(0, "comment-preview")
        if not gate.allowed:
            raise RuntimeError("Comment acceptance gate failed")
        self.runner.tap_control("comment-preview", "initialization_open_comment")
        time.sleep(0.8)
        results["comment_verified"] = send_comment(
            self.device,
            self.recorder,
            comment,
            0,
            expected_size=(self.runner.profile.width, self.runner.profile.height),
        )
        results["comment_screenshot"] = str(
            self.recorder.run_dir / "video-0-comment-sent.png"
        )
        return results
