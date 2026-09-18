"""Run production JavaScript behaviour tests with Node's built-in test runner."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_behaviour_including_unknown_data_polling_and_scope():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js fehlt: Frontend-Verhaltenstests wurden nicht ausgeführt")
    suite = Path(__file__).with_name("frontend_v100.test.js")
    result = subprocess.run([node, "--test", str(suite)], capture_output=True, text=True,
                            timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
