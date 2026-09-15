"""Every unit test builds `Settings` from nothing but its own arguments.

A key sitting in the developer's shell must never change what a test proves — or which
port `build_ports` would pick — so the ambient credentials are cleared for the whole
unit suite. The live tests supply their own.
"""

from __future__ import annotations

import pytest

AMBIENT = (
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "APIFY_TOKEN",
    "SHEET_ID",
    "DRIVE_ROOT_FOLDER_ID",
    "GOOGLE_TOKEN_PATH",
    "GOOGLE_CLIENT_SECRET_PATH",
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(request: pytest.FixtureRequest) -> None:
    """Clear the real keys, unless the test is marked `live`."""
    if request.node.get_closest_marker("live"):
        return
    patcher = pytest.MonkeyPatch()
    for name in AMBIENT:
        patcher.delenv(name, raising=False)
    request.addfinalizer(patcher.undo)
