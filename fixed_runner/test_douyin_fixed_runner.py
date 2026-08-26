from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from douyin_fixed_runner import (  # noqa: E402
    FixedDouyinRunner,
    PROFILE,
    RunRecorder,
    comment_panel_visible,
    favorite_active,
    like_active,
    main_feed_visible,
)


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"


class VisualChecksTest(unittest.TestCase):
    def load(self, name: str) -> Image.Image:
        return Image.open(WORK / name).convert("RGB")

    def test_verified_like_is_active(self) -> None:
        self.assertTrue(like_active(self.load("autoglm-handoff-like-verify.png")))

    def test_verified_favorite_is_active(self) -> None:
        self.assertTrue(
            favorite_active(self.load("autoglm-handoff-favorite-verify.png"))
        )

    def test_comment_panel_is_detected(self) -> None:
        image = self.load("autoglm-handoff-comment-verify.png")
        self.assertTrue(comment_panel_visible(image))
        self.assertFalse(main_feed_visible(image))

    def test_main_feed_is_detected(self) -> None:
        self.assertTrue(main_feed_visible(self.load("autoglm-handoff-video4.png")))


class LayoutProfileTest(unittest.TestCase):
    def test_calibrated_mumu_layout_is_supported(self) -> None:
        runner = FixedDouyinRunner(None, None, PROFILE)

        runner.ensure_profile(Image.new("RGB", (900, 1600), "black"))

        self.assertEqual((runner.profile.width, runner.profile.height), (900, 1600))

    def test_same_aspect_ratio_device_is_scaled_to_its_real_resolution(self) -> None:
        runner = FixedDouyinRunner(None, None, PROFILE)

        runner.ensure_profile(Image.new("RGB", (1264, 2800), "black"))

        self.assertEqual((runner.profile.width, runner.profile.height), (1264, 2800))
        self.assertEqual(runner.profile.absolute(runner.profile.swipe_start), (632, 2100))

    def test_materially_different_aspect_ratio_still_requires_recalibration(self) -> None:
        runner = FixedDouyinRunner(None, None, PROFILE)

        with self.assertRaisesRegex(RuntimeError, "Recalibrate first"):
            runner.ensure_profile(Image.new("RGB", (1080, 1920), "black"))


class RunRecorderTest(unittest.TestCase):
    def test_tcp_device_id_is_sanitized_for_windows_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = RunRecorder(Path(directory), "127.0.0.1:16448")

            self.assertNotIn(":", recorder.run_dir.name)
            self.assertTrue(recorder.run_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
