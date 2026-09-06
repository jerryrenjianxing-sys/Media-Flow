"""Native frontend integration without model calls or live credentials."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent_runtime import PROVIDER_ID, MODEL_ID


class NativeIntegrationTests(unittest.TestCase):
    def test_direct_provider_config_preserves_native_tools_and_catalog(self):
        from agent_native import native_config
        config = native_config()
        self.assertNotIn('mcp', config)
        self.assertNotIn('plugin', config)
        self.assertNotIn('permission', config)
        self.assertNotIn('agent', config)
        self.assertNotIn('enabled_providers', config)
        self.assertNotIn('disabled_providers', config)
        provider = config['provider'][PROVIDER_ID]
        self.assertEqual(provider['options']['baseURL'], 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        self.assertNotIn('apiKey', provider['options'])
        self.assertFalse(provider['models'][MODEL_ID]['options']['enable_thinking'])

    def test_native_start_does_not_launch_bridge_or_reply_dispatcher(self):
        from agent_service import AgentService
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Mock(root=Path(tmp)/'engine')
            runtime.status.return_value = {'state': 'ready'}
            service = AgentService(lambda: {}, root=Path(tmp), runtime=runtime, native_frontend=True)
            with patch('agent_service.current_qwen_key', return_value=''):
                service._start()
            self.assertIsNone(service.bridge)
            self.assertIsNone(service._watcher)
            runtime.start.assert_called_once()
            self.assertNotIn('mcp', runtime.start.call_args.args[0])
            service.close()

    def test_credentials_existing_native_are_never_overwritten(self):
        from agent_native import NativeMigration
        with tempfile.TemporaryDirectory() as tmp:
            auth = Mock()
            auth.native_auth.return_value = {'type': 'api', 'key': 'existing'}
            resolver = Mock()
            migration = NativeMigration(Path(tmp), auth, resolver)
            self.assertEqual(migration.credentials()['state'], 'preserved')
            resolver.assert_not_called()
            auth.runtime.request.assert_not_called()

    def test_migration_uses_native_auth_api_and_reads_back_without_key_in_receipt(self):
        from agent_native import NativeMigration
        with tempfile.TemporaryDirectory() as tmp:
            auth = Mock()
            auth.native_auth.side_effect = [None, {'type': 'api', 'key': 'fixture-key'}]
            auth.runtime.request.return_value = True
            result = NativeMigration(Path(tmp), auth, lambda: 'fixture-key').credentials()
            self.assertEqual(result['state'], 'migrated')
            auth.runtime.request.assert_any_call('PUT', '/auth/'+PROVIDER_ID, {'type': 'api', 'key': 'fixture-key'})
            self.assertNotIn('fixture-key', json.dumps(result))
            self.assertNotIn('fixture-key', (Path(tmp)/'migration.json').read_text(encoding='utf-8'))

    def test_missing_or_unreadable_key_does_not_block_native_sessions(self):
        from agent_native import NativeMigration
        for resolver in [lambda: '', Mock(side_effect=PermissionError('secret-value'))]:
            with tempfile.TemporaryDirectory() as tmp:
                auth = Mock()
                auth.native_auth.return_value = None
                result = NativeMigration(Path(tmp), auth, resolver).credentials()
                self.assertIn(result['state'], {'not_configured', 'failed'})
                self.assertNotIn('secret-value', json.dumps(result))
                auth.runtime.request.assert_not_called()

    def test_backup_uses_consistent_sqlite_snapshot_and_preserves_existing_backup(self):
        from agent_native import NativeMigration
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'sessions.db'
            with closing(sqlite3.connect(source)) as db:
                db.execute('create table evidence(value text)')
                db.execute("insert into evidence values ('original')")
                db.commit()
            migration = NativeMigration(root/'migration', Mock(), lambda: '')
            first = migration.backup({'host': source})
            second = migration.backup({'host': source})
            self.assertEqual(first, second)
            with closing(sqlite3.connect(first['host'])) as db:
                self.assertEqual(db.execute('select value from evidence').fetchone()[0], 'original')
            self.assertTrue(source.is_file())

    def test_skill_install_copies_references_and_scripts_without_touching_other_skills(self):
        from agent_runtime import install_platform_skill
        with tempfile.TemporaryDirectory() as tmp:
            target = install_platform_skill(Path(tmp))
            self.assertTrue((target.parent/'scripts/mediaflow.py').is_file())
            self.assertTrue(any((target.parent/'references').glob('*.md')))

    def test_archive_only_explicit_cleared_ids_and_never_deletes_messages(self):
        from agent_native import NativeMigration
        with tempfile.TemporaryDirectory() as tmp:
            root, auth = Path(tmp), Mock()
            (root/'archive-request.json').write_text(json.dumps({'session_ids': ['ses_old', 'ses_active']}))
            state = {'ses_old': {}, 'ses_active': {}}
            def request(method, path, body=None):
                sid = path.rsplit('/', 1)[-1]
                if method == 'PATCH':
                    state[sid] = body['time']
                return {'id': sid, 'time': state[sid]}
            auth.runtime.request.side_effect = request
            migration = NativeMigration(root, auth, lambda: '')
            migration.archive_cleared({'ses_active'})
            migration.archive_cleared({'ses_active'})
            self.assertEqual([c.args[0] for c in auth.runtime.request.call_args_list].count('PATCH'), 1)
            self.assertTrue(state['ses_old']['archived'])
            self.assertFalse(state['ses_active'])
            self.assertNotIn('DELETE', [c.args[0] for c in auth.runtime.request.call_args_list])

    def test_partial_backup_is_never_accepted_on_retry(self):
        from agent_native import NativeMigration
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.db'
            with closing(sqlite3.connect(source)) as db:
                db.execute('create table fixture(value)');db.commit()
            backup=root/'migration/backup/host.db';backup.parent.mkdir(parents=True)
            backup.write_bytes(b'partial database')
            migration=NativeMigration(root/'migration',Mock(),lambda:'')
            with self.assertRaisesRegex(RuntimeError,'备份'):
                migration.backup({'host':source})
            self.assertFalse((root/'migration/migration.json').exists())
            self.assertEqual(backup.read_bytes(),b'partial database')
