from __future__ import annotations

import numpy as np
import pytest

from custom_agent_mcp.runtime import PyFindRuntime, _to_pyfind_rect
from custom_agent_mcp.settings import Settings


def test_to_pyfind_rect_uses_left_top():
    assert _to_pyfind_rect({"x": -100, "y": 20, "width": 30, "height": 40}) == {
        "left": -100,
        "top": 20,
        "width": 30,
        "height": 40,
    }


def test_templates_accept_png_and_webp(tmp_path):
    cv2 = pytest.importorskip("cv2")
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    image = np.zeros((4, 5, 3), dtype=np.uint8)
    cv2.imwrite(str(templates_dir / "button.png"), image)
    cv2.imwrite(str(templates_dir / "dialog.webp"), image)

    runtime = PyFindRuntime(
        Settings(templates_dir=templates_dir, output_dir=tmp_path / "out")
    )
    try:
        listing = runtime.list_templates()
        assert listing["count"] == 2
        assert {item["name"] for item in listing["templates"]} == {"button", "dialog"}

        assert runtime._load_template_locked("button").data.shape[:2] == (4, 5)
        assert runtime._load_template_locked("dialog").data.shape[:2] == (4, 5)
        assert runtime._load_template_locked("dialog.webp").data.shape[:2] == (4, 5)
    finally:
        runtime.close()


def test_find_locked_passes_tuple_and_pyfind_rect(tmp_path):
    runtime = PyFindRuntime(
        Settings(templates_dir=tmp_path / "templates", output_dir=tmp_path / "out")
    )

    class FakeFinder:
        threshold = 0.5

        def __init__(self):
            self.calls = []

        def look(self, templates, **kwargs):
            self.calls.append((templates, kwargs))
            return None

    finder = FakeFinder()
    runtime._find_locked(
        finder,
        [object()],
        {"x": 10, "y": 20, "width": 30, "height": 40},
        threshold=0.7,
        timeout=1.0,
        disappear=False,
        all_matches=True,
        max_results=10,
    )

    templates, kwargs = finder.calls[0]
    assert isinstance(templates, tuple)
    assert kwargs["frame_src"] == {"left": 10, "top": 20, "width": 30, "height": 40}
    assert kwargs["ocr_settings"] == {}
    assert finder.threshold == 0.5
    runtime.close()