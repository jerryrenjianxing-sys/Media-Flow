from __future__ import annotations

import unittest
from pathlib import Path

from recovery_rules import (
    MINOR_MODE_RULE,
    find_action_bounds,
    match_verified_recovery_rule,
)


PACKAGE = "com.ss.android.ugc.aweme"


def node(*, text="", description="", bounds="[0,0][100,100]", clickable="false") -> str:
    return (
        f'<node text="{text}" content-desc="{description}" bounds="{bounds}" '
        f'package="{PACKAGE}" visible-to-user="true" clickable="{clickable}" />'
    )


class VerifiedRecoveryRuleTest(unittest.TestCase):
    def test_matches_minor_mode_but_not_incidental_minor_text(self) -> None:
        known = "<hierarchy>" + node(text="未成年人模式") + node(text="开启未成年人模式") + "</hierarchy>"
        incidental = "<hierarchy>" + node(text="新闻：保护未成年人") + "</hierarchy>"

        self.assertEqual(match_verified_recovery_rule(known), MINOR_MODE_RULE)
        self.assertIsNone(match_verified_recovery_rule(incidental))

    def test_prefers_close_and_supports_do_not_remind_fallback(self) -> None:
        source = (
            Path(__file__).resolve().parent
            / "test_fixtures"
            / "minor_mode_overlay.xml"
        ).read_text(encoding="utf-8")
        close, fallback = MINOR_MODE_RULE.actions

        self.assertEqual(find_action_bounds(source, close.labels, 1080, 2340), (960, 1427, 984, 1511))
        self.assertEqual(find_action_bounds(source, fallback.labels, 1080, 2340), (48, 2016, 984, 2148))

    def test_unknown_dialog_has_no_verified_rule(self) -> None:
        source = "<hierarchy>" + node(text="限时活动") + node(description="关闭", clickable="true") + "</hierarchy>"
        self.assertIsNone(match_verified_recovery_rule(source))


if __name__ == "__main__":
    unittest.main()
