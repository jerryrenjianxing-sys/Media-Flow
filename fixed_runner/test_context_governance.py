"""Context guards exercise failures without loading any production state."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('context_guard', ROOT / 'scripts/check-context.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class ContextGovernanceTests(unittest.TestCase):
    def test_links_and_fragments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'guide.md').write_text('# 安装配置\n', encoding='utf-8')
            self.assertEqual([], guard.check_text(root, 'README.md', '[指南](guide.md#安装配置)'))
            self.assertTrue(guard.check_text(root, 'README.md', '[错](missing.md)'))
            self.assertTrue(guard.check_text(root, 'README.md', '[错](guide.md#丢失)'))

    def test_retired_instructions_not_mentions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for old in ['打开 http://127.0.0.1:3000/', '抖音已安装并登录',
                        '必须点击计划确认卡', '运行3条零写入后才交付为就绪']:
                self.assertTrue(guard.check_text(root, 'README.md', old), old)
            self.assertEqual([], guard.check_text(root, 'README.md', '不内置OpenCode/Pi；三次验证不是使用门槛。'))

    def test_registry_rejects_new_and_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(guard, 'inventory_paths', return_value=['new.md']):
                errors = guard.validate(root, {'files': [{'path': 'old.md', 'role': 'historical',
                                                        'action': 'retain', 'reason': '旧证据'}]})
            self.assertEqual(2, len(errors))

    def test_historical_material_is_not_current_instruction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'old.md').write_text('http://127.0.0.1:3000/ [旧](missing.md)', encoding='utf-8')
            row = {'path': 'old.md', 'role': 'historical', 'action': 'retain', 'reason': '历史原文'}
            with patch.object(guard, 'inventory_paths', return_value=['old.md']):
                self.assertEqual([], guard.validate(root, {'files': [row]}))

    def test_checked_in_context(self):
        import json
        manifest = json.loads((ROOT / guard.REGISTRY).read_text(encoding='utf-8'))
        self.assertEqual([], guard.validate(ROOT, manifest))


if __name__ == '__main__':
    unittest.main()
