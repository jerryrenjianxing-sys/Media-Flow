import json
from pathlib import Path
import tempfile
import unittest

from pi_runtime import prepare_config, launch_spec, validate_source


class PiRuntimeTests(unittest.TestCase):
    def test_candidate_credentials_never_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            old=root/'old.json'
            old.write_text(json.dumps({'mediaflow-qwen-token-plan':{'type':'api','key':'fixture-key'}}))
            prepare_config(root/'pi', old)
            auth=root/'pi/agent/auth.json'
            self.assertEqual(json.loads(auth.read_text())['mediaflow-qwen-token-plan']['key'], 'fixture-key')
            before=auth.read_bytes()
            old.write_text('{}')
            prepare_config(root/'pi', old)
            self.assertEqual(before,auth.read_bytes())
            model=json.loads((root/'pi/agent/models.json').read_text())['providers']['mediaflow-qwen-token-plan']
            self.assertFalse(model['models'][0]['samplingParams']['enable_thinking'])

    def test_missing_key_does_not_block_config_or_new_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            result=prepare_config(root,root/'missing')
            self.assertFalse(result['credential_present'])
            self.assertTrue((root/'agent/settings.json').is_file())

    def test_launch_is_independent_and_budgeted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            spec=launch_spec(root,root/'node.exe',root/'ui',root/'guard.mjs',python=root/'python.exe',port=13030)
            self.assertEqual(spec.role,'pi-agent')
            self.assertIn('--import',spec.command)
            self.assertTrue(spec.command[2].startswith('file:///'))
            self.assertEqual(spec.env['PI_WEB_HOST'],'127.0.0.1')
            self.assertEqual(spec.env['PI_WEB_ENGINE'],'pi')
            self.assertNotIn('OPENCODE_CONFIG_CONTENT',spec.env)
            self.assertEqual(spec.env['MEDIAFLOW_PI_TRIAL_BUDGET_DIR'],str(root/'trial-request-budget'))

    def test_missing_or_wrong_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): validate_source(Path(folder))

    def test_stop_intent_prevents_late_host_start(self):
        from pi_host import main
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'runtime.json'
            config.write_text(json.dumps({'root':str(Path(folder)/'pi')}))
            with patch('sys.argv',['pi','stop','--config',str(config)]): self.assertEqual(main(),0)
            with patch('sys.argv',['pi','run','--config',str(config)]): self.assertEqual(main(),0)

    def test_missing_runtime_has_durable_windowless_error(self):
        from pi_host import main
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'runtime.json'
            with patch('sys.argv',['pi','run','--config',str(config)]): self.assertEqual(main(),1)
            self.assertEqual(json.loads((Path(folder)/'last-error.json').read_text())['reason_code'],'pi_runtime_missing')

if __name__=='__main__': unittest.main()
