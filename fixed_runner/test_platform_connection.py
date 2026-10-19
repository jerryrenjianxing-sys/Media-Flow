"""Connection Interface exercised with local filesystem and runtime facts only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from platform_connection import current_connection, publish_connection, connection_card, onboarding_summary


class ConnectionTests(unittest.TestCase):
    def test_atomic_publication_and_card_share_current_descriptor_after_upgrade(self):
        with tempfile.TemporaryDirectory(prefix='连接 空格') as directory:
            root = Path(directory)
            with patch.multiple('runtime_layout', DATA_ROOT=root/'data', APP_ROOT=root/'版本 一',
                                INSTANCE_ID='acceptance-connection', IS_DISTRIBUTION=True,
                                BUNDLED_PYTHON=root/'版本 一/runtime/python/python.exe', API_PORT=49338):
                initial = current_connection()
            published = publish_connection(initial)
            self.assertEqual(json.loads(published.read_text(encoding='utf-8')), initial)
            with patch.multiple('runtime_layout', DATA_ROOT=root/'data', APP_ROOT=root/'版本 二',
                                INSTANCE_ID='acceptance-connection', IS_DISTRIBUTION=True,
                                BUNDLED_PYTHON=root/'版本 二/runtime/python/python.exe', API_PORT=49340):
                upgraded = current_connection()
            self.assertEqual(publish_connection(upgraded), published)
            self.assertEqual(json.loads(published.read_text(encoding='utf-8')), upgraded)
            self.assertEqual(list(published.parent.iterdir()), [published])
            card = connection_card(upgraded)
            self.assertLessEqual(len(card.encode('utf-8')), 4096)
            self.assertIn(str(published), card)
            self.assertIn(upgraded['skill_path'], card)
            self.assertNotIn('版本 一', card)
            self.assertFalse(upgraded['runtime_available'])
            self.assertEqual(upgraded['api_url'], 'http://127.0.0.1:49340')

    def test_snapshot_summary_filters_retired_but_never_claims_current_online(self):
        summary = onboarding_summary({'available': True, 'paused': True, 'read_at': 'now',
            'devices': [{'last_known_state': 'running', 'last_seen_at': 'yesterday'},
                        {'last_known_state': 'retired', 'last_seen_at': 'today'}]})
        self.assertEqual(summary['registered_devices'], 1)
        self.assertFalse(summary['online_verified'])
        self.assertEqual(summary['task_readiness'], 'not_checked')
        self.assertEqual(summary['read_at'], 'now')
        self.assertEqual(summary['last_device_seen_at'], 'yesterday')
        self.assertNotIn('task_summary', summary)
        self.assertIsNone(onboarding_summary({'available': False})['registered_devices'])

    def test_powershell_literal_is_escaped_and_overlong_card_is_rejected(self):
        connection = {'connection_file': "C:/模拟 O'Brien/agent-connection.json", 'skill_path': 'Skill.md'}
        self.assertIn("O''Brien", connection_card(connection))
        connection['connection_file'] = '长' * 4096
        with self.assertRaisesRegex(ValueError, '4KiB'):
            connection_card(connection)
