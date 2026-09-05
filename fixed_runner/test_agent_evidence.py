import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image
from agent_evidence import read_incident
from agent_mcp import handle


class AgentEvidenceTests(unittest.TestCase):
    def test_single_existing_image_is_projected_without_private_ui_or_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new('RGB', (900, 1600), 'white').save(root / 'screen.png')
            (root / 'screen.xml').write_text('<hierarchy><node text="消息"/><node text="private-message-sentinel"/></hierarchy>', encoding='utf-8')
            row = SimpleNamespace(id='incident-one', task_id='task-one', stage='entry', error_type='entry_not_found',
                error_message='入口未识别', outcome='failed', recovery_action='restored', screenshot_path=str(root/'screen.png'), ui_tree_path=str(root/'screen.xml'))
            store = Mock()
            store.get_incident.return_value = row
            result = read_incident(store, root, {'incident_id': row.id, 'include_image': True})
            self.assertNotIn('private-message-sentinel', json.dumps(result))
            self.assertNotIn(str(root), json.dumps(result))
            self.assertEqual(result['ui_semantics']['known_markers'], ['消息'])
            reply = handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'incident_evidence', 'arguments': {'incident_id': row.id}}}, lambda *args: result)
            self.assertEqual(reply['result']['content'][1]['type'], 'image')
            store.finish_incident_analysis.assert_not_called()
            store.finish.assert_not_called()
            row.screenshot_path = str(root.parent / 'outside.png')
            row.ui_tree_path = str(root.parent / 'outside.xml')
            missing = read_incident(store, root, {'incident_id': row.id, 'include_image': True})
            self.assertFalse(missing['has_screenshot'])
            self.assertIsNone(missing['ui_semantics'])
            self.assertNotIn('_mcp_images', missing)
