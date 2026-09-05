"""Offline regression entry: provider state/credentials never use the live store."""
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fixed_runner'))
import model_providers

with tempfile.TemporaryDirectory() as temporary, open(os.devnull, 'w') as sink:
    with patch.multiple(model_providers, CONFIG_DB=Path(temporary)/'providers.db',
                        TASK_DB=Path(temporary)/'tasks.db', SECRET_ROOT=Path(temporary)/'secrets'):
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'fixed_runner'), pattern='test_*.py')
        with contextlib.redirect_stdout(sink):
            result = unittest.TextTestRunner(verbosity=1).run(suite)
        sys.exit(not result.wasSuccessful())
