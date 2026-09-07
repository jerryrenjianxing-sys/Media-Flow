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

    def test_native_git_discovery_environment_is_preserved(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.dict('os.environ',{'ProgramFiles':str(root/'apps'),'ProgramFiles(x86)':str(root/'apps86')},clear=True):
                spec=launch_spec(root,root/'node.exe',root/'ui',root/'guard.mjs')
            self.assertEqual(spec.env.get('ProgramFiles'),str(root/'apps'))
            self.assertEqual(spec.env.get('ProgramFiles(x86)'),str(root/'apps86'))

    def test_missing_windows_shell_blocks_upstream_auto_download(self):
        import pi_runtime
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            env={'ProgramFiles':str(root/'apps'),'USERPROFILE':str(root/'profile')}
            validator=getattr(pi_runtime,'validate_windows_shell',None)
            self.assertTrue(callable(validator),'missing startup shell validation')
            with self.assertRaisesRegex(ValueError,'Bash'):
                validator(env)
            bash=root/'apps/Git/bin/bash.exe'
            bash.parent.mkdir(parents=True)
            bash.touch()
            validator(env)

    def test_windowless_diagnostics_identify_known_causes_without_raw_errors(self):
        from pi_host import main
        from unittest.mock import patch
        cases=[(RuntimeError('本机端口 13034 已被占用，无法启动界面'),'pi_port_in_use'),
               (ValueError('Pi运行资源缺失或版本不符；请重新准备'),'pi_source_mismatch'),
               (ValueError('Pi所需Bash运行组件缺失；请先准备'),'pi_shell_missing'),
               (RuntimeError('upstream secret fixture-private-value'),'pi_host_failed')]
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'runtime.json'
            for error,code in cases:
                with patch('sys.argv',['pi','run','--config',str(config)]), patch('pi_host._run',side_effect=error):
                    self.assertEqual(main(),1)
                record=(Path(folder)/'last-error.json').read_text(encoding='utf-8')
                self.assertEqual(json.loads(record)['reason_code'],code)
                self.assertNotIn('fixture-private-value',record)

if __name__=='__main__': unittest.main()
