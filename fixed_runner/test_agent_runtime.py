import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_runtime import AgentRuntime, AgentRuntimeError, build_config, isolated_environment


class AgentRuntimeTests(unittest.TestCase):
    def test_dependency_errors_are_specific_and_do_not_leak_exception(self):
        for problem,code in [(TimeoutError('private'), 'engine_dependencies_timeout'),
                             (PermissionError('private'), 'engine_dependencies_permission'),
                             (FileNotFoundError('private'), 'engine_dependencies_missing')]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                binary=Path(tmp)/'fixture.exe';binary.write_bytes(b'fixture')
                runtime=AgentRuntime(Path(tmp)/'state',binary=binary)
                with patch('agent_runtime.hashlib.file_digest') as digest, patch('agent_runtime.prepare_dependencies',side_effect=problem):
                    from agent_runtime import ENGINE_SHA256
                    digest.return_value.hexdigest.return_value=ENGINE_SHA256
                    with self.assertRaises(AgentRuntimeError) as failure:runtime.start({})
                    self.assertEqual(failure.exception.code,code)
                    self.assertNotIn('private',runtime.status()['message'])
                    self.assertTrue(runtime.status()['diagnostic_id'])

    def test_provider_overlay_preserves_catalog(self):
        config = build_config('http://127.0.0.1:49200', 'ephemeral', ['python', 'mcp.py'])
        self.assertNotIn('enabled_providers', config)
        self.assertNotIn('disabled_providers', config)
        provider = config['provider']['mediaflow-qwen-token-plan']
        self.assertIn('qwen3.8-flash', provider['models'])
        self.assertEqual(provider['npm'], '@ai-sdk/openai-compatible')
        self.assertNotIn('response_format', json.dumps(config))

    def test_environment_does_not_inherit_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            env = isolated_environment(Path(folder), {'PATH': 'tools', 'OPENAI_API_KEY': 'secret',
                'OPENCODE_CONFIG_CONTENT': 'personal', 'SOME_TOKEN': 'secret'})
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('SOME_TOKEN', env)
            self.assertNotEqual(env.get('OPENCODE_CONFIG_CONTENT'), 'personal')
            self.assertEqual(env['XDG_DATA_HOME'], str(Path(folder) / 'data'))

    def test_missing_binary_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(Path(folder), binary=Path(folder) / 'absent.exe')
            with self.assertRaises(AgentRuntimeError) as error:
                runtime.start({})
            self.assertEqual(error.exception.code, 'engine_missing')
            self.assertEqual(runtime.status()['state'], 'failed')

    def test_proxy_path_not_arbitrary(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(Path(folder))
            with self.assertRaises(ValueError):
                runtime.request('GET', 'https://example.com')
            with self.assertRaises(ValueError):
                runtime.request('GET', '//example.com')

    def test_checksum_failure_does_not_start_process(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'wrong.exe'
            binary.write_bytes(b'not-an-engine')
            runtime = AgentRuntime(Path(folder) / 'state', binary=binary)
            with patch('agent_runtime.subprocess.Popen') as spawn:
                with self.assertRaises(AgentRuntimeError) as failure:
                    runtime.start({})
                self.assertEqual(failure.exception.code, 'engine_checksum')
                spawn.assert_not_called()

    def test_ready_restart_is_idempotent_and_exited_process_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = AgentRuntime(Path(folder))
            process = Mock()
            process.poll.return_value = None
            runtime._process, runtime._state = process, 'ready'
            with patch('agent_runtime.subprocess.Popen') as spawn:
                self.assertEqual(runtime.start({})['state'], 'ready')
                spawn.assert_not_called()
            process.poll.return_value = 1
            self.assertEqual(runtime.status()['reason_code'], 'engine_exited')
            runtime.stop()
            process.terminate.assert_not_called()

    def test_startup_timeout_terminates_only_owned_child(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'fixture.exe'
            binary.write_bytes(b'fixture')
            runtime = AgentRuntime(Path(folder) / 'state', binary=binary)
            process = Mock()
            process.poll.return_value = None
            with patch('agent_runtime.hashlib.file_digest') as digest, \
                 patch('agent_runtime.ChildJob') as job, \
                 patch('agent_runtime.subprocess.Popen', return_value=process):
                from agent_runtime import ENGINE_SHA256
                digest.return_value.hexdigest.return_value = ENGINE_SHA256
                with self.assertRaises(AgentRuntimeError) as failure:
                    runtime.start({}, timeout=0)
                self.assertEqual(failure.exception.code, 'engine_timeout')
                process.terminate.assert_called_once()
                job.return_value.close.assert_called_once()
                self.assertIsNone(runtime._lease.handle)


if __name__ == '__main__':
    unittest.main()
