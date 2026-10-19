"""Connection metadata is local state, never an installation asset."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('payload_audit', ROOT/'packaging/verify-electron-payload.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ConnectionPayloadTests(unittest.TestCase):
    def test_local_connection_file_is_rejected_from_release(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for name in audit.REQUIRED:
                path = stage/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            (stage/'runtime-lock.json').write_text(json.dumps({'files': {}}))
            self.assertTrue(audit.verify(stage)['ok'])
            (stage/'agent-connection.json').write_text('{"private_path":"fixture"}')
            with self.assertRaisesRegex(ValueError, 'Local state'):
                audit.verify(stage)
