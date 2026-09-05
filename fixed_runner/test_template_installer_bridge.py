import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from template_installer_bridge import execute


class TemplateInstallerBridgeTests(unittest.TestCase):
    def test_process_diagnostics_do_not_render_environment_secrets(self):
        from runtime_control import ProcessSpec
        spec=ProcessSpec('test',('python',),'root','log','python',env={'API_KEY':'fixture-private-secret'})
        self.assertNotIn('fixture-private-secret',repr(spec))

    def test_http_endpoint_passes_private_request_to_existing_template_worker(self):
        import json,threading,urllib.request
        from http.server import ThreadingHTTPServer
        from control_api import Handler
        from task_store import TaskStore
        with tempfile.TemporaryDirectory() as root:
            store=TaskStore(Path(root)/'tasks.db'); store.set_paused(True)
            manifest=Path(root)/'manifest.json'; manifest.write_text('{}')
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            event=threading.Event()
            with patch.object(Handler,'store',store), patch('control_api._run_virtual_device_create',side_effect=lambda **kw:event.set()) as worker:
                thread.start()
                try:
                    body={'private_manifest':str(manifest),'confirmation':'导入私人快照，保留旧模板和实例',
                          'new_attempt':True,'new_attempt_confirmation':'保留失败副本并新建一次','mumu_path':'chosen-manager','idempotency_key':'unique'}
                    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/virtual-device-template',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=5) as response: payload=json.load(response)
                    self.assertTrue(event.wait(2))
                    self.assertTrue(payload['operation']['request']['new_attempt'])
                    self.assertEqual(worker.call_args.kwargs['custom_path'],'chosen-manager')
                finally:
                    server.shutdown(); server.server_close(); thread.join(2)

    def test_installer_only_submits_backend_and_polls_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            version = {'version': '0.4.1-dev.16', 'source_revision': 'fixture'}
            pending = {'id':'one','status':'running','stage':'importing','progress':40}
            done = {**pending,'status':'completed','result':{'template_version':'private'}}
            with patch('product_version.product_version', return_value=version), \
                    patch('template_installer_bridge.time.sleep'), \
                    patch('template_installer_bridge.request', side_effect=[{'product_version':version},{'operation':pending},{'operation':done}]) as call, \
                    patch('local_vm_template.LocalVmTemplate.create') as local:
                result=execute(Path(root)/'manifest.json', custom_path='manager', new_attempt=True)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(call.call_args_list[1].args[0],'/api/virtual-device-template')
            self.assertTrue(call.call_args_list[1].args[1]['new_attempt'])
            local.assert_not_called()

    def test_three_read_failures_never_resubmit(self):
        version={'version':'test','source_revision':'one'}
        with tempfile.TemporaryDirectory() as root, patch('product_version.product_version',return_value=version), \
                patch('template_installer_bridge.time.sleep'), \
                patch('template_installer_bridge.request',side_effect=[{'product_version':version},{'operation':{'id':'one','status':'running','stage':'importing','progress':1}},OSError(),OSError(),OSError()]) as call:
            with self.assertRaisesRegex(RuntimeError,'连续三次'):
                execute(Path(root)/'manifest.json')
            self.assertEqual(sum(c.args[0]=='/api/virtual-device-template' for c in call.call_args_list),1)

    def test_wrong_version_never_dispatches(self):
        with patch('product_version.product_version',return_value={'version':'new','source_revision':'new'}), \
                patch('template_installer_bridge.time.monotonic',side_effect=[0,121]), \
                patch('template_installer_bridge.request',return_value={'product_version':{'version':'old'}}) as call:
            with self.assertRaisesRegex(RuntimeError,'同版本'):
                execute('manifest.json')
            self.assertEqual(call.call_count,1)

    def test_lost_submit_response_is_not_replayed(self):
        version={'version':'same','source_revision':'one'}
        with patch('product_version.product_version',return_value=version), \
                patch('template_installer_bridge.request',side_effect=[{'product_version':version},OSError('lost')]) as call:
            with self.assertRaisesRegex(RuntimeError,'未自动重发'):
                execute('manifest.json')
            self.assertEqual(call.call_count,2)
