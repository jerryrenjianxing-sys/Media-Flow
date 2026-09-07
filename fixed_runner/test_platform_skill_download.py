"""Actual-HTTP tests for the read-only portable Skill download."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile


EXPECTED_FILES = [
    "mediaflow-platform/NOTICE.md",
    "mediaflow-platform/README.md",
    "mediaflow-platform/SKILL.md",
    "mediaflow-platform/config.example.json",
    "mediaflow-platform/examples/execute-arguments.json",
    "mediaflow-platform/examples/plan-arguments.json",
    "mediaflow-platform/examples/request-status-arguments.json",
    "mediaflow-platform/references/api.md",
    "mediaflow-platform/references/troubleshooting.md",
    "mediaflow-platform/references/workflows.md",
    "mediaflow-platform/scripts/_common.py",
    "mediaflow-platform/scripts/mediaflow.ps1",
    "mediaflow-platform/scripts/mediaflow.py",
]


SERVER_SCRIPT = r"""
import sys
import threading
from http.server import ThreadingHTTPServer
import control_api

class ForbiddenStore:
    def __getattribute__(self, name):
        raise AssertionError('download route accessed the task store: ' + name)

class IsolatedHandler(control_api.Handler):
    store = ForbiddenStore()
    def log_message(self, *args):
        pass

if sys.argv[1] == 'fail':
    def fail_bundle(*args, **kwargs):
        raise OSError('private fixture detail must not escape')
    control_api.build_skill_bundle = fail_bundle

server = ThreadingHTTPServer(('127.0.0.1', 0), IsolatedHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(server.server_port, flush=True)
try:
    sys.stdin.readline()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
"""


class PlatformSkillDownloadTests(unittest.TestCase):
    @contextmanager
    def isolated_server(self, root: Path, mode: str = "ok"):
        data_root = root / "data"
        temporary_root = root / "temporary"
        temporary_root.mkdir()
        environment = {
            **os.environ,
            "MEDIAFLOW_APP_ROOT": str(Path(__file__).resolve().parents[1]),
            "MEDIAFLOW_DATA_ROOT": str(data_root),
            "TEMP": str(temporary_root),
            "TMP": str(temporary_root),
        }
        child = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-c", SERVER_SCRIPT, mode],
            cwd=Path(__file__).resolve().parent,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        port_line = child.stdout.readline().strip()
        if not port_line:
            _, stderr = child.communicate(timeout=5)
            self.fail("isolated API did not start: " + stderr)
        try:
            yield "http://127.0.0.1:%s" % int(port_line), temporary_root
        finally:
            if child.poll() is None:
                child.stdin.write("\n")
                child.stdin.flush()
            _, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stderr)

    def test_download_returns_deterministic_allowlisted_text_zip_under_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.isolated_server(Path(directory)) as (base_url, temporary_root):
                def download():
                    try:
                        response = urllib.request.urlopen(base_url + "/api/platform-skill", timeout=10)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        return response.status, dict(response.headers.items()), response.read()

                with ThreadPoolExecutor(max_workers=4) as executor:
                    responses = list(executor.map(lambda _index: download(), range(4)))

                first_payload = responses[0][2]
                for status, headers, payload in responses:
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["Content-Type"], "application/zip")
                    self.assertEqual(
                        headers["Content-Disposition"],
                        'attachment; filename="MediaFlow-Skill.zip"',
                    )
                    self.assertEqual(int(headers["Content-Length"]), len(payload))
                    self.assertEqual(payload, first_payload)
                with zipfile.ZipFile(BytesIO(first_payload)) as archive:
                    self.assertEqual(archive.namelist(), EXPECTED_FILES)
                    self.assertEqual(len(archive.namelist()), 13)
                    for name in EXPECTED_FILES:
                        archive.read(name).decode("utf-8-sig")
                self.assertEqual(list(temporary_root.iterdir()), [])

    def test_download_failure_is_bounded_and_leaves_no_temporary_output(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.isolated_server(Path(directory), "fail") as (base_url, temporary_root):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(base_url + "/api/platform-skill", timeout=10)
                response = raised.exception
                payload = response.read().decode("utf-8")
                self.assertEqual(response.code, 503)
                self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
                self.assertEqual(
                    json.loads(payload),
                    {"error": "MediaFlow Skill 下载包暂不可用，请检查安装文件后重试。"},
                )
                self.assertNotIn("private fixture detail", payload)
                self.assertEqual(list(temporary_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
