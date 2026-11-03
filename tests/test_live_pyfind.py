from __future__ import annotations

import pytest

from custom_agent_mcp.runtime import PyFindRuntime
from custom_agent_mcp.settings import Settings

pytestmark = pytest.mark.integration


def test_real_capture_initializes_pyfind(tmp_path):
    runtime = PyFindRuntime(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=False,
        )
    )
    try:
        virtual = runtime.virtual_screen()
        frame, metadata = runtime.capture(dict(virtual), {"kind": "screen"})
        assert frame is not None
        assert frame.ndim == 3
        assert frame.shape[2] == 4
        assert metadata["region"]["width"] > 0
        assert metadata["region"]["height"] > 0
    finally:
        runtime.close()