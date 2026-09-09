from __future__ import annotations

import json
import os
import sqlite3
import time
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import model_providers as providers
from model_budget import budgeted_post
from task_store import TaskStore


class TokenPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(providers, CONFIG_DB=self.root / "providers.db",
                                    TASK_DB=self.root / "tasks.db", SECRET_ROOT=self.root / "secrets")
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.old = patch("model_connection.status", return_value={"model_ready": True, "model_test_status": "passed", "provider": "OpenRouter", "model": "old"})
        self.old.start()
        self.addCleanup(self.old.stop)
        self.encrypt = patch("model_connection._encrypt_to", side_effect=self.encrypt_fake)
        self.decrypt = patch("model_connection._decrypt", side_effect=lambda p: p.read_text())
        self.encrypt.start(); self.decrypt.start()
        self.addCleanup(self.encrypt.stop); self.addCleanup(self.decrypt.stop)
        self.store = TaskStore(providers.TASK_DB)
        self.store.set_paused(True)

    @staticmethod
    def encrypt_fake(key, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key)

    def save(self):
        return providers.save_candidate("sk-sp-" + "a" * 32, providers.QWEN)

    def passed(self):
        self.save()
        with providers.database() as c:
            value = providers._candidate(c)
            value.update(model_test_status="passed", consent=providers.NOTICE_VERSION, tested_contract=providers.QWEN_CONTRACT)
            providers._write_candidate(c, value)
        return value["key_ref"]

    def response(self, code=200, value=None):
        response = MagicMock(status_code=code)
        response.__enter__.return_value = response
        response.json.return_value = value or {"choices": [{"message": {"content": "{}"}}]}
        return response

    def test_save_is_local_and_does_not_activate(self):
        with patch("model_budget.requests.post") as post, patch("model_connection.requests.get") as get:
            state = self.save()
        post.assert_not_called(); get.assert_not_called()
        self.assertEqual(state["model_test_status"], "untested")
        self.assertEqual(state["active_provider"], "openrouter")
        self.assertNotIn("sk-sp-", json.dumps(state))
        with self.assertRaises(ValueError):
            providers.activate(providers.QWEN)

    def test_doctor_uses_selected_qwen_without_requiring_openrouter(self):
        import runtime_control
        self.passed()
        providers.activate(providers.QWEN)
        with patch.object(runtime_control, "migrate_secret", side_effect=AssertionError("must not require OpenRouter")), patch.object(runtime_control, "background_task_check", return_value=(True, "test")):
            checks = runtime_control.doctor()["checks"]
        self.assertTrue(next(item for item in checks if item["name"] == "model_key")["ok"])
        self.assertFalse(any(item["name"] == "openrouter_key" for item in checks))

    def test_wrong_provider_key_is_rejected_without_overwriting(self):
        name = self.passed()
        for key in ("sk-or-v1-" + "b"*32, "sk-ws-" + "b"*32, "sk-sp-short"):
            with self.assertRaises(ValueError):
                providers.save_candidate(key, providers.QWEN)
        with providers.database() as c:
            self.assertEqual(providers._candidate(c)["key_ref"], name)

    def test_activation_then_new_candidate_failure_protects_old_key(self):
        name = self.passed()
        providers.activate(providers.QWEN)
        self.save()
        self.assertEqual(providers.selection()["active_key"], name)
        self.assertTrue(providers.status()["model_ready"])
        self.assertFalse(providers.status(providers.QWEN)["model_ready"])
        key, base, model = providers.resolve_runtime_model("stale-or-key", "old-url", "old-model")
        self.assertEqual((key, base, model), ("sk-sp-"+"a"*32, providers.QWEN_BASE_URL, providers.QWEN_MODEL))

    def test_busy_or_unpaused_activation_rejected(self):
        self.passed()
        self.store.set_paused(False)
        with self.assertRaisesRegex(ValueError, "暂停"):
            providers.activate(providers.QWEN)
        self.store.set_paused(True)
        with providers.admitted_request(providers.OPENROUTER):
            with self.assertRaisesRegex(ValueError, "模型请求"):
                providers.activate(providers.QWEN)
        self.assertEqual(providers.selection()["revision"], 0)

    def test_concurrent_usage_survives_reopen_without_a_limit(self):
        ref = self.passed()
        def attempt(_):
            try:
                with providers.admitted_request(providers.QWEN, candidate_ref=ref):
                    raise providers.ProviderError("network_timeout")
            except providers.ProviderError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(15)))
        self.assertEqual(results, ["network_timeout"] * 15)
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 15)
        self.assertIsNone(providers.status(providers.QWEN)["request_limit"])
        self.assertIsNone(providers.status(providers.QWEN)["requests_remaining"])
        self.assertEqual(attempt(0), "network_timeout")
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 16)

    def test_consent_missing_never_calls_model(self):
        self.save()
        with patch("model_budget.requests.post") as post:
            with self.assertRaises(providers.ProviderError):
                providers.test_candidate(consent=False)
        post.assert_not_called()
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 0)

    def test_real_probe_contract_not_just_http_success(self):
        self.save()
        good = {"choices": [{"message": {"content": json.dumps({"number": "1234", "color": "red", "text_check": "OK"})}}]}
        with patch("model_providers.secrets.randbelow", return_value=234), patch("model_budget.requests.post", return_value=self.response(value=good)) as post:
            state = providers.test_candidate(consent=True)
        self.assertEqual(state["model_test_status"], "passed")
        self.assertEqual(providers.selection()["provider"], "openrouter")
        request = post.call_args.kwargs["json"]
        self.assertTrue(request["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertFalse(request["enable_thinking"])
        self.assertEqual(request["response_format"], {"type": "json_object"})
        with patch("model_budget.requests.post", return_value=self.response()):
            result = providers.test_candidate(consent=True)
        self.assertEqual(result["model_test_status"], "failed")
        self.assertEqual(result["requests_used"], 2)

    def test_qwen_payload_and_usage_are_separate_from_usd_budget(self):
        ref = self.passed()
        response = self.response(value={"usage": {"total_tokens": 50, "secret": "bad", "cost": 100}})
        with patch.dict(os.environ, {"MEDIAFLOW_MODEL_BUDGET_PATH": "must-not-touch.db"}), patch("model_budget.requests.get", side_effect=AssertionError('no pricing lookup')), patch("model_budget.requests.post", return_value=response) as post:
            with budgeted_post(providers.QWEN_BASE_URL+"/chat/completions", candidate_ref=ref,
                               data=json.dumps({"model": providers.QWEN_MODEL, "provider": {"foo": 1}, "usage": {}, "stream": True}), headers={"Authorization": "Bearer dummy", "X-OpenRouter-Metadata": "enabled"}) as stream:
                stream.json()
        body = post.call_args.kwargs["json"]
        self.assertNotIn("provider", body); self.assertNotIn("usage", body)
        self.assertNotIn("X-OpenRouter-Metadata", post.call_args.kwargs["headers"])
        with providers.database() as c:
            self.assertEqual(json.loads(c.execute("SELECT usage FROM calls").fetchone()[0]), {"total_tokens": 50})

    def test_error_codes_redacted_and_quota_distinct(self):
        for code, detail, expected in ((401, "secret", "authentication"), (403, "secret", "permission"),
                                      (429, "Allocated quota exceeded", "quota_exhausted"), (429, "rate limit", "rate_limited"),
                                      (404, "bad model", "model_unavailable")):
            with self.assertRaises(providers.ProviderError) as caught:
                providers.http_error(self.response(code, {"error": {"message": detail}}))
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(caught.exception.retryable, expected == "rate_limited")

    def test_dead_call_is_closed_without_refund_or_replay(self):
        ref = self.passed()
        with providers.database() as c:
            c.execute("INSERT INTO calls VALUES ('dead',?,0,99999999,0,NULL,NULL,NULL)", (providers.QWEN,))
        with patch("runtime_control.SystemProcessInspector.snapshot", return_value=None):
            providers.activate(providers.QWEN)
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 1)
        self.assertEqual(providers.selection()["active_key"], ref)

    def test_stale_provider_request_cannot_mix_after_switch(self):
        self.passed(); providers.activate(providers.QWEN)
        with self.assertRaises(providers.ProviderError):
            with providers.admitted_request(providers.OPENROUTER):
                self.fail("must reject stale OpenRouter caller")

    def test_timed_out_probe_cannot_later_promote(self):
        from control_vision import _bounded_call
        self.save()
        good = self.response(value={"choices": [{"message": {"content": '{"number":"1234","color":"red","text_check":"OK"}'}}]})
        def slow(*args, **kwargs):
            time.sleep(.08)
            return good
        with patch("model_providers.secrets.randbelow", return_value=234), patch("model_budget.requests.post", side_effect=slow), patch("control_vision._bounded_call", side_effect=lambda call, seconds: _bounded_call(call, .02)):
            result = providers.test_candidate(consent=True)
            time.sleep(.12)
        self.assertEqual(result["model_test_status"], "failed")
        self.assertEqual(providers.status(providers.QWEN)["model_test_status"], "failed")
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 1)
        self.assertEqual(providers.selection()["provider"], "openrouter")

    def test_duplicate_test_and_unreadable_key_have_recoverable_errors(self):
        self.save()
        with providers.database() as c:
            candidate = providers._candidate(c)
            candidate.update(model_test_status="testing", testing_pid=os.getpid())
            providers._write_candidate(c, candidate)
        with patch("model_budget.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "正在进行"):
                providers.test_candidate(consent=True)
        post.assert_not_called()

    def test_model_contract_change_invalidates_old_test(self):
        self.passed(); providers.activate(providers.QWEN)
        with patch.object(providers, "QWEN_CONTRACT", "changed-contract"):
            self.assertFalse(providers.status()["model_ready"])
            self.assertFalse(providers.status(providers.QWEN)["can_enable"])
            with self.assertRaises(providers.ProviderError):
                providers.resolve_runtime_model()

    def test_unreadable_qwen_never_blocks_adb_worker_or_falls_back(self):
        import runtime_control
        self.passed(); providers.activate(providers.QWEN)
        with patch("model_connection._decrypt", side_effect=RuntimeError("different Windows user")):
            self.assertFalse(providers.status()["model_ready"])
            self.assertEqual(providers.status()["reason_code"], "credential_unreadable")
            self.assertIsNone(runtime_control.optional_analyzer_spec())
            worker = runtime_control.worker_spec("test-only-offline")
            self.assertNotIn("PHONE_AGENT_API_KEY", worker.env)
            with self.assertRaises(providers.ProviderError):
                providers.resolve_runtime_model()

    def test_model_api_save_verify_test_enable_contract(self):
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from control_api import Handler
        class IsolatedHandler(Handler):
            store = self.store
        server = ThreadingHTTPServer(("127.0.0.1", 0), IsolatedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None):
            raw = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}" + path, data=raw,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as response:
                return response.code, json.load(response)
        try:
            code, initial = request("/api/model?provider=qwen_token_plan")
            self.assertEqual((code, initial["active_provider"]), (200, "openrouter"))
            with patch("model_budget.requests.post") as post, patch("model_connection.requests.get") as get:
                code, saved = request("/api/model-key", {"provider": providers.QWEN, "api_key": "sk-sp-" + "x"*32})
                self.assertEqual(code, 200)
                self.assertTrue(saved["accepted"])
                self.assertEqual(request("/api/model/verify", {"provider": providers.QWEN})[0], 200)
                self.assertEqual(request("/api/model/test", {"provider": providers.QWEN})[0], 400)
                self.assertNotEqual(request("/api/model", {"provider": providers.QWEN})[0], 200)
            post.assert_not_called(); get.assert_not_called()
            self.assertEqual(providers.selection()["provider"], "openrouter")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_all_four_business_entries_use_selected_model(self):
        from PIL import Image
        from comment_ai import generate_comment, analyze_topic, review_comment_constraint, COMMENT_CONSTRAINT_PROMPT_VERSION
        from control_vision import VisionCandidateLocator
        from incident_analysis import analyze_incident
        self.passed(); providers.activate(providers.QWEN)
        for _ in range(10):
            with providers.admitted_request(providers.QWEN):
                pass
        image = self.root / "test.png"
        Image.new("RGB", (900, 1600), "white").save(image)
        task = self.store.submit("healthcheck", "offline-test-only")
        incident = self.store.record_incident(task_id=task, device_id="offline-test-only", video_index=0,
                                              stage="navigation", error_type="test", error_message="test",
                                              outcome="skipped", recovery_action="none", screenshot_path=str(image))
        response = self.response()
        def lines_for(value):
            return [("data: " + json.dumps({"choices": [{"delta": {"content": json.dumps(value)}}]})).encode(), b"data: [DONE]"]
        with patch("model_budget.requests.post", return_value=response) as post:
            response.iter_lines.return_value = lines_for({"decision": "skip", "comment": "", "reason": "unclear", "confidence": .1, "commercial": False})
            generate_comment(image)
            response.iter_lines.return_value = lines_for({"matches": False, "relevance": "uncertain", "topic": "test", "evidence": [], "reason": "unclear", "confidence": .1, "safe": False})
            analyze_topic(image, "test")
            response.iter_lines.return_value = lines_for({"decision": "block", "category": "policy_uncertain", "reason": "unclear", "confidence": .1, "policy_version": COMMENT_CONSTRAINT_PROMPT_VERSION})
            review_comment_constraint(image, "内部预览", "test")
            response.iter_lines.return_value = lines_for({"classification": "unknown", "summary": "页面不明确", "suggested_rule": "保留证据", "confidence": .1, "risk": "high"})
            analyze_incident(self.store.get_incident(incident))
            response.iter_lines.return_value = lines_for({"page_type": "unknown"})
            VisionCandidateLocator()._request(image, "Return JSON page type", 20)
        self.assertEqual(post.call_count, 5)
        for call in post.call_args_list:
            self.assertEqual(call.args[0], providers.QWEN_BASE_URL + "/chat/completions")
            self.assertEqual(call.kwargs["json"]["model"], providers.QWEN_MODEL)
            self.assertFalse(call.kwargs["json"]["enable_thinking"])
        self.assertEqual(providers.status(providers.QWEN)["requests_used"], 15)


if __name__ == "__main__":
    unittest.main()
