from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

from comment_ai import (  # noqa: E402
    build_request_payload,
    parse_comment_decision,
    parse_streaming_response,
    parse_topic_decision,
)


class CommentDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.image_path = Path(__file__).with_name("test-comment-image.jpg")
        Image.new("RGB", (16, 16), "white").save(self.image_path)

    def tearDown(self) -> None:
        self.image_path.unlink(missing_ok=True)

    def test_topic_decision_requires_safe_content(self) -> None:
        decision = parse_topic_decision(
            '{"matches":true,"topic":"职场沟通","reason":"画面匹配",'
            '"confidence":0.91,"safe":false}'
        )
        self.assertFalse(decision.matches)
        self.assertFalse(decision.safe)

    def test_openrouter_payload_uses_portable_fields(self) -> None:
        payload = build_request_payload(
            self.image_path,
            model="google/gemini-3.1-flash-lite",
            base_url="https://openrouter.ai/api/v1",
            fallback_models=("openai/gpt-4.1-nano",),
        )
        self.assertNotIn("model", payload)
        self.assertEqual(
            payload["models"],
            ["google/gemini-3.1-flash-lite", "openai/gpt-4.1-nano"],
        )
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(
            payload["provider"],
            {
                "allow_fallbacks": True,
                "require_parameters": True,
                "data_collection": "deny",
            },
        )
        content = payload["messages"][1]["content"]
        self.assertEqual([item["type"] for item in content], ["text", "image_url"])

    def test_zai_payload_keeps_provider_specific_thinking_switch(self) -> None:
        payload = build_request_payload(
            self.image_path,
            model="glm-4.6v-flash",
            base_url="https://api.z.ai/api/paas/v4",
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("provider", payload)

    def test_streaming_response_is_combined(self) -> None:
        lines = [
            b'data: {"choices":[{"delta":{"content":"{\\"decision\\":\\""}}]}\n',
            b'data: {"choices":[{"delta":{"content":"skip\\"}"}}]}\n',
            b"data: [DONE]\n",
        ]
        self.assertEqual(
            parse_streaming_response(lines),
            '{"decision":"skip"}',
        )

    def test_streaming_response_without_content_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_streaming_response([b"data: [DONE]\n"])

    def test_safe_json_comment_is_accepted(self) -> None:
        decision = parse_comment_decision(
            '{"decision":"comment","comment":"雨中的舞台很有力量","reason":"演出现场",'
            '"confidence":0.93,"commercial":false}'
        )
        self.assertEqual(decision.decision, "comment")
        self.assertEqual(decision.comment, "雨中的舞台很有力量")

    def test_markdown_wrapped_json_is_parsed(self) -> None:
        decision = parse_comment_decision(
            '```json\n{"decision":"skip","comment":"","reason":"商业内容",'
            '"confidence":0.98,"commercial":true}\n```'
        )
        self.assertEqual(decision.decision, "skip")

    def test_low_confidence_comment_is_rejected(self) -> None:
        decision = parse_comment_decision(
            '{"decision":"comment","comment":"这个画面很舒服","reason":"不确定",'
            '"confidence":0.4,"commercial":false}'
        )
        self.assertEqual(decision.decision, "skip")
        self.assertIn("low_confidence", decision.reason)

    def test_promotional_comment_is_rejected(self) -> None:
        decision = parse_comment_decision(
            '{"decision":"comment","comment":"喜欢的话联系我购买","reason":"产品",'
            '"confidence":0.99,"commercial":false}'
        )
        self.assertEqual(decision.decision, "skip")
        self.assertIn("banned_fragment", decision.reason)

    def test_contact_pattern_is_rejected(self) -> None:
        decision = parse_comment_decision(
            '{"decision":"comment","comment":"加我123456了解详情","reason":"回复",'
            '"confidence":0.99,"commercial":false}'
        )
        self.assertEqual(decision.decision, "skip")

    def test_invalid_response_fails_closed(self) -> None:
        decision = parse_comment_decision("finish(message='随便评论一句')")
        self.assertEqual(decision.decision, "skip")
        self.assertEqual(decision.comment, "")


if __name__ == "__main__":
    unittest.main()
