from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from agent_operations import AgentOperations
from task_store import TaskStore
from virtual_commands import submit_virtual_command


class AgentOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root / 'tasks.db')
        self.store.save_virtual_device({'virtual_device_id': 'vm1', 'provider': 'mumu', 'provider_install_id': 'install',
            'provider_instance_id': '0', 'name': 'MuMu安卓设备', 'state': 'stopped', 'recipe': {}, 'provider_snapshot': {}})
        self.executor = Mock()
        self.operations = AgentOperations(self.root / 'commands', self.store,
            lambda device_id, body: submit_virtual_command(self.store, device_id, body, executor=self.executor))

    def tearDown(self):
        self.temp.cleanup()

    def test_command_requires_approval_and_duplicate_does_not_execute_again(self):
        command = self.operations.propose({'virtual_device_id': 'vm1', 'action': 'start'}, {'session_id': 'session1', 'call_id': 'call1'})
        self.executor.assert_not_called()
        with self.assertRaises(ValueError):
            self.operations.confirm(command['command_id'], 'session1', {})
        body = {'confirmed': True, 'fingerprint': command['fingerprint']}
        with patch('virtual_commands.threading.Thread') as thread:
            first = self.operations.confirm(command['command_id'], 'session1', body)
            second = self.operations.confirm(command['command_id'], 'session1', body)
            self.assertEqual(first['operation']['id'], second['operation']['id'])
            thread.return_value.start.assert_called_once()
        self.assertEqual(self.store.get_virtual_device('vm1')['state'], 'stopped')

    def test_delete_name_and_no_backup_are_explicit(self):
        command = self.operations.propose({'virtual_device_id': 'vm1', 'action': 'delete', 'backup': False}, {'session_id': 'session1', 'call_id': 'call1'})
        body = {'confirmed': True, 'fingerprint': command['fingerprint']}
        with self.assertRaisesRegex(ValueError, '完整名称'):
            self.operations.confirm(command['command_id'], 'session1', body)
        with patch('virtual_commands.threading.Thread'):
            result = self.operations.confirm(command['command_id'], 'session1', {**body, 'confirmation_name': 'MuMu安卓设备'})
        operation = self.store.get_virtual_operation(result['operation']['id'])
        self.assertFalse(operation['request']['backup'])
        self.executor.assert_not_called()

    def test_session_cancel_identity_drift_and_arbitrary_path_are_blocked(self):
        with self.assertRaises(ValueError):
            self.operations.propose({'virtual_device_id': 'vm1', 'action': 'start', 'mumu_path': 'other.exe'}, {'session_id': 'session1', 'call_id': 'call0'})
        command = self.operations.propose({'virtual_device_id': 'vm1', 'action': 'start'}, {'session_id': 'session1', 'call_id': 'call1'})
        body = {'confirmed': True, 'fingerprint': command['fingerprint']}
        device = self.store.get_virtual_device('vm1')
        self.store.save_virtual_device({**device, 'android_identity': 'changed'})
        with self.assertRaisesRegex(ValueError, '身份'):
            self.operations.confirm(command['command_id'], 'session1', body)
        self.operations.cancel_unconfirmed('session1')
        with self.assertRaisesRegex(ValueError, '取消'):
            self.operations.confirm(command['command_id'], 'session1', body)
        with self.assertRaises(ValueError):
            self.operations.get(command['command_id'], 'session2')

    def test_common_command_repeated_nonce_checks_target_and_does_not_spawn(self):
        with patch('virtual_commands.threading.Thread') as thread:
            result = submit_virtual_command(self.store, 'vm1', {'action': 'start', 'idempotency_key': 'once'}, executor=self.executor)
            repeated = submit_virtual_command(self.store, 'vm1', {'action': 'start', 'idempotency_key': 'once'}, executor=self.executor)
            self.assertEqual(result['operation']['id'], repeated['operation']['id'])
            thread.return_value.start.assert_called_once()
            with self.assertRaises(ValueError):
                submit_virtual_command(self.store, 'vm1', {'action': 'stop', 'idempotency_key': 'once'}, executor=self.executor)


if __name__ == '__main__':
    unittest.main()
