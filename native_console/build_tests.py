"""Offline regression checks for pinned native source preparation."""
import hashlib
import http.server
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import build


class NativeBuildTests(unittest.TestCase):
    def test_transport_retries_have_a_hard_limit(self):
        with patch.object(build.urllib.request, "urlopen", side_effect=OSError("offline")) as request:
            with patch.object(build.time, "sleep"):
                with self.assertRaises(OSError):
                    build.read_url("https://example.invalid/source")
        self.assertEqual(request.call_count, 3)

    def test_truncated_source_read_retries_boundedly(self):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"hi" if len(requests) == 1 else b"hello")
            def log_message(self, *_):
                pass
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertEqual(build.read_url(f"http://127.0.0.1:{server.server_port}/blob"), b"hello")
                self.assertEqual(len(requests), 2)
            finally:
                server.shutdown()
                thread.join()

    def test_git_tree_rejects_tampered_metadata(self):
        blob = build.git_hash(b"hello")
        payload = b"100644 a.txt\0" + bytes.fromhex(blob)
        tree = build.git_hash(payload, "tree")
        entries = [{"path": "a.txt", "mode": "100644", "type": "blob", "sha": blob}]
        build.verify_tree(entries, tree)
        entries[0]["sha"] = "0" * 40
        with self.assertRaises(ValueError):
            build.verify_tree(entries, tree)

    def test_exact_patch_rejects_drift(self):
        edit = {"old": "first", "new": "second"}
        self.assertEqual(build.apply_edits("first line", [edit]), "second line")
        with self.assertRaises(ValueError):
            build.apply_edits("first first", [edit])
        with self.assertRaises(ValueError):
            build.apply_edits("changed", [edit])

    def test_download_checks_blob_before_replacing_existing_file(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not the expected source")
            def log_message(self, *_):
                pass
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "source"
                    target.write_bytes(b"preserved")
                    with self.assertRaises(ValueError):
                        build.fetch_blob(f"http://127.0.0.1:{server.server_port}/blob", "0" * 40, target)
                    self.assertEqual(target.read_bytes(), b"preserved")
            finally:
                server.shutdown()
                thread.join()

    def test_materialized_link_and_escape_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target").write_bytes(b"source")
            (root / "link").write_text("target")
            build.materialize(root, "link", "target")
            self.assertEqual((root / "link").read_bytes(), b"source")
            build.materialize(root, "link", "target")
            (root / "link").write_bytes(b"local edit")
            with self.assertRaises(ValueError):
                build.materialize(root, "link", "target")
            self.assertEqual((root / "link").read_bytes(), b"local edit")
            with self.assertRaises(ValueError):
                build.materialize(root, "link", "../outside")


if __name__ == "__main__":
    unittest.main()
