from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, Field


class Rect(BaseModel):
    """Absolute rectangle in virtual-screen pixels.

    Secondary monitors can have negative ``x`` or ``y`` coordinates.
    """

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def serialize_find_result(result: Any) -> dict[str, Any]:
    """Serialize a ``PyFind.utils.FindResult`` without importing PyFind."""
    center = getattr(result, "center", None)
    return {
        "x": int(getattr(result, "x", 0)),
        "y": int(getattr(result, "y", 0)),
        "width": int(getattr(result, "width", 0)),
        "height": int(getattr(result, "height", 0)),
        "center": [int(center[0]), int(center[1])] if center else None,
        "rect": [int(v) for v in getattr(result, "rect", ())],
        "confidence": round(float(getattr(result, "confidence", 0.0)), 4),
        "relative": {
            "x": int(getattr(result, "rel_x", 0)),
            "y": int(getattr(result, "rel_y", 0)),
            "width": int(getattr(result, "width", 0)),
            "height": int(getattr(result, "height", 0)),
        },
    }


def serialize_templates(
    templates: Iterable[Any],
    *,
    all_matches: bool,
    max_results: int,
) -> list[dict[str, Any]]:
    """Serialize PyFind template objects returned by ``FindResults``."""
    output: list[dict[str, Any]] = []
    for template in templates:
        matches = list(getattr(template, "get_results", lambda: [])())
        if not all_matches:
            matches = matches[:1]
        matches = matches[:max_results]
        output.append(
            {
                "name": getattr(template, "name", None),
                "path": getattr(template, "path", None),
                "found": bool(matches),
                "matches": [serialize_find_result(match) for match in matches],
            }
        )
    return output