import json
from pathlib import Path
import tempfile
import time
import unittest
from agent_dependencies import dependency_manifest, prepare_dependencies


class AgentDependencyTests(unittest.TestCase):
    def test_offline_seed_is_idempotent_and_does_not_copy_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'packaging/agent-engine'
            plugin=source/'node_modules/@opencode-ai/plugin/package.json'
            plugin.parent.mkdir(parents=True)
            plugin.write_text('{"version":"1.18.29"}')
            (source/'package.json').write_text('{"dependencies":{"@opencode-ai/plugin":"1.18.29"}}')
            (source/'package-lock.json').write_text(json.dumps({'packages':{'node_modules/@opencode-ai/plugin':{'version':'1.18.29'}}}))
            (source/'auth.json').write_text('must-not-copy')
            engine=root/'engine';auth=engine/'data/opencode/auth.json';auth.parent.mkdir(parents=True);auth.write_text('preserve-local')
            result=prepare_dependencies(root,engine,time.monotonic()+10)
            self.assertEqual(result['files'],3)
            self.assertEqual(prepare_dependencies(root,engine,time.monotonic()+10),result)
            self.assertEqual(auth.read_text(),'preserve-local')
            self.assertFalse((engine/'config/opencode/auth.json').exists())
            with self.assertRaises(TimeoutError):prepare_dependencies(root,engine,0)
            manifest=dependency_manifest(source)
            manifest['files']['../auth.json']='bad'
            (source/'dependency-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):prepare_dependencies(root,engine,time.monotonic()+10)
            manifest=dependency_manifest(source)
            manifest['engine_version']='unknown'
            (source/'dependency-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):prepare_dependencies(root,engine,time.monotonic()+10)
            manifest['engine_version']='1.18.29'
            del manifest['files']['package-lock.json']
            (source/'dependency-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):prepare_dependencies(root,engine,time.monotonic()+10)
