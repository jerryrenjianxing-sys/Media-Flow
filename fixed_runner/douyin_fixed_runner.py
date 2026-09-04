from __future__ import annotations

import argparse
import base64
import io
import json
import msvcrt
import os
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from runtime_layout import RUNTIME_ROOT


DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
SUPPORTED_LAYOUT_SIZES = {(1080, 2400), (900, 1600)}


@dataclass(frozen=True)
class LayoutProfile:
    width: int = 1080
    height: int = 2400
    like_xy: tuple[float, float] = (0.914, 0.456)
    comment_xy: tuple[float, float] = (0.914, 0.539)
    favorite_xy: tuple[float, float] = (0.914, 0.661)
    swipe_start: tuple[float, float] = (0.50, 0.75)
    swipe_end: tuple[float, float] = (0.50, 0.22)

    def absolute(self, point: tuple[float, float]) -> tuple[int, int]:
        return round(point[0] * self.width), round(point[1] * self.height)

    def for_size(self, width: int, height: int) -> "LayoutProfile":
        if width <= 0 or height <= 0:
            raise ValueError("Screenshot dimensions must be positive")
        if (width, height) in SUPPORTED_LAYOUT_SIZES:
            return replace(self, width=width, height=height)
        expected_aspect = self.width / self.height
        actual_aspect = width / height
        aspect_drift = abs(actual_aspect - expected_aspect) / expected_aspect
        if aspect_drift > 0.02:
            raise ValueError(
                f"Screenshot aspect ratio drift {aspect_drift:.1%} exceeds 2%"
            )
        return replace(self, width=width, height=height)


PROFILE = LayoutProfile()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def decode_screenshot(payload: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")


def crop_relative(
    image: Image.Image, box: tuple[float, float, float, float]
) -> Image.Image:
    width, height = image.size
    return image.crop(
        (
            round(box[0] * width),
            round(box[1] * height),
            round(box[2] * width),
            round(box[3] * height),
        )
    )


def color_ratio(image: Image.Image, color: str) -> float:
    matched = 0
    total = image.width * image.height
    for red, green, blue in image.get_flattened_data():
        if color == "red":
            ok = red > 180 and red > green * 1.45 and red > blue * 1.45
        elif color == "yellow":
            ok = red > 180 and green > 110 and blue < 130 and red > green * 1.05
        else:
            raise ValueError(f"Unsupported color: {color}")
        matched += int(ok)
    return matched / total


def mean_brightness(image: Image.Image) -> float:
    total = 0
    count = image.width * image.height
    for red, green, blue in image.get_flattened_data():
        total += (red + green + blue) / 3
    return total / count


def like_active(image: Image.Image) -> bool:
    patch = crop_relative(image, (0.855, 0.42, 0.985, 0.49))
    return color_ratio(patch, "red") >= 0.035


def favorite_active(image: Image.Image) -> bool:
    patch = crop_relative(image, (0.855, 0.63, 0.985, 0.71))
    return color_ratio(patch, "yellow") >= 0.025


def main_feed_visible(image: Image.Image) -> bool:
    # The Douyin main feed has a dark bottom-navigation band. Comment/editor
    # panels replace this area with a light sheet, so this is a conservative
    # same-device precondition rather than a general visual classifier.
    bottom_navigation = crop_relative(image, (0.05, 0.865, 0.95, 0.935))
    return mean_brightness(bottom_navigation) < 95


def comment_panel_visible(image: Image.Image) -> bool:
    panel = crop_relative(image, (0.05, 0.67, 0.95, 0.90))
    return mean_brightness(panel) > 150


class DeviceLock(AbstractContextManager["DeviceLock"]):
    def __init__(self, root: Path, device_id: str) -> None:
        safe_device = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in device_id)
        self.path = root / "locks" / f"{safe_device}.lock"
        self.handle = None

    def __enter__(self) -> "DeviceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.handle.seek(0, os.SEEK_END) == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(
                f"Device is already locked: {self.path}. "
                "Another worker or runner is active."
            ) from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\nstarted={now_iso()}\n".encode("utf-8"))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.handle is not None:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None


class RunRecorder:
    def __init__(self, root: Path, device_id: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_device = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in device_id
        )
        self.run_dir = root / "runs" / f"{stamp}-{safe_device}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.log_path = self.run_dir / "events.jsonl"

    def emit(self, event: str, **data: Any) -> None:
        payload = {"time": now_iso(), "event": event, **data}
        line = json.dumps(payload, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        print(json.dumps(payload, ensure_ascii=True), flush=True)

    def screenshot(self, device, name: str) -> Image.Image:
        image = decode_screenshot(device.screenshot())
        path = self.run_dir / f"{name}.png"
        image.save(path)
        self.emit("screenshot", name=name, path=str(path), size=list(image.size))
        return image


class FixedDouyinRunner:
    def __init__(self, device, recorder: RunRecorder, profile: LayoutProfile) -> None:
        self.device = device
        self.recorder = recorder
        self.profile = profile

    def ensure_profile(self, image: Image.Image) -> None:
        if image.size == (self.profile.width, self.profile.height):
            return
        try:
            self.profile = self.profile.for_size(*image.size)
        except ValueError as exc:
            raise RuntimeError(
                f"Unsupported screenshot size {image.size}; expected "
                f"the same layout ratio as {(self.profile.width, self.profile.height)}. "
                "Recalibrate first."
            ) from exc

    def require_main_feed(self, image: Image.Image, stage: str) -> None:
        if not main_feed_visible(image):
            raise RuntimeError(f"Main feed precondition failed before {stage}")

    def tap(self, point: tuple[float, float], action: str) -> None:
        x, y = self.profile.absolute(point)
        self.recorder.emit("tap", action=action, x=x, y=y)
        self.device.tap(x, y)

    def swipe_next(self, from_video: int, to_video: int) -> None:
        start_x, start_y = self.profile.absolute(self.profile.swipe_start)
        end_x, end_y = self.profile.absolute(self.profile.swipe_end)
        started = time.monotonic()
        self.device.swipe(start_x, start_y, end_x, end_y, 350)
        time.sleep(0.7)
        self.recorder.emit(
            "swipe",
            from_video=from_video,
            to_video=to_video,
            elapsed_s=round(time.monotonic() - started, 3),
        )

    def watch(self, video: int, seconds: float) -> None:
        started = time.monotonic()
        time.sleep(seconds)
        self.recorder.emit(
            "watch",
            video=video,
            planned_s=seconds,
            actual_s=round(time.monotonic() - started, 3),
        )

    def like(self, video: int) -> None:
        before = self.recorder.screenshot(self.device, f"video-{video}-like-before")
        self.ensure_profile(before)
        self.require_main_feed(before, "like")
        before_active = like_active(before)
        self.recorder.emit("like_state_before", video=video, active=before_active)
        if before_active:
            raise RuntimeError("Like target is already active or visually ambiguous")
        self.tap(self.profile.like_xy, "like")
        time.sleep(0.8)
        after = self.recorder.screenshot(self.device, f"video-{video}-like-after")
        after_active = like_active(after)
        self.recorder.emit("like_state_after", video=video, active=after_active)
        if not after_active:
            raise RuntimeError("Like verification failed; stopping without further mutations")

    def favorite(self, video: int) -> None:
        before = self.recorder.screenshot(self.device, f"video-{video}-favorite-before")
        self.ensure_profile(before)
        self.require_main_feed(before, "favorite")
        before_active = favorite_active(before)
        self.recorder.emit("favorite_state_before", video=video, active=before_active)
        if before_active:
            raise RuntimeError("Favorite target is already active or visually ambiguous")
        self.tap(self.profile.favorite_xy, "favorite")
        time.sleep(0.8)
        after = self.recorder.screenshot(self.device, f"video-{video}-favorite-after")
        after_active = favorite_active(after)
        self.recorder.emit("favorite_state_after", video=video, active=after_active)
        if not after_active:
            raise RuntimeError("Favorite verification failed; stopping without further mutations")

    def open_and_close_comments(self, video: int) -> None:
        before = self.recorder.screenshot(self.device, f"video-{video}-comments-before")
        self.ensure_profile(before)
        self.require_main_feed(before, "open_comments")
        self.tap(self.profile.comment_xy, "open_comments")
        time.sleep(1.0)
        opened = self.recorder.screenshot(self.device, f"video-{video}-comments-open")
        visible = comment_panel_visible(opened)
        self.recorder.emit("comment_panel_state", video=video, visible=visible)
        if not visible:
            raise RuntimeError("Comment panel verification failed")
        self.device.key("back")
        time.sleep(0.7)
        closed = self.recorder.screenshot(self.device, f"video-{video}-comments-closed")
        if not main_feed_visible(closed):
            raise RuntimeError("Comment panel did not close cleanly")

    def run(self, dwell: list[float]) -> dict[str, Any]:
        if len(dwell) != 4:
            raise ValueError("Exactly four dwell values are required")
        wall_started = time.monotonic()
        self.device.start_app(DOUYIN_PACKAGE)
        time.sleep(1.5)
        initial = self.recorder.screenshot(self.device, "initial")
        self.ensure_profile(initial)
        self.require_main_feed(initial, "initial swipe")

        # Start from a new item so prior manual/AutoGLM state is not reused.
        self.swipe_next(0, 1)

        actions = {2: self.like, 3: self.favorite, 4: self.open_and_close_comments}
        for video, seconds in enumerate(dwell, start=1):
            self.watch(video, seconds)
            action = actions.get(video)
            if action is not None:
                action(video)
            if video < 4:
                self.swipe_next(video, video + 1)

        wall_s = round(time.monotonic() - wall_started, 3)
        planned_s = round(sum(dwell), 3)
        summary = {
            "status": "passed",
            "planned_dwell_s": planned_s,
            "wall_s": wall_s,
            "control_and_verification_overhead_s": round(wall_s - planned_s, 3),
        }
        self.recorder.emit("run_complete", **summary)
        return summary


def parse_dwell(value: str) -> list[float]:
    values = [float(part.strip()) for part in value.split(",")]
    if len(values) != 4 or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("Use four non-negative seconds: 4,5,4,5")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Fixed Douyin internal-test benchmark")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--dwell", type=parse_dwell, default=parse_dwell("4,5,4,5"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "artifacts",
    )
    args = parser.parse_args()

    recorder = RunRecorder(args.output_root, args.device_id)
    try:
        with DeviceLock(RUNTIME_ROOT, args.device_id):
            recorder.emit("run_start", device_id=args.device_id, dwell=args.dwell)
            from mobilerun_core import Mobilerun

            device = Mobilerun().connect(args.device_id, backend="local-android-adb")
            FixedDouyinRunner(device, recorder, PROFILE).run(args.dwell)
        return 0
    except Exception as exc:
        recorder.emit("run_failed", error_type=type(exc).__name__, error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
