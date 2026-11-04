from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .settings import Settings


def _to_bgr_image(frame: Any) -> Any:
    import cv2

    if frame is None:
        raise RuntimeError("No frame was captured")
    if getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


def _image_extension(settings: Settings) -> str:
    if settings.image_format not in {"png", "webp"}:
        raise ValueError(
            f"image_format must be 'png' or 'webp', got {settings.image_format!r}"
        )
    return settings.image_format


def _imwrite_params(settings: Settings) -> list[int]:
    import cv2

    if settings.image_format != "webp":
        return []
    # OpenCV treats quality > 100 as lossless WebP.
    quality = 101 if settings.webp_lossless else settings.webp_quality
    return [cv2.IMWRITE_WEBP_QUALITY, quality]


def _write_image(path: Path, frame: Any, settings: Settings) -> bool:
    import cv2

    image = _to_bgr_image(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(path), image, _imwrite_params(settings)))


def _prune_outputs(output_dir: Path, keep: int) -> None:
    files = []
    for pattern in ("pyfind_*.png", "pyfind_*.webp"):
        files.extend(output_dir.glob(pattern))
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def save_screenshot(frame: Any, settings: Settings) -> dict[str, Any]:
    """Save a full-resolution image and a smaller preview for MCP content."""
    import cv2

    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    _prune_outputs(output_dir, settings.max_output_files)

    extension = _image_extension(settings)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    full_path = output_dir / f"pyfind_{stamp}_{token}.{extension}"
    if not _write_image(full_path, frame, settings):
        raise RuntimeError(f"Failed to write screenshot to {full_path}")

    image = _to_bgr_image(frame)
    height, width = int(image.shape[0]), int(image.shape[1])
    preview_path = full_path
    preview_width, preview_height = width, height

    longest = max(width, height)
    if longest > settings.preview_max_width:
        scale = settings.preview_max_width / float(longest)
        preview_width = max(1, int(width * scale))
        preview_height = max(1, int(height * scale))
        preview = cv2.resize(image, (preview_width, preview_height), interpolation=cv2.INTER_AREA)
        preview_path = output_dir / f"pyfind_{stamp}_{token}_preview.{extension}"
        if not _write_image(preview_path, preview, settings):
            preview_path = full_path
            preview_width, preview_height = width, height

    return {
        "path": str(full_path),
        "preview_path": str(preview_path),
        "format": extension,
        "width": width,
        "height": height,
        "preview_width": preview_width,
        "preview_height": preview_height,
    }