"""Platform remains usable without any embedded chat modules or resources."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class RetiredAgentsTests(unittest.TestCase):
    def test_desktop_shell_opens_current_skill_home(self):
        source = (ROOT / 'launcher/RiskFlowLauncher.cs').read_text(encoding='utf-8-sig')
        self.assertIn('private const string DefaultUrl = "http://127.0.0.1:3001/";', source)
        self.assertNotIn('http://127.0.0.1:3000/', source)

    def test_business_context_has_no_engine_or_chat_store(self):
        from automation import PlatformContext, AutomationService
        from task_store import TaskStore
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = TaskStore(root / 'tasks.db')
            host = PlatformContext(lambda: {'paused': True}, root=root / 'agent', store=store)
            service = AutomationService(host)
            self.assertTrue(service.catalog()['actions'])
            for field in ('runtime', 'bridge', 'permissions', 'recovery'):
                self.assertFalse(hasattr(host, field), field)
            for name in ('engine', 'sessions.db', 'permissions'):
                self.assertFalse((host.root / name).exists())

    def test_old_chat_handler_is_tombstone_not_service_factory(self):
        from control_api import Handler
        handler = object.__new__(Handler)
        handler._json = Mock()
        handler.retired_chat()
        payload, status = handler._json.call_args.args
        self.assertEqual(status, 410)
        self.assertEqual(payload['reason_code'], 'external_skill_home')
        self.assertFalse(hasattr(Handler, 'agent_service'))

    def test_retired_modules_have_no_remaining_production_imports(self):
        retired = {'agent_service', 'agent_runtime', 'agent_permissions', 'agent_mcp',
                   'agent_bridge', 'agent_native', 'agent_tool_context', 'pi_host',
                   'pi_runtime', 'independent_agent', 'native_console_host'}
        for file in (ROOT / 'fixed_runner').glob('*.py'):
            if file.name.startswith('test_'):
                continue
            imports = set()
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8-sig'))):
                if isinstance(node, ast.ImportFrom):
                    imports.add(node.module)
                elif isinstance(node, ast.Import):
                    imports.update(x.name for x in node.names)
            self.assertFalse(imports & retired, (file.name, imports & retired))

    def test_packaged_resources_need_skill_not_engine(self):
        spec = importlib.util.spec_from_file_location('platform_resources', ROOT / 'packaging/build-agent-resources.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root, stage = Path(folder) / 'source', Path(folder) / 'stage'
            skill = root / 'fixed_runner/assets/agent/skills/mediaflow-platform'
            for name in module.REQUIRED:
                target = skill / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# fixture\n', encoding='utf-8')
            for name in module.RUNTIME_ASSETS:
                (root / 'fixed_runner/assets' / name).write_text(
                    json.dumps({'templates': [{'fixture': True}]}), encoding='utf-8')
            result = module.assemble(root, stage, 'fixture-revision')
            self.assertFalse(result['embedded_agent'])
            self.assertEqual(result['agent_integration'], 'external-skill')
            self.assertFalse((stage / 'runtime/opencode').exists())
            self.assertTrue((stage / 'assets/agent/repair-source.zip').is_file())
            for name in module.RUNTIME_ASSETS:
                self.assertEqual((stage / 'fixed_runner/assets' / name).read_bytes(),
                                 (root / 'fixed_runner/assets' / name).read_bytes())
            self.assertEqual(module.assemble(root, stage, 'fixture-revision')['skill_files'], result['skill_files'])
            (stage / 'runtime/opencode').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'retired'):
                module.assemble(root, stage, 'fixture-revision')
            (skill / 'scripts/mediaflow.py').unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                module.assemble(root, Path(folder) / 'fresh', 'fixture-revision')

    def test_packaged_resources_reject_missing_badge_dictionary(self):
        spec = importlib.util.spec_from_file_location('platform_resources', ROOT / 'packaging/build-agent-resources.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'
            for name in module.REQUIRED:
                target = root / 'fixed_runner/assets/agent/skills/mediaflow-platform' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# fixture\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'runtime asset missing'):
                module.assemble(root, Path(folder) / 'stage', 'fixture')
