from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import uiautomator2 as u2

from device_profiles import load_device_profiles
from douyin_fixed_runner import PROFILE, RUNTIME_ROOT
from douyin_uia2_runner import Uia2DouyinRunner, Uia2RunRecorder
from profile_devices import online_device_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="No-engagement device recovery/search preview")
    parser.add_argument("--device", default="")
    parser.add_argument("--search-device", default="")
    parser.add_argument("--query", default="")
    args = parser.parse_args()

    profiles = load_device_profiles()
    results = []
    online = online_device_ids()
    selected = [args.device] if args.device and args.device in online else online
    for device_id in selected:
        profile = profiles.get(device_id)
        recorder = Uia2RunRecorder(RUNTIME_ROOT / "calibration", device_id)
        row = {
            "device_id": device_id,
            "friendly_name": profile.friendly_name if profile else device_id,
            "profile_verified": bool(profile and profile.verified),
            "home_recovery": False,
            "search_preview": "not_requested",
        }
        try:
            device = u2.connect(device_id)
            runner = Uia2DouyinRunner(
                device, recorder, PROFILE, max_gate_skips=3, device_id=device_id
            )
            runner.ensure_app_ready()
            row["home_recovery"] = runner.recover_main_feed("calibration-preview")
            recorder.screenshot(device, "calibration-feed-confirmed")
            if device_id == args.search_device and args.query:
                try:
                    runner.enter_topic_search(args.query)
                    row["search_preview"] = "entered_video"
                finally:
                    row["search_returned_home"] = runner.recover_main_feed(
                        "search-preview-complete"
                    )
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["artifact_dir"] = str(recorder.run_dir)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "side_effects": {"likes": 0, "favorites": 0, "comments": 0, "posts": 0},
        "results": results,
    }
    output = RUNTIME_ROOT / "calibration" / "latest-device-calibration.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
