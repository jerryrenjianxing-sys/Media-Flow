import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class NativeHostTests(unittest.TestCase):
    def test_owned_management_port_matches_links_and_api_origin(self):
        import native_console_host
        legacy=Mock(env={}, command=('node.exe','vinext','start','--port','3000'), cwd='fixture')
        with patch('runtime_control.ui_spec',return_value=legacy), patch.object(native_console_host,'ChildJob'), patch.object(native_console_host,'require_available_ports',create=True), patch.object(native_console_host,'supervise',return_value=0) as run:
            native_console_host.main()
        specs=run.call_args.args[0]
        self.assertEqual(specs[0][1]['PORT'],'3001')
        self.assertEqual(specs[0][0][-1],'3001')
        self.assertEqual(specs[1][1]['MEDIAFLOW_LEGACY_UI_URL'],'http://127.0.0.1:3001')

    def test_native_ui_uses_one_owned_process_wrapper(self):
        import runtime_control
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'native_console/dist').mkdir(parents=True)
            (root/'native_console/dist/index.html').write_text('native')
            with patch.object(runtime_control, 'PROJECT_ROOT', root):
                spec = runtime_control.ui_spec()
            self.assertIn('native_console_host.py', ' '.join(spec.command))
            self.assertEqual(spec.role, 'control-ui')
            self.assertEqual(spec.env['NODE_ENV'], 'production')

    def test_host_closes_both_owned_children_when_either_exits(self):
        from native_console_host import supervise
        children = [Mock(), Mock()]
        children[0].poll.return_value = None
        children[1].poll.return_value = 3
        job = Mock()
        with patch('native_console_host.subprocess.Popen', side_effect=children):
            result = supervise([(['legacy'], {}, '.'), (['gateway'], {}, '.')], job)
        self.assertEqual(result, 3)
        self.assertEqual(job.assign.call_count, 2)
        job.close.assert_called_once()
        children[0].wait.assert_called_once()
        children[1].wait.assert_called_once()

    def test_no_orphan_if_second_launch_fails(self):
        from native_console_host import supervise
        child, job = Mock(), Mock()
        with patch('native_console_host.subprocess.Popen', side_effect=[child, OSError('fixture')]):
            with self.assertRaises(OSError):
                supervise([(['legacy'], {}, '.'), (['gateway'], {}, '.')], job)
        job.close.assert_called_once()
        child.wait.assert_called_once()
