# encoding: utf-8
"""Smoke checks for the browser viewer asset."""
import shutil
import subprocess
from pathlib import Path

import pytest


VIEWER_JS = Path(__file__).resolve().parents[1] / 'public' / 'maplibre_viewer' / 'viewer.js'


def test_viewer_js_parses_with_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    subprocess.run(
        [node, '--check', str(VIEWER_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
