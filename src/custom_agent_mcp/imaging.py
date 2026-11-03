from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .settings import Settings


def _to_png_image(frame: Any) -> Any:
    import cv2

    if frame is None:
        raise RuntimeError("No frame was captured")
    if getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


def _write_png(path: Path, frame: Any) -> None:
    import cv2

    image = _to_png_image(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Failed to write screenshot to {path}")


def _prune_outputs(output_dir: Path, keep: int) -> None:
    files = sorted(
        output_dir.glob("pyfind_*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def save_screenshot(frame: Any, settings: Settings) -> dict[str, Any]:
    """Save a full-resolution PNG and a smaller preview for MCP image content."""
    import cv2

    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _prune_outputs(output_dir, settings.max_output_files)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    full_path = output_dir / f"pyfind_{stamp}_{token}.png"
    _write_png(full_path, frame)

    image = _to_png_image(frame)
    height, width = int(image.shape[0]), int(image.shape[1])
    preview_path = full_path
    preview_width, preview_height = width, height

    longest = max(width, height)
    if longest > settings.preview_max_width:
        scale = settings.preview_max_width / float(longest)
        preview_width = max(1, int(width * scale))
        preview_height = max(1, int(height * scale))
        preview = cv2.resize(image, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
        preview_path = output_dir / f"pyfind_{stamp}_{token}_preview.png"
        if not cv2.imwrite(str(preview_path), preview):
            preview_path = full_path
            preview_width, preview_height = width, height

    return {
        "path": str(full_path),
        "preview_path": str(preview_path),
        "width": width,
        "height": height,
        "preview_width": preview_width,
        "preview_height": preview_height,
    }