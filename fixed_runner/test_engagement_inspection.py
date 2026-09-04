from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engagement_inspection as engagement_module  # noqa: E402
from douyin_fixed_runner import DOUYIN_PACKAGE  # noqa: E402
from engagement_inspection import (  # noqa: E402
    EngagementInspector,
    InspectionPolicy,
    V2PreconditionMismatch,
    compare_visitor_baseline,
    find_v2_filter_title_bounds,
    parse_entry_badge,
    parse_message_badge,
    parse_private_messages,
    parse_profile_visitors,
    parse_received_likes,
    readable_v2_entries,
    visitor_ui_fingerprint,
)
from execution_tasks import execute_task  # noqa: E402
from task_store import TaskStore  # noqa: E402
from PIL import Image  # noqa: E402


def node(
    text: str = "",
    *,
    description: str = "",
    bounds: str = "[0,0][1080,200]",
    clickable: bool = False,
) -> str:
    return (
        f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
        f'text="{text}" content-desc="{description}" '
        f'clickable="{str(clickable).lower()}" bounds="{bounds}" />'
    )


def nav(*, unread: str = "") -> str:
    message_desc = f"消息，{unread}，按钮" if unread else "消息，按钮"
    return "".join(
        (
            node(description="首页，按钮", bounds="[0,2200][260,2400]", clickable=True),
            node(description=message_desc, bounds="[550,2200][810,2400]", clickable=True),
            node(description="我，按钮", bounds="[820,2200][1080,2400]", clickable=True),
        )
    )


def hierarchy(*children: str) -> str:
    return f"<hierarchy>{''.join(children)}</hierarchy>"


HOME = hierarchy(node("推荐"), nav(unread="3条未读"))
MESSAGE = hierarchy(
    node("消息"),
    node(description="私信会话，张三，昨天，你好，2条未读"),
    node(description="推荐卡片，可能感兴趣的人"),
    node(description="互动消息，按钮", bounds="[40,300][500,520]", clickable=True),
    nav(),
)
LIKES = hierarchy(
    node("互动消息"),
    node(description="Alice赞了你的作品，2小时前"),
    node(description="Bob赞了你，回赞按钮"),
    nav(),
)
COMBINED_ACTIVITY = hierarchy(
    node("全部消息"),
    node(description="Alice赞了你的作品，2小时前"),
    node(description="访客甲 等 10 人近期访问过你的主页，9小时前"),
    nav(),
)
PROFILE = hierarchy(
    node("我的主页"),
    node(description="主页访客，按钮", bounds="[40,300][500,520]", clickable=True),
    nav(),
)
VISITORS = hierarchy(
    node("主页访客"),
    node(description="访客记录，Carol，3小时前"),
    nav(),
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, **payload) -> None:
        self.events.append((event, payload))


class V2Recorder(Recorder):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.run_dir = root

    def screenshot(self, _device, name: str):
        image = Image.new("RGB", (1080, 2400), "white")
        image.save(self.run_dir / f"{name}.png")
        return image


class FakeDevice:
    def __init__(self, pages: dict[str, str] | None = None, *, foreground=True) -> None:
        self.pages = {
            "home": HOME,
            "message": MESSAGE,
            "likes": LIKES,
            "profile": PROFILE,
            "visitors": VISITORS,
            **(pages or {}),
        }
        self.state = "home"
        self.foreground = DOUYIN_PACKAGE if foreground else "com.android.launcher"
        self.clicks: list[tuple[str, int, int]] = []
        self.app_starts = 0

    def window_size(self):
        return 1080, 2400

    def shell(self, *_args, **_kwargs):
        raise RuntimeError("use app_current fallback")

    def app_current(self):
        return {"package": self.foreground}

    def dump_hierarchy(self, compressed=True, pretty=False):
        return self.pages[self.state]

    def click(self, x: int, y: int) -> None:
        self.clicks.append((self.state, x, y))
        if y >= 2200 and x < 300:
            self.state = "home"
        elif y >= 2200 and 540 <= x <= 820:
            self.state = "message"
        elif y >= 2200 and x > 820:
            self.state = "profile"
        elif self.state == "message" and y < 1000:
            self.state = "likes"
        elif self.state == "profile" and y < 1000:
            self.state = "visitors"
        else:
            raise AssertionError(f"unexpected click from {self.state}: {(x, y)}")

    def press(self, key: str) -> None:
        if key != "back":
            raise AssertionError(key)
        self.state = "home"

    def app_start(self, package: str, stop=False, wait=True) -> None:
        self.app_starts += 1
        self.foreground = package
        self.state = "home"


def v3_nav() -> str:
    return "".join(
        (
            node(description="首页，按钮", bounds="[0,1450][220,1600]", clickable=True),
            node(description="朋友，按钮", bounds="[220,1450][440,1600]", clickable=True),
            node(description="消息，按钮", bounds="[650,1450][900,1600]", clickable=True),
        )
    )


def v3_page(*children: str) -> str:
    return hierarchy(*children, v3_nav())


def v3_policy() -> dict[str, object]:
    calibration = {
        "profile_version": "mediaflow-engagement-v3-r1",
        "device_id": "device-v3",
        "app_version": "35.8.0",
        "display_signature": "900x1600x320x0x100",
        "passes": 3,
        "later_passes_semantically_equal": True,
        "controls": {"aggregate": ["互动消息"]},
        "sections": {
            "received_likes": True,
            "comment_danmaku": True,
            "profile_visitors": True,
        },
    }
    return {
        "inspection_workflow_version": "v3",
        "expected_app_version": "35.8.0",
        "expected_display_signature": "900x1600x320x0x100",
        "inspection_calibration": calibration,
        "max_items_per_section": 100,
    }


class V3Device(FakeDevice):
    riskflow_display_signature = "900x1600x320x0x100"
    info = {"displayRotation": 0}

    def __init__(self, activity_pages: list[str]) -> None:
        message = v3_page(
            node("消息", bounds="[380,40][520,130]"),
            (
                f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
                'text="" content-desc="" clickable="true" bounds="[0,280][900,450]">'
                + node("互动消息", bounds="[120,320][350,390]")
                + "</node>"
            ),
        )
        super().__init__(
            {
                "home": v3_page(node("推荐", bounds="[380,40][520,130]")),
                "message": message,
                **{
                    f"activity-{index}": page
                    for index, page in enumerate(activity_pages)
                },
            }
        )
        self.activity_pages = activity_pages
        self.activity_index = 0
        self.swipes = 0

    def window_size(self):
        return 900, 1600

    def app_info(self, _package):
        return {"versionName": "35.8.0"}

    def click(self, x: int, y: int) -> None:
        self.clicks.append((self.state, x, y))
        if y >= 1450 and x < 220:
            self.state = "home"
        elif y >= 1450 and x >= 650:
            self.state = "message"
        elif self.state == "message":
            self.state = "activity-0"
        else:
            raise AssertionError(f"unexpected click from {self.state}: {(x, y)}")

    def swipe(self, *_args, **_kwargs):
        self.swipes += 1
        if self.state.startswith("activity-"):
            self.activity_index = min(
                self.activity_index + 1, len(self.activity_pages) - 1
            )
            self.state = f"activity-{self.activity_index}"

    def press(self, key: str) -> None:
        if key != "back":
            raise AssertionError(key)
        self.state = "home"


class EngagementParsingTest(unittest.TestCase):
    def test_v3_finds_moved_interaction_entry_through_clickable_parent(self) -> None:
        source = hierarchy(
            node("消息"),
            (
                f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
                'text="" content-desc="" clickable="true" bounds="[0,560][900,720]">'
                + node("互动消息", bounds="[142,590][330,650]")
                + "</node>"
            ),
        )
        self.assertEqual(
            engagement_module.find_unified_activity_entry_bounds(source, 900, 1600),
            (0, 560, 900, 720),
        )

    def test_v3_rejects_ambiguous_interaction_entries(self) -> None:
        source = hierarchy(
            node(description="互动消息，按钮", bounds="[0,320][900,470]", clickable=True),
            node(description="互动消息，按钮", bounds="[0,620][900,770]", clickable=True),
        )
        with self.assertRaisesRegex(ValueError, "interaction_entry_ambiguous"):
            engagement_module.find_unified_activity_entry_bounds(source, 900, 1600)

    def test_v3_read_boundary_keeps_only_unread_rows_above_it(self) -> None:
        source = hierarchy(
            node("互动消息", bounds="[300,60][600,150]"),
            node(description="Alice赞了你的作品，2小时前", bounds="[30,220][870,360]", clickable=True),
            node(description="Bob评论了你的作品，1小时前", bounds="[30,370][870,510]", clickable=True),
            node(description="Carol近期访问过你的主页，30分钟前", bounds="[30,520][870,660]", clickable=True),
            node("已读", bounds="[400,700][500,750]"),
            node(description="Old赞了你的作品，昨天", bounds="[30,780][870,920]", clickable=True),
        )
        result = engagement_module.parse_unified_activity_viewport(
            source, InspectionPolicy(), width=900, height=1600
        )
        self.assertEqual(result["boundary"], "read")
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(
            result["categories"],
            ["received_likes", "comment_danmaku", "profile_visitors"],
        )
        self.assertNotIn("Old", json.dumps(result, ensure_ascii=False))

    def test_v3_explicit_empty_is_complete_without_private_message_guessing(self) -> None:
        source = hierarchy(
            node("互动消息", bounds="[300,60][600,150]"),
            node("暂无互动消息", bounds="[300,600][600,680]"),
        )
        result = engagement_module.parse_unified_activity_viewport(
            source, InspectionPolicy(), width=900, height=1600
        )
        self.assertEqual(result["boundary"], "explicit_empty")
        self.assertEqual(result["items"], [])

    def test_v3_groups_empty_clickable_parent_and_child_labels_into_one_item(self) -> None:
        source = hierarchy(
            node("互动消息", bounds="[300,60][600,150]"),
            (
                f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
                'text="" content-desc="" clickable="true" bounds="[20,220][880,420]">'
                + node("Alice", bounds="[130,245][330,300]")
                + node("赞了你的作品", bounds="[130,305][620,360]")
                + node("2小时前", bounds="[690,245][850,300]")
                + "</node>"
            ),
            node("已读", bounds="[410,500][490,560]"),
        )
        result = engagement_module.parse_unified_activity_viewport(
            source, InspectionPolicy(), width=900, height=1600
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["display_name"], "Alice")
        self.assertEqual(result["items"][0]["category"], "received_likes")

    def test_v3_visitor_disabled_prompt_is_not_counted_as_an_interaction(self) -> None:
        source = hierarchy(
            node("互动消息", bounds="[300,60][600,150]"),
            node("访客记录已关闭", bounds="[180,250][720,330]"),
            node("已读", bounds="[410,500][490,560]"),
        )
        result = engagement_module.parse_unified_activity_viewport(
            source, InspectionPolicy(), width=900, height=1600
        )
        self.assertTrue(result["visitor_history_disabled"])
        self.assertEqual(result["items"], [])

    def test_v2_readable_entries_keep_real_fields_and_omit_missing_ones(self) -> None:
        source = hierarchy(
            node(
                description="本地昵称，赞了你的作品，2小时前",
                bounds="[80,400][1000,620]",
            ),
            node(description="消息，按钮", bounds="[550,2200][810,2400]", clickable=True),
        )
        entries = readable_v2_entries(
            source,
            InspectionPolicy(),
            section="received_likes",
            width=1080,
            height=2400,
        )
        self.assertEqual(entries[0]["display_name"], "本地昵称")
        self.assertEqual(entries[0]["time"], "2小时前")
        self.assertNotIn("unread_count", entries[0])

    def test_v2_readable_entries_prefer_one_composite_row_over_children(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/r8y" '
            'class="android.view.ViewGroup" clickable="true" '
            'content-desc="本地昵称，赞了你的评论 1天前，按钮" '
            'bounds="[80,400][1000,620]" />',
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/iv_avatar" '
            'class="android.widget.FrameLayout" clickable="true" '
            'content-desc="本地昵称的头像" bounds="[80,400][220,540]" />',
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/r8a" '
            'class="android.widget.TextView" clickable="true" '
            'text="赞了你的评论 1天前" bounds="[240,500][900,580]" />',
        )
        entries = readable_v2_entries(
            source,
            InspectionPolicy(),
            section="received_likes",
            width=1080,
            height=2400,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["display_name"], "本地昵称")

    def test_v2_sanitized_fixture_records_three_matching_mbh_passes(self) -> None:
        fixture = json.loads((Path(__file__).resolve().parent / "test_fixtures" / "engagement_inspection" / "mbh_douyin5_v2_2026_09_01.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["passes"], 3)
        self.assertTrue(fixture["later_passes_semantically_equal"])
        self.assertFalse(fixture["prohibited_action_taken"])
        self.assertFalse(fixture["retained_sensitive_content"])

    def test_v2_calibration_snapshot_is_bound_to_device_and_stable_passes(self) -> None:
        class CalibrationDevice:
            riskflow_display_signature = "1080x2340x480x0x101"

            def window_size(self):
                return (1080, 2340)

            def app_info(self, _package):
                return {"versionName": "40.2.0"}

        inspector = EngagementInspector(
            CalibrationDevice(),
            SimpleNamespace(emit=lambda *_args, **_kwargs: None),
            device_id="device-1",
        )
        base = {
            "expected_app_version": "40.2.0",
            "expected_display_signature": "1080x2340x480x0x101",
            "inspection_calibration": {
                "profile_version": "douyin-engagement-v2-device1-r1",
                "device_id": "device-1",
                "app_version": "40.2.0",
                "display_signature": "1080x2340x480x0x101",
                "passes": 3,
                "later_passes_semantically_equal": True,
                "controls": {"aggregate": ["互动消息"]},
            },
        }
        inspector._validate_v2_calibration(base)
        with self.assertRaisesRegex(RuntimeError, "v2_calibration_device_mismatch"):
            inspector._validate_v2_calibration(
                {
                    **base,
                    "inspection_calibration": {
                        **base["inspection_calibration"],
                        "device_id": "another-device",
                    },
                }
            )
        with self.assertRaisesRegex(RuntimeError, "v2_calibration_unstable"):
            inspector._validate_v2_calibration(
                {
                    **base,
                    "inspection_calibration": {
                        **base["inspection_calibration"],
                        "later_passes_semantically_equal": False,
                    },
                }
            )

    def test_v2_runtime_version_drift_is_structured_before_navigation(self) -> None:
        class DriftedDevice:
            riskflow_display_signature = "1080x2340x480x0x101"

            def window_size(self):
                return (1080, 2340)

            def app_info(self, _package):
                return {"versionName": "40.3.0"}

        inspector = EngagementInspector(
            DriftedDevice(),
            SimpleNamespace(emit=lambda *_args, **_kwargs: None),
            device_id="device-1",
        )
        policy = {
            "expected_app_version": "40.2.0",
            "expected_display_signature": "1080x2340x480x0x101",
            "inspection_calibration": {
                "device_id": "device-1",
                "app_version": "40.2.0",
                "display_signature": "1080x2340x480x0x101",
                "passes": 3,
                "later_passes_semantically_equal": True,
                "controls": {"aggregate": ["互动消息"]},
            },
        }
        with self.assertRaises(V2PreconditionMismatch) as raised:
            inspector._validate_v2_calibration(policy)
        mismatch = raised.exception
        self.assertEqual(mismatch.code, "v2_app_version_changed")
        self.assertEqual(mismatch.expected_app_version, "40.2.0")
        self.assertEqual(mismatch.actual_app_version, "40.3.0")
        self.assertTrue(mismatch.recovery_eligible)
        self.assertFalse(mismatch.navigation_started)

    def test_v2_inspection_returns_structured_precondition_for_worker_recovery(self) -> None:
        inspector = EngagementInspector(
            SimpleNamespace(window_size=lambda: (1080, 2340)),
            SimpleNamespace(emit=lambda *_args, **_kwargs: None),
            device_id="device-1",
        )
        mismatch = V2PreconditionMismatch(
            "v2_app_version_changed",
            expected_app_version="40.2.0",
            actual_app_version="40.3.0",
            expected_display_signature="1080x2340x480x0x101",
            actual_display_signature="1080x2340x480x0x101",
        )
        with (
            patch.object(inspector, "_validate_v2_calibration", side_effect=mismatch),
            patch.object(inspector, "_restore_home", return_value=True),
        ):
            result = inspector.inspect({"inspection_workflow_version": "v2"})

        self.assertEqual(result["failure_reason"], "v2_app_version_changed")
        self.assertEqual(result["failure_class"], "recoverable_precondition")
        self.assertEqual(result["actual_app_version"], "40.3.0")
        self.assertTrue(result["recovery_eligible"])
        self.assertFalse(result["navigation_started"])

    def test_v2_entry_badge_is_limited_to_calibrated_row(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" text="赞与收藏" bounds="[0,400][1080,600]">'
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/red_tips_count_view" text="7" />'
            "</node>",
            node(description="普通红色视频卡片"),
        )
        self.assertEqual(parse_entry_badge(source, ("赞与收藏",))["unread_count"], 7)
        self.assertFalse(parse_entry_badge(source, ("收到的评论",))["has_unread"])

    def test_v2_non_clickable_calibrated_filter_title_is_safe_tap_target(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/2ra" class="android.widget.TextView" clickable="false" text="全部消息" bounds="[417,145][621,213]" />'
        )
        self.assertEqual(
            find_v2_filter_title_bounds(source, ("全部消息",), 1080, 2340),
            (417, 145, 621, 213),
        )

    def test_v2_40x_android_title_is_safe_tap_target(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="android:id/text1" class="android.widget.TextView" clickable="false" text="互动消息" bounds="[172,71][296,113]" />'
        )
        self.assertEqual(
            find_v2_filter_title_bounds(source, ("互动消息",), 720, 1600),
            (172, 71, 296, 113),
        )

    def test_v2_403_semantic_filter_title_is_safe_tap_target(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/40r" class="android.widget.TextView" clickable="false" text="全部消息" bounds="[427,130][615,193]" />'
        )
        self.assertEqual(
            find_v2_filter_title_bounds(source, ("全部消息",), 1080, 2340),
            (427, 130, 615, 193),
        )

    def test_v2_visitor_fingerprint_and_conflict_rules(self) -> None:
        source = hierarchy(
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/root_layout" bounds="[0,368][1080,632]" />',
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/3h6" text="synthetic-row" bounds="[120,390][620,470]" />',
        )
        current = {**visitor_ui_fingerprint(source), "visual_hash": "bbbb"}
        self.assertEqual(current["row_count"], 1)
        self.assertNotIn("synthetic-row", current["first_row_hash"])
        self.assertEqual(compare_visitor_baseline(None, current), "baseline_created")
        baseline = {"row_count": 1, "first_row_hash": current["first_row_hash"], "visual_hash": "bbbb"}
        self.assertEqual(compare_visitor_baseline(baseline, current), "unchanged")
        changed = {**current, "first_row_hash": "changed", "visual_hash": "cccc"}
        self.assertEqual(compare_visitor_baseline(baseline, changed), "changed")
        conflict = {**current, "first_row_hash": "changed", "visual_hash": "bbbb"}
        self.assertEqual(compare_visitor_baseline(baseline, conflict), "pending_review")
    def test_mbh_sanitized_fixture_matches_fixed_parsers_and_action_boundary(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "test_fixtures"
            / "engagement_inspection"
            / "mbh_douyin5_2026_09_01.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        source = fixture["synthetic_activity_xml"]
        likes = parse_received_likes(source, InspectionPolicy())
        visitors = parse_profile_visitors(source, InspectionPolicy())

        self.assertEqual(likes["status"], "available")
        self.assertEqual(likes["count"], 1)
        self.assertEqual(visitors["status"], "available")
        self.assertEqual(visitors["count"], 1)
        self.assertEqual(
            fixture["allowed_actions"],
            [
                "bottom_tab:消息",
                "list_entry:互动消息",
                "key:back",
                "bottom_tab:首页",
            ],
        )
        self.assertFalse(fixture["observations"]["prohibited_action_taken"])

    def test_badge_supports_number_dot_and_none(self) -> None:
        self.assertEqual(parse_message_badge(HOME)["unread_count"], 3)
        dot = hierarchy(node("推荐"), nav(unread="有新消息"))
        self.assertEqual(parse_message_badge(dot)["indicator"], "dot")
        self.assertIsNone(parse_message_badge(dot)["unread_count"])
        self.assertEqual(parse_message_badge(hierarchy(nav()))["indicator"], "none")

    def test_private_messages_exclude_recommendation_cards(self) -> None:
        result = parse_private_messages(MESSAGE, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["display_name"], "张三")
        self.assertEqual(result["unread_count"], 2)

    def test_private_messages_accept_structured_full_width_rows(self) -> None:
        conversation_row = (
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'class="android.widget.Button" clickable="true" bounds="[0,700][1080,920]">'
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/tv_title" text="客户甲" />'
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/time" text="昨天" />'
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/preview" text="请问产品规格" />'
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'resource-id="com.ss.android.ugc.aweme:id/red_tips_count_view" text="2" />'
            "</node>"
        )
        public_row = (
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" '
            'class="android.widget.Button" clickable="true" bounds="[0,480][1080,700]" '
            'content-desc="互动消息,未读9条消息" />'
        )
        source = hierarchy(node("消息"), public_row, conversation_row, nav())
        result = parse_private_messages(source, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["display_name"], "客户甲")
        self.assertEqual(result["entries"][0]["unread_count"], 2)

    def test_ambiguous_private_message_rows_fail_closed(self) -> None:
        source = hierarchy(node("消息"), node(description="推荐卡片，小明"), nav())
        self.assertEqual(
            parse_private_messages(source, InspectionPolicy())["status"], "failed"
        )

    def test_likes_ignore_quick_actions(self) -> None:
        result = parse_received_likes(LIKES, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["display_name"], "Alice")

    def test_likes_ignore_nested_summary_without_actor(self) -> None:
        source = hierarchy(
            node(description="Alice赞了你的作品，2小时前"),
            node("赞了你的作品，2小时前"),
        )
        result = parse_received_likes(source, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)

    def test_visitor_disabled_is_unavailable_without_guessing(self) -> None:
        source = hierarchy(node("主页访客"), node("开启主页访客记录"), nav())
        result = parse_profile_visitors(source, InspectionPolicy())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "visitor_history_disabled")

    def test_unknown_visitor_privacy_page_fails_closed(self) -> None:
        source = hierarchy(node("隐私设置"), node("允许查看"), nav())
        result = parse_profile_visitors(source, InspectionPolicy())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "visitor_page_not_recognized")

    def test_visitors_can_be_read_from_combined_activity_page(self) -> None:
        result = parse_profile_visitors(COMBINED_ACTIVITY, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["display_name"], "访客甲")
        self.assertEqual(result["entries"][0]["time"], "9小时前")

    def test_visitors_ignore_nested_summary_without_actor(self) -> None:
        source = hierarchy(
            node("全部消息"),
            node(description="访客甲，等 10 人近期访问过你的主页，9小时前，按钮"),
            node("等 10 人近期访问过你的主页，9小时前"),
        )
        result = parse_profile_visitors(source, InspectionPolicy())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["display_name"], "访客甲")

    def test_entries_are_bounded_and_fields_truncated(self) -> None:
        rows = [
            node(description=f"访客记录，{'X' * 200}{index}，刚刚")
            for index in range(25)
        ]
        result = parse_profile_visitors(
            hierarchy(node("主页访客"), *rows, nav()),
            InspectionPolicy(max_items_per_section=20),
        )
        self.assertEqual(len(result["entries"]), 20)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["entries"][0]["display_name"]), 160)


class EngagementInspectorTest(unittest.TestCase):
    def inspect(self, device: FakeDevice):
        recorder = Recorder()
        result = EngagementInspector(device, recorder, sleep=lambda _value: None).inspect(
            {"max_items_per_section": 20}
        )
        return result, recorder

    def test_v3_first_screen_read_boundary_completes_without_scrolling(self) -> None:
        activity = v3_page(
            node("互动消息", bounds="[330,40][570,130]"),
            node(
                description="Alice赞了你的作品，2小时前",
                bounds="[30,220][870,370]",
                clickable=True,
            ),
            node("已读", bounds="[410,470][490,530]"),
        )
        device = V3Device([activity])
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            result = EngagementInspector(
                device,
                V2Recorder(Path(directory)),
                sleep=lambda _value: None,
                store=store,
                device_id="device-v3",
                task_id="inspection-v3-1",
            ).inspect(v3_policy())

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["restored"])
        self.assertEqual(result["unified_activity"]["read_boundary"], "first_screen")
        self.assertEqual(result["unified_activity"]["unread_item_count"], 1)
        self.assertEqual(result["unified_activity"]["scroll_count"], 0)
        self.assertNotIn("private_messages", result["sections"])
        self.assertEqual(device.swipes, 0)
        self.assertEqual(device.state, "home")

    def test_v3_scrolls_until_read_boundary_then_stops(self) -> None:
        first = v3_page(
            node("互动消息", bounds="[330,40][570,130]"),
            node(
                description="Alice收藏了你的作品，刚刚",
                bounds="[30,220][870,370]",
                clickable=True,
            ),
        )
        second = v3_page(
            node("互动消息", bounds="[330,40][570,130]"),
            node(
                description="Bob回复了你的评论，1小时前",
                bounds="[30,220][870,370]",
                clickable=True,
            ),
            node("已读", bounds="[410,470][490,530]"),
        )
        device = V3Device([first, second])
        result = EngagementInspector(
            device,
            Recorder(),
            sleep=lambda _value: None,
            device_id="device-v3",
        ).inspect(v3_policy())

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["unified_activity"]["read_boundary"], "after_scroll")
        self.assertEqual(result["unified_activity"]["scroll_count"], 1)
        self.assertEqual(result["unified_activity"]["unread_item_count"], 2)
        self.assertEqual(device.swipes, 1)

    def test_v3_stagnant_list_is_incomplete_and_never_reports_clear(self) -> None:
        activity = v3_page(
            node("互动消息", bounds="[330,40][570,130]"),
            node(
                description="Alice赞了你的作品，刚刚",
                bounds="[30,220][870,370]",
                clickable=True,
            ),
        )
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            result = EngagementInspector(
                V3Device([activity]),
                V2Recorder(Path(directory)),
                sleep=lambda _value: None,
                store=store,
                device_id="device-v3",
                task_id="inspection-v3-incomplete",
                incident_sink=incidents.append,
            ).inspect(v3_policy())
            receipts = store.list_interaction_inspections()

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["unified_activity"]["complete"])
        self.assertEqual(
            result["unified_activity"]["reason_code"],
            "list_boundary_not_confirmed",
        )
        self.assertFalse(result["inspection_metadata"].get("alert_created", False))
        self.assertEqual(receipts["inspections"][0]["result_kind"], "incomplete")
        self.assertTrue(
            any(
                item["context"]["inspection_section"] == "unified_activity"
                for item in incidents
            )
        )

    def test_v3_visitor_disabled_keeps_other_results_but_is_not_full_success(self) -> None:
        activity = v3_page(
            node("互动消息", bounds="[330,40][570,130]"),
            node("访客记录已关闭", bounds="[180,170][720,230]"),
            node(
                description="Alice赞了你的作品，2小时前",
                bounds="[30,260][870,410]",
                clickable=True,
            ),
            node("已读", bounds="[410,500][490,560]"),
        )
        result = EngagementInspector(
            V3Device([activity]),
            Recorder(),
            sleep=lambda _value: None,
            device_id="device-v3",
        ).inspect(v3_policy())

        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["unified_activity"]["complete"])
        self.assertEqual(
            result["unified_activity"]["reason_code"],
            "visitor_history_disabled",
        )
        self.assertEqual(result["sections"]["received_likes"]["count"], 1)
        self.assertEqual(result["sections"]["profile_visitors"]["status"], "unavailable")
        self.assertEqual(
            result["sections"]["profile_visitors"]["reason"],
            "visitor_history_disabled",
        )

    def test_complete_run_uses_only_allowlisted_navigation(self) -> None:
        device = FakeDevice()
        result, recorder = self.inspect(device)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["restored"])
        self.assertEqual(device.state, "home")
        actions = [
            payload["action"]
            for event, payload in recorder.events
            if event == "engagement_navigation"
        ]
        self.assertEqual(
            actions,
            [
                "bottom_tab:消息",
                "list_entry:互动消息",
                "bottom_tab:我",
                "list_entry:主页访客",
                "bottom_tab:首页",
            ],
        )
        self.assertNotIn("回赞", json.dumps(actions, ensure_ascii=False))

    def test_single_section_failure_continues_and_degrades(self) -> None:
        ambiguous_message = hierarchy(
            node("消息"),
            node(description="推荐卡片，小明"),
            node(description="互动消息，按钮", bounds="[40,300][500,520]", clickable=True),
            nav(),
        )
        device = FakeDevice({"message": ambiguous_message})
        result, _recorder = self.inspect(device)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["sections"]["private_messages"]["status"], "failed")
        self.assertEqual(result["sections"]["received_likes"]["status"], "available")
        self.assertEqual(result["sections"]["profile_visitors"]["status"], "available")

    def test_single_section_failure_records_its_own_paired_evidence(self) -> None:
        ambiguous_message = hierarchy(
            node("消息"),
            node(description="推荐卡片，小明"),
            node(description="互动消息，按钮", bounds="[40,300][500,520]", clickable=True),
            nav(),
        )
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = V2Recorder(Path(directory))
            result = EngagementInspector(
                FakeDevice({"message": ambiguous_message}),
                recorder,
                sleep=lambda _value: None,
                incident_sink=incidents.append,
            ).inspect({"max_items_per_section": 20})
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["context"]["inspection_section"], "private_messages")
        self.assertEqual(incidents[0]["error_message"], "private_message_rows_ambiguous")
        self.assertTrue(incidents[0]["screenshot_path"])
        self.assertTrue(incidents[0]["ui_tree_path"])

    def test_multiple_section_failures_keep_separate_incident_contexts(self) -> None:
        ambiguous_message = hierarchy(node("消息"), node(description="推荐卡片，小明"), nav())
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            result = EngagementInspector(
                FakeDevice({"message": ambiguous_message}),
                V2Recorder(Path(directory)),
                sleep=lambda _value: None,
                incident_sink=incidents.append,
            ).inspect({"max_items_per_section": 20})
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(
            {item["context"]["inspection_section"] for item in incidents},
            {"private_messages", "received_likes"},
        )

    def test_interaction_entry_can_be_clickable_without_button_word(self) -> None:
        message = hierarchy(
            node("消息"),
            node(description="私信会话，张三，昨天，你好"),
            node(
                description="互动消息，未读9条消息",
                bounds="[0,300][1080,520]",
                clickable=True,
            ),
            nav(),
        )
        result, _recorder = self.inspect(FakeDevice({"message": message}))
        self.assertEqual(result["sections"]["received_likes"]["status"], "available")

    def test_combined_activity_avoids_hidden_profile_navigation(self) -> None:
        device = FakeDevice({"likes": COMBINED_ACTIVITY})
        result, recorder = self.inspect(device)
        actions = [
            payload["action"]
            for event, payload in recorder.events
            if event == "engagement_navigation"
        ]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sections"]["profile_visitors"]["count"], 1)
        self.assertNotIn("bottom_tab:我", actions)
        self.assertNotIn("list_entry:主页访客", actions)
        self.assertNotIn("bottom_tab:首页", actions)
        self.assertEqual(device.state, "home")

    def test_missing_visitor_entry_is_unavailable_and_not_clicked(self) -> None:
        profile = hierarchy(node("我的主页"), nav())
        device = FakeDevice({"profile": profile})
        result, _recorder = self.inspect(device)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(
            result["sections"]["profile_visitors"]["status"], "unavailable"
        )
        profile_clicks = [click for click in device.clicks if click[0] == "profile"]
        self.assertEqual(profile_clicks[0][2], 2300)

    def test_explicitly_unavailable_visitor_entry_is_not_an_incident(self) -> None:
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            result = EngagementInspector(
                FakeDevice({"profile": hierarchy(node("我的主页"), nav())}),
                V2Recorder(Path(directory)),
                sleep=lambda _value: None,
                incident_sink=incidents.append,
            ).inspect({"max_items_per_section": 20})
        self.assertEqual(result["sections"]["profile_visitors"]["status"], "unavailable")
        self.assertEqual(incidents, [])

    def test_grey_visitor_entry_is_unavailable_and_not_clicked(self) -> None:
        profile = hierarchy(
            node("我的主页"),
            node(description="主页访客，按钮", bounds="[40,300][500,520]", clickable=False),
            nav(),
        )
        device = FakeDevice({"profile": profile})
        result, _recorder = self.inspect(device)
        self.assertEqual(
            result["sections"]["profile_visitors"]["status"], "unavailable"
        )
        self.assertFalse(any(state == "profile" and y < 1000 for state, _x, y in device.clicks))

    def test_cold_start_recovers_before_inspection(self) -> None:
        device = FakeDevice(foreground=False)
        result, _recorder = self.inspect(device)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(device.app_starts, 1)

    def test_conversation_overlay_is_not_mistaken_for_home(self) -> None:
        conversation = hierarchy(
            node("推荐"),
            node(description="发送消息"),
            nav(),
        )
        device = FakeDevice({"conversation": conversation})
        device.state = "conversation"
        result, _recorder = self.inspect(device)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(device.clicks[0][0], "home")

    def test_deep_settings_recovery_waits_for_delayed_home(self) -> None:
        privacy = hierarchy(node("隐私设置"))

        class DelayedHomeDevice(FakeDevice):
            def __init__(self) -> None:
                super().__init__({"privacy": privacy})
                self.state = "privacy"
                self.startup_dumps_remaining = 0

            def dump_hierarchy(self, compressed=True, pretty=False):
                if self.state == "starting":
                    if self.startup_dumps_remaining:
                        self.startup_dumps_remaining -= 1
                        return privacy
                    self.state = "home"
                return super().dump_hierarchy(compressed=compressed, pretty=pretty)

            def press(self, key: str) -> None:
                if key != "back":
                    raise AssertionError(key)

            def app_start(self, package: str, stop=False, wait=True) -> None:
                self.app_starts += 1
                self.foreground = package
                self.state = "starting"
                self.startup_dumps_remaining = 1

        result, _recorder = self.inspect(DelayedHomeDevice())
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["restored"])
        self.assertEqual(
            {name: section["status"] for name, section in result["sections"].items()},
            {
                "private_messages": "available",
                "received_likes": "available",
                "profile_visitors": "available",
            },
        )

    def test_home_tab_recovery_waits_without_reclicking(self) -> None:
        class DelayedHomeTabDevice(FakeDevice):
            def __init__(self) -> None:
                super().__init__()
                self.state = "message"
                self.home_pending_dumps = 0

            def dump_hierarchy(self, compressed=True, pretty=False):
                if self.home_pending_dumps:
                    self.home_pending_dumps -= 1
                    if not self.home_pending_dumps:
                        self.state = "home"
                    else:
                        return self.pages["message"]
                return super().dump_hierarchy(compressed=compressed, pretty=pretty)

            def click(self, x: int, y: int) -> None:
                if self.state == "message" and y >= 2200 and x < 300:
                    self.clicks.append((self.state, x, y))
                    self.home_pending_dumps = 3
                    return
                super().click(x, y)

        result, _recorder = self.inspect(DelayedHomeTabDevice())
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["restored"])

    def test_login_page_returns_failed_without_unknown_clicks(self) -> None:
        unknown_page = hierarchy(node("隐私设置"))
        device = FakeDevice({"home": unknown_page})
        result, _recorder = self.inspect(device)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(device.clicks, [])

    def test_restore_failure_returns_failed(self) -> None:
        class StuckDevice(FakeDevice):
            def click(self, x: int, y: int) -> None:
                if self.state == "visitors" and y >= 2200 and x < 300:
                    self.clicks.append((self.state, x, y))
                    return
                super().click(x, y)

            def press(self, key: str) -> None:
                return None

            def app_start(self, package: str, stop=False, wait=True) -> None:
                self.app_starts += 1
                self.foreground = package

        result, _recorder = self.inspect(StuckDevice())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["restored"])

    def test_restore_failure_records_recovery_incident(self) -> None:
        class StuckDevice(FakeDevice):
            def click(self, x: int, y: int) -> None:
                if self.state == "visitors" and y >= 2200 and x < 300:
                    self.clicks.append((self.state, x, y))
                    return
                super().click(x, y)

            def press(self, key: str) -> None:
                return None

            def app_start(self, package: str, stop=False, wait=True) -> None:
                self.app_starts += 1
                self.foreground = package

        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            result = EngagementInspector(
                StuckDevice(),
                V2Recorder(Path(directory)),
                sleep=lambda _value: None,
                incident_sink=incidents.append,
            ).inspect({"max_items_per_section": 20})
        self.assertEqual(result["status"], "failed")
        restore = next(item for item in incidents if item["stage"] == "engagement_restore")
        self.assertEqual(restore["error_message"], "home_restore_failed")
        self.assertEqual(restore["context"]["inspection_section"], "restore")

    def test_result_contains_no_absolute_paths_and_no_model_events(self) -> None:
        result, recorder = self.inspect(FakeDevice())
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("C:\\\\", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertFalse(any("model" in event for event, _payload in recorder.events))
        self.assertLessEqual(len(recorder.events), 12)

    def test_execute_task_dispatches_inspector_and_records_result(self) -> None:
        task = SimpleNamespace(
            id="task-1",
            task_type="douyin_engagement_inspection",
            payload={"max_items_per_section": 20},
        )
        recorder = Recorder()
        expected = {
            "status": "degraded",
            "task_type": "douyin_engagement_inspection",
            "restored": True,
            "failure_reason": None,
            "side_effect_notice": "notice",
            "sections": {},
            "evidence": [],
        }
        with patch("execution_tasks.EngagementInspector") as inspector:
            inspector.return_value.inspect.return_value = expected
            result = execute_task(object(), task, recorder)
        self.assertEqual(result["task_id"], "task-1")
        inspector.return_value.inspect.assert_called_once_with(task.payload)
        self.assertEqual(recorder.events[-1][0], "task_complete")

    def test_fatal_inspection_records_paired_incident_evidence_before_restore(self) -> None:
        unknown_page = hierarchy(node("隐私设置"))
        device = FakeDevice({"home": unknown_page})
        task = SimpleNamespace(
            id="inspection-task-1",
            device_id="device-1",
            task_type="douyin_engagement_inspection",
            payload={"max_items_per_section": 20},
        )
        incidents: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            recorder = V2Recorder(Path(directory))
            result = execute_task(
                device,
                task,
                recorder,
                incident_sink=incidents.append,
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_reason"], "main_feed_not_ready")
            self.assertEqual(len(incidents), 2)
            incident = next(
                item for item in incidents if item["stage"] == "engagement_navigation"
            )
            self.assertEqual(incident["stage"], "engagement_navigation")
            self.assertEqual(incident["error_message"], "main_feed_not_ready")
            self.assertEqual(incident["context"]["workflow_version"], "v1")
            self.assertEqual(incident["context"]["inspection_section"], "navigation")
            self.assertTrue(Path(incident["screenshot_path"]).is_file())
            self.assertTrue(Path(incident["ui_tree_path"]).is_file())
            self.assertIn("隐私设置", Path(incident["ui_tree_path"]).read_text(encoding="utf-8"))

    def test_v2_runs_calibrated_path_and_keeps_deduplicated_local_evidence(self) -> None:
        def page(*items: str) -> str:
            return hierarchy(*items, nav())

        message = page(node("消息"), node(description="互动消息，按钮", bounds="[0,480][1080,700]", clickable=True))
        aggregate = page(node(description="全部消息，按钮", bounds="[400,100][680,260]", clickable=True))
        menu = page(
            node(description="赞与收藏，按钮", bounds="[0,425][1080,569]", clickable=True),
            node(description="收到的评论，按钮", bounds="[0,713][1080,857]", clickable=True),
            node(description="收到的弹幕，按钮", bounds="[0,1001][1080,1145]", clickable=True),
        )
        likes = page(node(description="赞与收藏，按钮", bounds="[400,100][680,260]", clickable=True), node("暂时没有更多了"))
        comments = page(node(description="收到的评论，按钮", bounds="[400,100][680,260]", clickable=True), node("暂时没有更多了"))
        danmaku = page(node(description="收到的弹幕，按钮", bounds="[400,100][680,260]", clickable=True), node("你还没有收到弹幕"))
        profile = page(node("我的主页"), node(description="主页访客，按钮", bounds="[670,130][770,230]", clickable=True))
        visitors = page(
            node("主页访客"),
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/root_layout" bounds="[0,368][1080,632]" />',
            f'<node package="{DOUYIN_PACKAGE}" visible-to-user="true" resource-id="com.ss.android.ugc.aweme:id/3h6" text="synthetic-row" bounds="[120,390][620,470]" />',
        )

        class V2Device(FakeDevice):
            riskflow_display_signature = "1080x2400x480x0x100"
            info = {"displayRotation": 0}

            def __init__(self) -> None:
                super().__init__({"home": page(node("推荐")), "message": message, "aggregate": aggregate, "menu": menu, "likes": likes, "comments": comments, "danmaku": danmaku, "profile": profile, "visitors": visitors})

            def app_info(self, _package):
                return {"versionName": "33.0.0"}

            def click(self, x: int, y: int) -> None:
                self.clicks.append((self.state, x, y))
                if y >= 2200 and x < 300: self.state = "home"
                elif y >= 2200 and 540 <= x <= 820: self.state = "message"
                elif y >= 2200 and x > 820: self.state = "profile"
                elif self.state == "message": self.state = "aggregate"
                elif self.state in {"aggregate", "likes", "comments", "danmaku"} and y < 300: self.state = "menu"
                elif self.state == "menu" and y < 650: self.state = "likes"
                elif self.state == "menu" and y < 900: self.state = "comments"
                elif self.state == "menu": self.state = "danmaku"
                elif self.state == "profile": self.state = "visitors"
                else: raise AssertionError((self.state, x, y))

            def swipe(self, *_args, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "tasks.db")
            recorder = V2Recorder(root)
            result = EngagementInspector(
                V2Device(), recorder, sleep=lambda _value: None,
                store=store, device_id="device-1", task_id="task-1",
            ).inspect({
                "inspection_workflow_version": "v2",
                "expected_app_version": "33.0.0",
                "expected_display_signature": "1080x2400x480x0x100",
                "inspection_calibration": {
                    "profile_version": "synthetic-v2-r1",
                    "device_id": "device-1",
                    "app_version": "33.0.0",
                    "display_signature": "1080x2400x480x0x100",
                    "passes": 3,
                    "later_passes_semantically_equal": True,
                    "controls": {
                        "aggregate": ["互动消息"],
                        "filter_titles": [
                            "全部消息", "赞与收藏", "收到的评论", "收到的弹幕"
                        ],
                        "received_likes": ["赞与收藏"],
                        "received_comments": ["收到的评论"],
                        "received_danmaku": ["收到的弹幕"],
                        "visitor": ["主页访客"],
                    },
                    "sections": {
                        "private_messages": True,
                        "received_likes": True,
                        "received_comments": True,
                        "received_danmaku": True,
                        "profile_visitors": True,
                    },
                },
                "max_items_per_section": 20,
            })
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["restored"])
            self.assertEqual(result["sections"]["comment_danmaku"]["status"], "available")
            self.assertEqual(result["sections"]["profile_visitors"]["baseline_status"], "baseline_created")
            self.assertEqual(
                result["sections"]["profile_visitors"]["entries"][0]["display_name"],
                "synthetic-row",
            )
            self.assertEqual(len(list(root.glob("inspection-v2-*.png"))), 1)
            self.assertEqual(len(list(root.glob("inspection-v2-*.xml.gz"))), 1)
            self.assertIsNotNone(store.get_visitor_baseline("device-1"))
            receipts = store.list_interaction_inspections()
            self.assertEqual(receipts["total"], 1)
            self.assertEqual(receipts["inspections"][0]["result_kind"], "clear")
            self.assertGreater(receipts["inspections"][0]["summary"]["evidence_count"], 0)
            self.assertEqual(
                receipts["inspections"][0]["summary"]["sections"]["profile_visitors"]["items"][0]["display_name"],
                "synthetic-row",
            )


if __name__ == "__main__":
    unittest.main()
