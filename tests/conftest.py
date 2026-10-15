from __future__ import annotations

import shutil
import subprocess

import pytest

from support import ROOT, import_discovery_module


@pytest.fixture(scope="session")
def discovery():
    return import_discovery_module()


@pytest.fixture
def proxy_program(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the optional OpenChamber hook tests")
    hook = ROOT / "examples/openchamber/10-openchamber-loopback-proxy.sh"
    source = hook.read_text().split("<<'PROXY_EOF'\n", 1)[1].split("\nPROXY_EOF\n", 1)[0]
    program = tmp_path / "proxy.mjs"
    program.write_text(source)
    subprocess.run([node, "--check", str(program)], check=True, capture_output=True)
    return node, program
