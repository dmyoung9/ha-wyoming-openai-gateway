from __future__ import annotations

import pytest

from support import import_discovery_module


@pytest.fixture(scope="session")
def discovery():
    return import_discovery_module()
