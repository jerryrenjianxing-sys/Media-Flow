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
with tempfile.TemporaryDirectory() as temporary, open(os.devnull, 'w') as sink:
    # Set the data boundary before importing modules with eager default stores.
    # Patching only the model provider left Handler.store on the developer DB.
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
                suite = unittest.defaultTestLoader.discover(str(ROOT / 'fixed_runner'), pattern='test_*.py')
                with contextlib.redirect_stdout(sink):
                    result = unittest.TextTestRunner(verbosity=1).run(suite)
                sys.exit(not result.wasSuccessful())
