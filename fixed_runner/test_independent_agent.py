"""The platform cannot own or mutate the official chat runtime."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_control


class IndependentAgentTests(unittest.TestCase):
    def test_stop_before_child_registration_prevents_late_start(self):
        from independent_agent import main
        with tempfile.TemporaryDirectory() as directory:
            with patch('sys.argv', ['agent', 'stop', '--root', directory]):
                try:
                    self.assertEqual(main(), 0)
                except FileNotFoundError:
                    self.fail('Cancelled startup still inspected the engine')
            # No engine is installed here: a cancelled startup must exit before
            # attempting to inspect or launch it, preserving the stop intent.
            with patch('sys.argv', ['agent', 'run', '--root', directory, '--binary', str(Path(directory)/'missing.exe')]):
                self.assertEqual(main(), 0)

    def test_repair_health_does_not_require_agent(self):
        from repair_update_worker import DevelopmentDriver
        driver = object.__new__(DevelopmentDriver)
        driver.snapshot = lambda: {'product_version': {'source_revision': 'abc', 'source_dirty': False}, 'paused': True}
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def local_only(url, **kwargs):
            self.assertEqual(url, 'http://127.0.0.1:3001/')
            return Response()
        with patch('repair_update_worker.urllib.request.urlopen', side_effect=local_only):
            driver.health('abcdef')

    def test_management_spec_never_launches_native_gateway(self):
        with patch('runtime_control.shutil.which', return_value='C:/node/node.exe'), \
                patch.object(Path, 'is_file', return_value=True):
            spec = runtime_control.ui_spec()
        self.assertEqual(spec.env.get('PORT'), '3001')
        self.assertNotIn('native_console_host.py', ' '.join(spec.command))

    def test_automation_context_reads_status_without_chat_database(self):
        import automation
        self.assertTrue(hasattr(automation, 'PlatformContext'), 'automation still requires AgentService')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = automation.PlatformContext(lambda: {'paused': True}, root=root)
            result = automation.AutomationService(host).call({'action': 'platform_status', 'request_id': 'read'})
            self.assertTrue(result['ok'], result)
            self.assertFalse((root/'sessions.db').exists())
            self.assertFalse((root/'engine').exists())
            self.assertFalse(hasattr(host, 'runtime'))

    def test_official_launch_keeps_data_but_has_no_chat_proxy(self):
        self.assertIsNotNone(importlib.util.find_spec('independent_agent'), 'independent launcher is missing')
        from independent_agent import launch_spec
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root/'data/opencode/auth.json'
            auth.parent.mkdir(parents=True)
            auth.write_text('{"existing":"preserved"}')
            spec = launch_spec(root, Path('C:/opencode.exe'), python='C:/python.exe', port=13009)
            self.assertEqual(Path(spec.command[0]), Path('C:/opencode.exe'))
            self.assertEqual(spec.command[1:], ('serve', '--hostname', '127.0.0.1', '--port', '13009'))
            self.assertEqual(spec.env['XDG_DATA_HOME'], str(root/'data'))
            self.assertEqual(auth.read_text(), '{"existing":"preserved"}')
            config = json.loads(spec.env['OPENCODE_CONFIG_CONTENT'])
            self.assertNotIn('mcp', config)
            self.assertNotIn('permission', config)
            self.assertNotIn('OPENCODE_SERVER_PASSWORD', spec.env)
            self.assertNotIn('MEDIAFLOW_AGENT_BRIDGE_URL', spec.env)
            self.assertFalse((root/'gateway-connection.json').exists())


if __name__ == '__main__':
    unittest.main()
