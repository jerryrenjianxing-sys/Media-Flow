from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import model_connection


class ModelConnectionTest(unittest.TestCase):
    def test_forbidden_is_not_misreported_as_invalid_key(self):
        with patch("model_connection.requests.get", return_value=Mock(status_code=403)):
            status, message = model_connection._auth("test")
        self.assertEqual(status, "pending")
        self.assertIn("拒绝", message)
        self.assertNotIn("Key 无效", message)

    def _paths(self, root: str):
        base = Path(root)
        return patch.multiple(
            model_connection,
            OPENROUTER_KEY_PATH=base / "active.dpapi",
            OPENROUTER_PENDING_KEY_PATH=base / "pending.dpapi",
            MODEL_CONNECTION_PATH=base / "status.json",
        )

    @staticmethod
    def _fake_encrypt(key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")

    @staticmethod
    def _fake_decrypt(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_authenticated_candidate_is_promoted_and_requires_model_test(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._encrypt_to", side_effect=self._fake_encrypt
        ), patch("model_connection._decrypt", side_effect=self._fake_decrypt), patch(
            "model_connection._auth", return_value=("authenticated", "鉴权成功")
        ):
            result = model_connection.save_candidate("sk-or-v1-" + "a" * 64)
            self.assertTrue(result["key_configured"])
            self.assertEqual(result["auth_status"], "authenticated")
            self.assertEqual(result["model_test_status"], "untested")
            self.assertFalse(result["model_ready"])

    def test_invalid_candidate_preserves_existing_active_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._encrypt_to", side_effect=self._fake_encrypt
        ), patch("model_connection._decrypt", side_effect=self._fake_decrypt), patch(
            "model_connection._auth", return_value=("invalid", "Key 无效")
        ):
            model_connection.OPENROUTER_KEY_PATH.write_text("old", encoding="utf-8")
            result = model_connection.save_candidate("sk-or-v1-" + "b" * 64)
            self.assertFalse(result["accepted"])
            self.assertEqual(model_connection.OPENROUTER_KEY_PATH.read_text(encoding="utf-8"), "old")
            self.assertFalse(model_connection.OPENROUTER_PENDING_KEY_PATH.exists())

    def test_network_failure_keeps_candidate_without_replacing_active_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._encrypt_to", side_effect=self._fake_encrypt
        ), patch("model_connection._decrypt", side_effect=self._fake_decrypt), patch(
            "model_connection._auth", return_value=("pending", "等待联网")
        ):
            model_connection.OPENROUTER_KEY_PATH.write_text("old", encoding="utf-8")
            result = model_connection.save_candidate("sk-or-v1-" + "c" * 64)
            self.assertTrue(result["accepted"])
            self.assertTrue(result["has_pending_key"])
            self.assertEqual(model_connection.OPENROUTER_KEY_PATH.read_text(encoding="utf-8"), "old")

    def test_unreadable_active_key_is_not_reported_as_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._decrypt", side_effect=RuntimeError("different Windows identity")
        ):
            model_connection.OPENROUTER_KEY_PATH.write_text("encrypted", encoding="utf-8")

            result = model_connection.verify()

            self.assertFalse(result["key_configured"])
            self.assertEqual(result["storage_status"], "unreadable")
            self.assertEqual(result["auth_status"], "unreadable")
            self.assertFalse(result["model_ready"])
            self.assertIn("重新输入", result["message"])

    def test_model_test_turns_unreadable_key_into_visible_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._decrypt", side_effect=RuntimeError("different Windows identity")
        ):
            model_connection.OPENROUTER_KEY_PATH.write_text("encrypted", encoding="utf-8")

            result = model_connection.test_current_model()

            self.assertEqual(result["storage_status"], "unreadable")
            self.assertEqual(result["model_test_status"], "failed")
            self.assertIn("重新输入", result["message"])

    def test_new_authenticated_key_clears_unreadable_storage_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self._paths(directory), patch(
            "model_connection._encrypt_to", side_effect=self._fake_encrypt
        ), patch("model_connection._decrypt", side_effect=self._fake_decrypt), patch(
            "model_connection._auth", return_value=("authenticated", "鉴权成功")
        ):
            model_connection.MODEL_CONNECTION_PATH.write_text(
                '{"storage_status":"unreadable","auth_status":"unreadable"}',
                encoding="utf-8",
            )

            result = model_connection.save_candidate("sk-or-v1-" + "d" * 64)

            self.assertTrue(result["key_configured"])
            self.assertEqual(result["storage_status"], "stored")
            self.assertEqual(result["auth_status"], "authenticated")

    def test_second_model_operation_is_rejected_instead_of_running_concurrently(self) -> None:
        class BusyLock:
            def acquire(self, blocking: bool = True) -> bool:
                return False

            def release(self) -> None:
                raise AssertionError("a lock that was not acquired must not be released")

        with patch.object(model_connection, "MODEL_OPERATION_LOCK", BusyLock()):
            with self.assertRaisesRegex(RuntimeError, "正在进行"):
                model_connection.save_candidate("sk-or-v1-" + "e" * 64)

    def test_new_encryption_uses_native_dpapi_without_powershell(self) -> None:
        protected = b"protected-value"
        with tempfile.TemporaryDirectory() as directory, patch(
            "model_connection._dpapi_transform", return_value=protected
        ) as transform, patch("model_connection.subprocess.run") as run:
            target = Path(directory) / "candidate.dpapi"
            model_connection._encrypt_to("sk-or-v1-" + "f" * 64, target)
            self.assertTrue(target.read_text(encoding="ascii").startswith(model_connection.DPAPI_PREFIX))
            transform.assert_called_once()
            run.assert_not_called()

    def test_native_dpapi_payload_round_trips(self) -> None:
        if model_connection.os.name != "nt":
            self.skipTest("Windows only")
        value = "sk-or-v1-" + "9" * 64
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "candidate.dpapi"
            model_connection._encrypt_to(value, target)
            self.assertEqual(value, model_connection._decrypt(target))


if __name__ == "__main__":
    unittest.main()
