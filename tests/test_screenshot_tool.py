from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
from mcp import Client
from mcp.types import ImageContent, TextContent

from custom_agent_mcp.runtime import PyFindRuntime
from custom_agent_mcp.server import create_server
from custom_agent_mcp.settings import Settings


def test_screenshot_returns_text_and_image(monkeypatch, tmp_path):
    settings = Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=False,
        preview_max_width=8,
    )
    server = create_server(settings)
    frame = np.zeros((10, 20, 4), dtype=np.uint8)
    rect = {"x": 0, "y": 0, "width": 20, "height": 10}
    target = {"kind": "screen", "rect": rect, "requested_rect": rect}

    monkeypatch.setattr(PyFindRuntime, "resolve_target", lambda self, **kwargs: (rect, target))
    monkeypatch.setattr(
        PyFindRuntime,
        "capture",
        lambda self, rect, target: (frame, {"region": rect, "target": target, "captured_at": 0.0}),
    )

    async def run():
        async with Client(server) as client:
            return await client.call_tool("screenshot", {})

    result = asyncio.run(run())

    assert result.is_error is False
    assert any(isinstance(item, TextContent) for item in result.content)
    assert any(isinstance(item, ImageContent) for item in result.content)

    text = next(item for item in result.content if isinstance(item, TextContent)).text
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["width"] == 20
    assert payload["height"] == 10
    assert (tmp_path / "out").exists()
    assert payload["path"].endswith(".png")
    assert Path(payload["path"]).exists()