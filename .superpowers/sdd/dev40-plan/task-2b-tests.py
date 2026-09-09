"""Scoped offline runner; same store isolation as scripts/test-python.py."""
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'fixed_runner'))
with tempfile.TemporaryDirectory() as temporary, open(os.devnull, 'w') as sink:
    with patch.dict(os.environ, {'MEDIAFLOW_DATA_ROOT': temporary, 'RISKFLOW_DATA_ROOT': temporary}):
        import runtime_layout
        with patch.multiple(runtime_layout, SECRET_ROOT=Path(temporary)/'secrets',
                            STREAM_SECRET_PATH=Path(temporary)/'secrets/device-stream-host.secret',
                            BOOTSTRAP_CONFIG_PATH=Path(temporary)/'bootstrap.json',
                            DEVICE_PROFILE_PATH=Path(temporary)/'device_profiles.json',
                            PLATFORM_PROFILE_PATH=Path(temporary)/'platform_profiles.json'):
            import model_providers
            with patch.multiple(model_providers, CONFIG_DB=Path(temporary)/'providers.db',
                                TASK_DB=Path(temporary)/'tasks.db', SECRET_ROOT=Path(temporary)/'secrets'):
                suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
                with contextlib.redirect_stdout(sink):
                    result = unittest.TextTestRunner(verbosity=1).run(suite)
                sys.exit(not result.wasSuccessful())
