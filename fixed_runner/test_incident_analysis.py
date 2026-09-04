from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from incident_analysis import build_incident_analysis_payload, parse_incident_advice
from task_store import TaskStore


class IncidentAnalysisTests(unittest.TestCase):
    def test_valid_advice_is_read_only(self) -> None:
        advice = parse_incident_advice(
            '{"classification":"overlay","summary":"出现遮罩弹窗",'
            '"suggested_rule":"增加弹窗语义识别后交给固定恢复状态机",'
            '"confidence":0.92,"risk":"medium"}'
        )
        self.assertEqual(advice.classification, "overlay")
        self.assertFalse(advice.auto_applicable)

    def test_coordinate_advice_is_rejected(self) -> None:
        advice = parse_incident_advice(
            '{"classification":"overlay","summary":"弹窗",'
            '"suggested_rule":"点击坐标 (100, 200)",'
            '"confidence":0.99,"risk":"low"}'
        )
        self.assertEqual(advice.classification, "unknown")
        self.assertEqual(advice.risk, "high")
        self.assertNotIn("100", advice.suggested_rule)

    def test_openrouter_payload_contains_no_device_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=1,
                stage="comment",
                error_type="RuntimeError",
                error_message="input missing",
                outcome="skipped",
                recovery_action="continue",
            )
            payload = build_incident_analysis_payload(
                store.get_incident(incident_id),
                model="google/gemini-3.1-flash-lite",
                base_url="https://openrouter.ai/api/v1",
                fallback_models=("openai/gpt-4.1-nano",),
            )
        self.assertEqual(payload["models"][0], "google/gemini-3.1-flash-lite")
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        system = payload["messages"][0]["content"]
        self.assertIn("do not control the phone", system)
        self.assertIn("Do not output screen coordinates", system)

    def test_glm_flash_payload_uses_supported_json_object_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory) / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=1,
                stage="topic_model",
                error_type="RuntimeError",
                error_message="model failed",
                outcome="model_failed",
                recovery_action="none_model_channel",
            )
            payload = build_incident_analysis_payload(
                store.get_incident(incident_id),
                model="z-ai/glm-5.3-flash",
                base_url="https://openrouter.ai/api/v1",
            )
        self.assertEqual(payload["models"], ["z-ai/glm-5.3-flash"])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertFalse(payload["provider"]["require_parameters"])
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": True})
        self.assertEqual(payload["max_tokens"], 1600)
        self.assertNotIn("temperature", payload)

    def test_payload_includes_bounded_ui_semantics_without_raw_message_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ui_tree = root / "incident.xml"
            ui_tree.write_text(
                '<hierarchy><node text="消息" clickable="true" />'
                '<node text="敏感昵称和私信正文" class="android.widget.TextView" /></hierarchy>',
                encoding="utf-8",
            )
            store = TaskStore(root / "tasks.db")
            task_id = store.submit("healthcheck", "device-1")
            incident_id = store.record_incident(
                task_id=task_id,
                device_id="device-1",
                video_index=None,
                stage="engagement_section",
                error_type="EngagementSectionError",
                error_message="private_message_rows_ambiguous",
                outcome="skipped",
                recovery_action="calibrate_engagement_section",
                ui_tree_path=str(ui_tree),
            )
            payload = build_incident_analysis_payload(
                store.get_incident(incident_id),
                model="google/gemini-3.1-flash-lite",
                base_url="https://openrouter.ai/api/v1",
            )
        metadata_text = payload["messages"][1]["content"][0]["text"]
        metadata = json.loads(metadata_text.split("\n", 1)[1])
        self.assertEqual(metadata["ui_semantics"]["known_markers"], ["消息"])
        self.assertEqual(metadata["ui_semantics"]["clickable_count"], 1)
        self.assertNotIn("敏感昵称", metadata_text)


if __name__ == "__main__":
    unittest.main()
