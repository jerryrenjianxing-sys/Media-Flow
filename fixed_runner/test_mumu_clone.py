import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from mumu_clone import snapshot, wait_clone


class CloneCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = self.root / 'nx_main' / 'MuMuManager.exe'
        self.manager.parent.mkdir(); self.manager.touch()
        self.vms = self.root / 'vms'; self.vms.mkdir()
        source = self.vms / 'MuMuPlayer-15.0-4'; source.mkdir()
        (source/'data.vdi').write_bytes(b'private-test-disk')
        self.deadline = time.monotonic() + 10
        self.evidence = snapshot(self.manager, '4', deadline=self.deadline)
        self.provider = Mock()
        self.provider.list_instances.return_value = [
            {'provider_instance_id':'4','state':'stopped'},
            {'provider_instance_id':'5','state':'stopped'}]
        self.target = self.vms / 'MuMuPlayer-15.0-5'; self.target.mkdir()

    def wait(self):
        return wait_clone(self.provider, {'4'}, self.evidence, deadline=self.deadline)

    def test_matching_complete_disk_is_returned_without_device_action(self):
        (self.target/'data.vdi').write_bytes(b'private-test-disk')
        self.assertEqual(self.wait()['provider_instance_id'],'5')
        self.provider.clone.assert_not_called()
        self.provider.launch.assert_not_called()

    def test_partial_copy_waits_for_exact_content_not_just_size(self):
        disk = self.target/'data.vdi'; disk.write_bytes(b'wrong!!-test-disk')
        with patch('mumu_clone.time.sleep', side_effect=lambda _: disk.write_bytes(b'private-test-disk')) as sleep:
            self.assertEqual(self.wait()['provider_instance_id'],'5')
        sleep.assert_called_once()

    def test_multiple_results_never_choose_a_candidate(self):
        (self.vms/'MuMuPlayer-15.0-6').mkdir()
        with self.assertRaisesRegex(RuntimeError,'不唯一'): self.wait()

    def test_source_change_rejects_copy(self):
        self.evidence['disk'].write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError,'来源状态改变'): self.wait()

    def test_deadline_preserves_partial_copy(self):
        self.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(RuntimeError,'截止时间'): self.wait()
        self.assertTrue(self.target.exists())

    def test_directory_identity_must_match_candidate(self):
        self.provider.list_instances.return_value[1]['provider_instance_id']='6'
        with self.assertRaisesRegex(RuntimeError,'不匹配'): self.wait()
