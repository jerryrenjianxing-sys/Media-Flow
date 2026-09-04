from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from douyin_adapter import DouyinAdapter  # noqa: E402
from douyin_uia2_runner import classify_douyin_page_source  # noqa: E402


PACKAGE = "com.ss.android.ugc.aweme"
FIXTURES = Path(__file__).resolve().parent / "test_fixtures"


def _home_video_source() -> str:
    return (
        '<hierarchy><node package="com.ss.android.ugc.aweme" '
        'visible-to-user="true" bounds="[0,0][720,1600]">'
        '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
        'content-desc="视频" bounds="[0,0][720,1450]" />'
        '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
        'text="首页" content-desc="首页" clickable="true" bounds="[0,1414][142,1510]" />'
        '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
        'content-desc="未点赞，喜欢12，按钮" bounds="[610,700][710,820]" />'
        '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
        'content-desc="评论3，按钮" bounds="[610,850][710,970]" />'
        '<node package="com.ss.android.ugc.aweme" visible-to-user="true" '
        'content-desc="未选中，收藏4，按钮" bounds="[610,1000][710,1120]" />'
        '</node></hierarchy>'
    )


class DouyinAdapterCalibrationTest(unittest.TestCase):
    def test_calibration_advances_past_home_image_note_before_reading_controls(self) -> None:
        image_note = (FIXTURES / "home_image_note_douyin3.xml").read_text(
            encoding="utf-8"
        )
        video_source = _home_video_source()
        self.assertEqual(
            classify_douyin_page_source(image_note, PACKAGE, 720, 1600),
            "home_image_note",
        )

        with tempfile.TemporaryDirectory() as directory:
            device = Mock()
            device.window_size.return_value = (720, 1600)
            device.app_current.return_value = {"package": PACKAGE}
            device.dump_hierarchy.return_value = video_source
            recorder = Mock()
            recorder.run_dir = Path(directory)
            recorder.screenshot.return_value = Image.new("RGB", (720, 1600), "black")
            adapter = DouyinAdapter(device, recorder, device_id="device-1")
            adapter.runner = Mock()

            image, source, _source_path = adapter._seek_home_video_for_calibration(
                Image.new("RGB", (720, 1600), "black"),
                image_note,
                warnings=[],
                evidence=[],
            )

            adapter.runner.swipe_next.assert_called_once_with(0, 1)
            self.assertEqual(image.size, (720, 1600))
            self.assertEqual(
                classify_douyin_page_source(source, PACKAGE, 720, 1600),
                "home_feed",
            )


if __name__ == "__main__":
    unittest.main()
