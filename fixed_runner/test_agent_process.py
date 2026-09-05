from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_process import DirectoryLease, ChildJob


class AgentProcessTests(unittest.TestCase):
    def test_only_one_owner_of_data_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = [DirectoryLease(Path(folder) / 'engine.lock') for _ in range(2)]
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()

    def test_job_closes_only_owned_process(self):
        job = ChildJob()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(15)'],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job.assign(child)
            job.close()
            self.assertIsNotNone(child.wait(timeout=3))
        finally:
            job.close()
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
