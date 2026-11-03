from __future__ import annotations

from PyFind import utils

from custom_agent_mcp.models import Rect, serialize_find_result


def test_serialize_find_result_absolute_and_relative():
    result = utils.FindResult(10, 20, 30, 40, 0.81234, utils.Point(-100, 200))

    payload = serialize_find_result(result)

    assert payload["x"] == -90
    assert payload["y"] == 220
    assert payload["width"] == 30
    assert payload["height"] == 40
    assert payload["center"] == [-75, 240]
    assert payload["rect"] == [-90, 220, -60, 260]
    assert payload["confidence"] == 0.8123
    assert payload["relative"] == {"x": 10, "y": 20, "width": 30, "height": 40}


def test_rect_rejects_empty_dimensions():
    try:
        Rect(x=0, y=0, width=0, height=10)
    except Exception:
        pass
    else:
        raise AssertionError("Rect should reject a zero width")