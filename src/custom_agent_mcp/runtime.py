from __future__ import annotations

import asyncio
import atexit
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from loguru import logger

from . import beep
from . import focus as focus_support
from .exceptions import (
    FocusNotAcquiredError,
    InputDisabledError,
    Loge2NotInstalledError,
    PyFindRuntimeError,
    TemplateNotFoundError,
    WindowNotFoundError,
)
from .input_gate import InputGate
from .loge2_support import import_inject
from .models import Rect, serialize_templates
from .settings import Settings

__all__ = [
    "FocusNotAcquiredError",
    "InputDisabledError",
    "Loge2NotInstalledError",
    "PyFindRuntime",
    "PyFindRuntimeError",
    "TemplateNotFoundError",
    "WindowNotFoundError",
]

_TEMPLATE_SUFFIXES = (".png", ".webp")


def _require_windows() -> None:
    if sys.platform != "win32":
        raise PyFindRuntimeError("PyFind is Windows-only; this MCP server requires Windows.")


def _to_pyfind_rect(rect: dict[str, int]) -> dict[str, int]:
    """PyFind's cropper.Container expects left/top, not x/y."""
    return {
        "left": int(rect["x"]),
        "top": int(rect["y"]),
        "width": int(rect["width"]),
        "height": int(rect["height"]),
    }


def _cursor_position() -> dict[str, int]:
    _require_windows()
    import ctypes
    from ctypes import wintypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        error = ctypes.get_last_error()
        raise PyFindRuntimeError(f"GetCursorPos failed (WinError {error}).")
    return {"x": int(point.x), "y": int(point.y)}


class PyFindRuntime:
    """Owns the single long-lived PyFinder instance and all PyFind calls."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.input_gate = InputGate(settings, preload=True)
        self._lock = threading.RLock()
        self._input_lock = threading.RLock()
        self._finder: Any | None = None
        self._template_cache: dict[str, Any] = {}
        self._closed = False
        self._dpi_ready = False
        self._ensure_dpi()
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _ensure_dpi(self) -> None:
        if self._dpi_ready:
            return
        self._dpi_ready = True
        try:
            from PyFind import dpi

            dpi.setup_dpi()
        except Exception as exc:  # PyFind may not be installed yet.
            logger.debug("Could not set PyFind DPI awareness: {}", exc)

    def _create_finder(self) -> Any:
        _require_windows()
        try:
            from PyFind import capture_util, core, utils
        except ImportError as exc:
            raise PyFindRuntimeError(
                "PyFind is not installed. Install it from the autopy2 repository first "
                "(for example: python -m pip install -e F:\\all\\GitHub\\autopy2)."
            ) from exc

        utils.stop_event.clear()

        class HeadlessPyFinder(core.PyFinder):
            def screenshot_loop(self) -> None:
                while not utils.stop_event.is_set():
                    time.sleep(0.5)

            def display_loop(self) -> None:
                while not utils.stop_event.is_set():
                    time.sleep(0.5)

        logger.info("Creating PyFinder (max_workers={})", self.settings.max_workers)
        try:
            finder = HeadlessPyFinder(
                imgs_dir=None,
                threshold=self.settings.threshold,
                max_workers=self.settings.max_workers,
                cap_ctx=capture_util.ctx_mss(grayscale=False),
                display_cap=False,
                default_interval=0.1,
                default_timeout=self.settings.default_timeout,
                default_timeout_exception=False,
            )
        except Exception as exc:
            raise PyFindRuntimeError(f"Failed to initialize PyFinder: {exc}") from exc
        logger.info("PyFinder ready")
        return finder

    def _ensure_finder_locked(self) -> Any:
        if self._closed:
            raise PyFindRuntimeError("The PyFind runtime is closed.")
        if self._finder is None:
            self._finder = self._create_finder()
        return self._finder

    def close(self) -> None:
        try:
            self.input_gate.close()
        except Exception:
            logger.exception("Error while closing the input gate")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            finder = self._finder
            self._finder = None
            if finder is not None:
                try:
                    finder.stop()
                except Exception:
                    logger.exception("Error while stopping PyFinder")

    # ------------------------------------------------------------------
    # screen / window discovery
    # ------------------------------------------------------------------
    def virtual_screen(self) -> dict[str, int]:
        _require_windows()
        try:
            from mss import MSS
        except ImportError as exc:
            raise PyFindRuntimeError("The 'mss' package is required for screen capture.") from exc
        with MSS() as sct:
            monitor = sct.monitors[0]
        return {
            "x": int(monitor["left"]),
            "y": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }

    def list_monitors(self) -> dict[str, Any]:
        _require_windows()
        try:
            from screeninfo import get_monitors
        except ImportError as exc:
            raise PyFindRuntimeError("The 'screeninfo' package is required to list monitors.") from exc

        monitors = []
        for index, monitor in enumerate(get_monitors()):
            monitors.append(
                {
                    "index": index,
                    "x": int(monitor.x),
                    "y": int(monitor.y),
                    "width": int(monitor.width),
                    "height": int(monitor.height),
                    "is_primary": bool(monitor.is_primary),
                    "name": monitor.name,
                }
            )
        return {"virtual_desktop": self.virtual_screen(), "monitors": monitors}

    def _window_record(self, window: Any) -> dict[str, Any]:
        try:
            handle = int(window._hWnd)
        except Exception:
            handle = 0
        try:
            active = bool(window.isActive)
        except Exception:
            active = False
        try:
            minimized = bool(window.isMinimized)
        except Exception:
            minimized = False
        try:
            maximized = bool(window.isMaximized)
        except Exception:
            maximized = False
        try:
            visible = bool(window.visible)
        except Exception:
            visible = True
        return {
            "title": str(window.title),
            "handle": handle,
            "x": int(window.left),
            "y": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
            "is_active": active,
            "is_minimized": minimized,
            "is_maximized": maximized,
            "visible": visible,
        }

    def list_windows(self, query: str | None = None, visible_only: bool = True) -> dict[str, Any]:
        _require_windows()
        try:
            import pygetwindow as gw
        except ImportError as exc:
            raise PyFindRuntimeError("The 'pygetwindow' package is required to list windows.") from exc

        needle = (query or "").strip().lower()
        windows = []
        for window in gw.getAllWindows():
            try:
                record = self._window_record(window)
            except Exception:
                continue
            if not record["title"]:
                continue
            if visible_only and not record["visible"]:
                continue
            if needle and needle not in record["title"].lower():
                continue
            windows.append(record)

        windows.sort(key=lambda item: (not item["is_active"], item["title"].lower()))
        return {"count": len(windows), "windows": windows[:500]}

    def find_window(self, title: str) -> dict[str, Any]:
        _require_windows()
        needle = (title or "").strip()
        if not needle:
            raise WindowNotFoundError("window_title must not be empty")

        try:
            import pygetwindow as gw
        except ImportError as exc:
            raise PyFindRuntimeError("The 'pygetwindow' package is required to find windows.") from exc

        records = []
        for window in gw.getAllWindows():
            try:
                record = self._window_record(window)
            except Exception:
                continue
            if record["title"] and needle.lower() in record["title"].lower():
                records.append(record)

        if not records:
            all_titles = []
            for window in gw.getAllWindows():
                try:
                    title_value = str(window.title)
                except Exception:
                    continue
                if title_value and title_value not in all_titles:
                    all_titles.append(title_value)
            hint = ", ".join(repr(name) for name in all_titles[:10])
            raise WindowNotFoundError(
                f"No window title contains {needle!r}. Visible windows include: {hint or 'none'}"
            )

        records.sort(
            key=lambda item: (
                item["is_minimized"],
                not item["visible"],
                item["title"].lower() != needle.lower(),
                item["title"].lower(),
            )
        )
        return records[0]

    def _clamp_rect(self, rect: dict[str, int], virtual: dict[str, int]) -> dict[str, int]:
        left = max(rect["x"], virtual["x"])
        top = max(rect["y"], virtual["y"])
        right = min(rect["x"] + rect["width"], virtual["x"] + virtual["width"])
        bottom = min(rect["y"] + rect["height"], virtual["y"] + virtual["height"])
        if right <= left or bottom <= top:
            raise PyFindRuntimeError(
                f"Requested rectangle {rect} does not intersect the virtual desktop {virtual}."
            )
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def resolve_target(
        self,
        *,
        window_title: str | None = None,
        monitor: int | None = None,
        region: Rect | dict[str, int] | None = None,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        _require_windows()
        provided = sum(value is not None for value in (window_title, monitor, region))
        if provided > 1:
            raise ValueError("Use at most one of window_title, monitor, or region.")

        virtual = self.virtual_screen()
        requested: dict[str, int]
        target: dict[str, Any]

        if region is not None:
            rect_model = region if isinstance(region, Rect) else Rect.model_validate(region)
            requested = rect_model.as_dict()
            target = {"kind": "region"}
        elif monitor is not None:
            monitors = self.list_monitors()["monitors"]
            if not 0 <= int(monitor) < len(monitors):
                raise ValueError(
                    f"monitor index {monitor} is out of range; available: 0..{max(0, len(monitors) - 1)}"
                )
            selected = monitors[int(monitor)]
            requested = {
                "x": selected["x"],
                "y": selected["y"],
                "width": selected["width"],
                "height": selected["height"],
            }
            target = {"kind": "monitor", "monitor": int(monitor)}
        elif window_title is not None:
            window = self.find_window(window_title)
            if not window["visible"]:
                raise PyFindRuntimeError(
                    f"Window {window['title']!r} is hidden; MSS cannot capture hidden windows."
                )
            if window["is_minimized"]:
                raise PyFindRuntimeError(
                    f"Window {window['title']!r} is minimized; MSS cannot capture minimized windows."
                )
            requested = {
                "x": window["x"],
                "y": window["y"],
                "width": window["width"],
                "height": window["height"],
            }
            target = {
                "kind": "window",
                "window_title": window["title"],
                "handle": window["handle"],
            }
        else:
            requested = dict(virtual)
            target = {"kind": "screen"}

        effective = self._clamp_rect(requested, virtual)
        target["requested_rect"] = requested
        target["rect"] = effective
        return effective, target

    # ------------------------------------------------------------------
    # capture / OCR
    # ------------------------------------------------------------------
    def capture(
        self,
        rect: dict[str, int] | None = None,
        target: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        with self._lock:
            finder = self._ensure_finder_locked()
            frame_src = _to_pyfind_rect(rect) if rect is not None else None
            _frame, bgra, _is_new, region, timestamp, _info = finder.capture.capture_to_buffer(
                crop=None,
                frame_src=frame_src,
            )
            if bgra is None:
                raise PyFindRuntimeError("Screen capture returned no frame.")
            if rect is not None:
                region_rect = {
                    "x": int(rect["x"]),
                    "y": int(rect["y"]),
                    "width": int(rect["width"]),
                    "height": int(rect["height"]),
                }
            else:
                region_rect = {
                    "x": int(region.xywh[0]),
                    "y": int(region.xywh[1]),
                    "width": int(region.xywh[2]),
                    "height": int(region.xywh[3]),
                }
            metadata = {
                "region": region_rect,
                "target": target or {"kind": "screen"},
                "captured_at": timestamp,
            }
            return bgra, metadata

    def ocr(
        self,
        frame: Any,
        region: dict[str, int],
        *,
        lang: str = "en-US",
        preprocess: bool = False,
        include_words: bool = False,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        if max_lines < 1:
            raise ValueError("max_lines must be >= 1")
        with self._lock:
            _require_windows()
            try:
                import cv2
                from PyFind import windows_ocr
            except ImportError as exc:
                raise PyFindRuntimeError("PyFind's Windows OCR support is not available.") from exc

            image = frame
            if preprocess:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
                cleaned = windows_ocr.preprocess_for_ocr(gray)
                image = cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGRA)

            try:
                result = asyncio.run(windows_ocr.ocr_numpy_image(image, lang, is_rgb=False))
            except Exception as exc:
                raise PyFindRuntimeError(f"Windows OCR failed for language {lang!r}: {exc}") from exc

            offset_x = int(region["x"])
            offset_y = int(region["y"])
            lines = []
            total_words = 0
            for line in result.lines:
                words = []
                rects = []
                for word in line.words:
                    total_words += 1
                    text = str(getattr(word, "text", ""))
                    bounding = getattr(word, "bounding_rect", None)
                    if bounding is not None:
                        word_rect = {
                            "x": int(bounding.x) + offset_x,
                            "y": int(bounding.y) + offset_y,
                            "width": int(bounding.width),
                            "height": int(bounding.height),
                        }
                        rects.append(word_rect)
                    else:
                        word_rect = None
                    words.append({"text": text, "rect": word_rect})

                if rects:
                    left = min(item["x"] for item in rects)
                    top = min(item["y"] for item in rects)
                    right = max(item["x"] + item["width"] for item in rects)
                    bottom = max(item["y"] + item["height"] for item in rects)
                    line_rect = {"x": left, "y": top, "width": right - left, "height": bottom - top}
                else:
                    line_rect = None

                lines.append(
                    {
                        "text": " ".join(item["text"] for item in words),
                        "rect": line_rect,
                        "word_count": len(words),
                        "words": words if include_words else None,
                    }
                )

            truncated = len(lines) > max_lines
            if truncated:
                lines = lines[:max_lines]
            returned_words = sum(item["word_count"] for item in lines)

            return {
                "text": "\n".join(item["text"] for item in lines),
                "lines": lines,
                "line_count": len(lines),
                "word_count": returned_words,
                "total_word_count": total_words,
                "truncated": truncated,
                "lang": lang,
                "region": region,
            }

    # ------------------------------------------------------------------
    # template matching
    # ------------------------------------------------------------------
    def list_templates(self) -> dict[str, Any]:
        templates_dir = self.settings.templates_dir
        templates_dir.mkdir(parents=True, exist_ok=True)
        items = []
        paths = [
            path
            for suffix in _TEMPLATE_SUFFIXES
            for path in templates_dir.glob(f"*{suffix}")
        ]
        for path in sorted(paths):
            width = height = 0
            try:
                import cv2

                image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if image is not None:
                    height, width = int(image.shape[0]), int(image.shape[1])
            except Exception:
                pass
            items.append({"name": path.stem, "path": str(path), "width": width, "height": height})
        return {
            "templates_dir": str(templates_dir),
            "count": len(items),
            "templates": items[:500],
        }

    def reload_templates(self) -> dict[str, Any]:
        with self._lock:
            self._template_cache.clear()
            if self._finder is not None:
                try:
                    self._finder.proc_templates.cache_clear()
                except Exception:
                    pass
            return self.list_templates()

    def _load_template_locked(self, reference: str) -> Any:
        from PyFind import utils

        raw = Path(reference).expanduser()
        if raw.is_file():
            path = raw.resolve()
        else:
            candidates = [self.settings.templates_dir / reference]
            if raw.suffix.lower() not in _TEMPLATE_SUFFIXES:
                candidates.extend(
                    self.settings.templates_dir / f"{reference}{suffix}"
                    for suffix in _TEMPLATE_SUFFIXES
                )
            path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
            if path is None:
                available = ", ".join(
                    sorted(
                        {
                            candidate.stem
                            for suffix in _TEMPLATE_SUFFIXES
                            for candidate in self.settings.templates_dir.glob(f"*{suffix}")
                        }
                    )
                )
                raise TemplateNotFoundError(
                    f"Template {reference!r} was not found. Available templates: {available or 'none'}"
                )

        if path.suffix.lower() not in _TEMPLATE_SUFFIXES:
            raise ValueError(
                f"Only PNG or WebP templates are supported, got {path.name!r}."
            )

        key = str(path)
        if key not in self._template_cache:
            template = utils.ImageTempl(key, channels=4)
            if template.data is None:
                raise ValueError(f"Could not read template image: {path}")
            self._template_cache[key] = template
        return self._template_cache[key]

    def _find_locked(
        self,
        finder: Any,
        templates: Sequence[Any],
        rect: dict[str, int],
        *,
        threshold: float,
        timeout: float,
        disappear: bool,
        all_matches: bool,
        max_results: int,
        ocr_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0.0 and 1.0")
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        if timeout > self.settings.max_timeout:
            raise ValueError(f"timeout must be <= {self.settings.max_timeout} seconds")
        if disappear and timeout <= 0:
            raise ValueError("disappear=True requires timeout > 0")
        if max_results < 1:
            raise ValueError("max_results must be >= 1")

        old_threshold = finder.threshold
        finder.threshold = threshold
        # PyFind's look()/wait_to_go() feed this collection into an lru_cache
        # helper; a list is unhashable, so always pass a tuple.
        template_tuple = tuple(templates)
        try:
            kwargs: dict[str, Any] = {
                "frame_src": _to_pyfind_rect(rect),
                "ret": "all",
                "ocr_settings": ocr_settings or {},
            }
            if timeout > 0:
                kwargs.update({"timeout": timeout, "timeout_exc": False, "interval": 0.1})
                if disappear:
                    result = finder.wait_to_go(template_tuple, **kwargs)
                else:
                    result = finder.look(template_tuple, **kwargs)
            else:
                result = finder.find(template_tuple, **kwargs)
        finally:
            finder.threshold = old_threshold

        try:
            from PyFind import utils

            if utils.stop_event.is_set():
                raise PyFindRuntimeError(
                    "PyFind's global stop event is set; reconnect or restart the MCP server."
                )
        except ImportError:
            pass

        requested = [getattr(template, "name", None) for template in templates]
        timed_out = result is None
        serialized = serialize_templates(
            getattr(result, "results", []) if result is not None else [],
            all_matches=all_matches,
            max_results=max_results,
        )
        found = any(item["found"] for item in serialized)
        payload = {
            "found": found,
            "timed_out": timed_out,
            "disappeared": bool(disappear and result is not None),
            "still_present": bool(disappear and result is None),
            "requested": requested,
            "threshold": threshold,
            "templates": serialized,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
        }
        return payload

    def find_text(
        self,
        texts: Sequence[str],
        rect: dict[str, int],
        *,
        match_mode: str = "in",
        timeout: float = 0.0,
        disappear: bool = False,
        all_matches: bool = True,
        max_results: int = 50,
    ) -> dict[str, Any]:
        if not texts:
            raise ValueError("texts must contain at least one string")
        if any(not str(text).strip() for text in texts):
            raise ValueError("texts must not contain empty strings")
        if match_mode not in {"in", "=", "startswith", "endswith"}:
            raise ValueError("match_mode must be one of: in, =, startswith, endswith")
        with self._lock:
            finder = self._ensure_finder_locked()
            from PyFind import utils

            templates = [utils.StrTempl(text) for text in texts]
            return self._find_locked(
                finder,
                templates,
                rect,
                threshold=self.settings.threshold,
                timeout=timeout,
                disappear=disappear,
                all_matches=all_matches,
                max_results=max_results,
                ocr_settings={"match_mode": match_mode},
            )

    def find_images(
        self,
        references: Sequence[str],
        rect: dict[str, int],
        *,
        threshold: float,
        timeout: float = 0.0,
        disappear: bool = False,
        all_matches: bool = True,
        max_results: int = 50,
    ) -> dict[str, Any]:
        if not references:
            raise ValueError("templates must contain at least one template")
        with self._lock:
            finder = self._ensure_finder_locked()
            templates = [self._load_template_locked(reference) for reference in references]
            for template in templates:
                data = getattr(template, "data", None)
                if data is None:
                    continue
                height, width = int(data.shape[0]), int(data.shape[1])
                if width > rect["width"] or height > rect["height"]:
                    raise ValueError(
                        f"Template {getattr(template, 'name', '?')!r} is {width}x{height}, larger than "
                        f"the capture region {rect['width']}x{rect['height']}."
                    )
            return self._find_locked(
                finder,
                templates,
                rect,
                threshold=threshold,
                timeout=timeout,
                disappear=disappear,
                all_matches=all_matches,
                max_results=max_results,
            )

    # ------------------------------------------------------------------
    # mouse / keyboard
    # ------------------------------------------------------------------
    _CLICK_BUTTONS = frozenset(
        {"left", "right", "middle", "x1", "x2", "back", "forward", "move"}
    )
    _DRAG_BUTTONS = frozenset({"left", "right", "middle", "x1", "x2", "back", "forward"})
    _WHEEL_UNIT = 120

    def _require_input(self) -> None:
        if not self.settings.allow_input:
            raise InputDisabledError(
                "Input tools are disabled. Set PYFIND_MCP_ALLOW_INPUT=true and restart the MCP server."
            )

    def _inject(self) -> Any:
        self._require_input()
        return import_inject()

    @contextmanager
    def _gated_injection(
        self,
        *,
        keyboard: bool,
        mouse: bool,
    ) -> Iterator[None]:
        """Block only for the duration of one injection and release in finally."""
        with self._input_lock:
            lease = None
            if self.settings.allow_input_gate:
                lease = self.input_gate.begin_injection(keyboard=keyboard, mouse=mouse)
            try:
                if self.settings.beep:
                    beep.injection_cue()
                yield
            finally:
                if lease is not None:
                    self.input_gate.end_injection(**lease)

    @contextmanager
    def _focused_gated_injection(
        self,
        *,
        keyboard: bool,
        mouse: bool,
        window_title: str | None,
    ) -> Iterator[dict[str, Any]]:
        """Focus the named window, then run one gated injection.

        Input is already blocked for the injection when focus must be restored,
        so a delayed focus change cannot race the keystroke. If the window
        cannot be focused, :class:`FocusNotAcquiredError` is raised and nothing
        is injected.
        """
        with self._input_lock:
            window: dict[str, Any] | None = None
            recovered = False
            lease = None
            try:
                if window_title is not None:
                    window = self.find_window(window_title)
                    handle = int(window["handle"])
                    if not focus_support.owns_foreground(handle):
                        if self.settings.allow_input_gate:
                            lease = self.input_gate.begin_injection(
                                keyboard=keyboard, mouse=mouse
                            )
                        else:
                            logger.warning(
                                "Focusing {!r} without the input gate: "
                                "enable PYFIND_MCP_ALLOW_INPUT_GATE=true to prevent "
                                "focus races.",
                                window["title"],
                            )
                        focus_support.ensure_foreground(
                            handle, title=window["title"]
                        )
                        recovered = True

                with self._gated_injection(keyboard=keyboard, mouse=mouse):
                    yield {
                        "window_title": window["title"] if window else None,
                        "window_handle": int(window["handle"]) if window else None,
                        "focus_recovered": recovered,
                    }
            finally:
                if lease is not None:
                    self.input_gate.end_injection(**lease)

    def focus_window(
        self,
        window_title: str,
        *,
        timeout: float = focus_support.FOREGROUND_SETTLE_TIMEOUT,
    ) -> dict[str, Any]:
        """Bring a window to the foreground and verify that it has focus."""
        self._require_input()
        if not 0 < timeout <= 5.0:
            raise ValueError("timeout must be greater than 0 and at most 5 seconds")
        window = self.find_window(window_title)
        handle = int(window["handle"])
        was_focused = focus_support.owns_foreground(handle)
        focus_support.ensure_foreground(
            handle, title=window["title"], timeout=timeout
        )
        return {
            "ok": True,
            "window_title": window["title"],
            "window_handle": handle,
            "was_focused": was_focused,
            "focused": True,
        }

    @staticmethod
    def _validate_modifiers(modifiers: Sequence[str] | None) -> list[str]:
        if not modifiers:
            return []
        cleaned = []
        for modifier in modifiers:
            name = str(modifier).strip().lower()
            if not name:
                raise ValueError("modifiers must not contain empty strings")
            cleaned.append(name)
        return cleaned

    def _validate_point(self, x: int, y: int) -> None:
        virtual = self.virtual_screen()
        if not (virtual["x"] <= x < virtual["x"] + virtual["width"]):
            raise ValueError(f"x={x} is outside the virtual desktop ({virtual['x']}..{virtual['x'] + virtual['width'] - 1})")
        if not (virtual["y"] <= y < virtual["y"] + virtual["height"]):
            raise ValueError(f"y={y} is outside the virtual desktop ({virtual['y']}..{virtual['y'] + virtual['height'] - 1})")

    @staticmethod
    def _release_modifiers(inject: Any, pressed: Sequence[str]) -> None:
        for modifier in reversed(pressed):
            try:
                inject.key_up(modifier)
            except Exception:
                logger.exception("Could not release modifier {!r}", modifier)

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.0,
        modifiers: Sequence[str] | None = None,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        if button not in self._CLICK_BUTTONS:
            raise ValueError(
                "button must be one of " + ", ".join(sorted(self._CLICK_BUTTONS))
            )
        if not 1 <= clicks <= 5:
            raise ValueError("clicks must be between 1 and 5")
        if not 0.0 <= interval <= 5.0:
            raise ValueError("interval must be between 0 and 5 seconds")
        self._validate_point(x, y)

        inject = self._inject()
        mods = self._validate_modifiers(modifiers)
        pressed: list[str] = []
        with self._focused_gated_injection(
            keyboard=bool(mods), mouse=True, window_title=window_title
        ) as focused:
            try:
                for modifier in mods:
                    inject.key_down(modifier)
                    pressed.append(modifier)
                if button == "move":
                    inject.mouse_move_to(x, y)
                else:
                    inject.mouse_click_at(
                        x,
                        y,
                        button=button,
                        count=clicks,
                        interval=interval,
                    )
            finally:
                self._release_modifiers(inject, pressed)
        return {
            "ok": True,
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "modifiers": mods,
            **focused,
        }

    def move_mouse(self, x: int, y: int) -> dict[str, Any]:
        self._require_input()
        self._validate_point(x, y)
        inject = self._inject()
        with self._gated_injection(keyboard=False, mouse=True):
            inject.mouse_move_to(x, y)
        return {"ok": True, "x": x, "y": y}

    def move_mouse_by(self, dx: int, dy: int) -> dict[str, Any]:
        self._require_input()
        position = _cursor_position()
        x = position["x"] + int(dx)
        y = position["y"] + int(dy)
        self._validate_point(x, y)
        inject = self._inject()
        with self._gated_injection(keyboard=False, mouse=True):
            inject.mouse_move_to(x, y)
        return {"ok": True, "dx": dx, "dy": dy, "x": x, "y": y}

    def drag(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        *,
        button: str = "left",
        duration: float = 0.2,
        modifiers: Sequence[str] | None = None,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        if button not in self._DRAG_BUTTONS:
            raise ValueError(
                "button must be one of " + ", ".join(sorted(self._DRAG_BUTTONS))
            )
        if not 0.0 <= duration <= 10.0:
            raise ValueError("duration must be between 0 and 10 seconds")
        self._validate_point(from_x, from_y)
        self._validate_point(to_x, to_y)

        inject = self._inject()
        mods = self._validate_modifiers(modifiers)
        pressed: list[str] = []
        with self._focused_gated_injection(
            keyboard=bool(mods), mouse=True, window_title=window_title
        ) as focused:
            try:
                for modifier in mods:
                    inject.key_down(modifier)
                    pressed.append(modifier)
                inject.mouse_move_to(from_x, from_y)
                inject.mouse_down(button)
                try:
                    if duration <= 0:
                        inject.mouse_move_to(to_x, to_y)
                    else:
                        steps = max(1, min(200, int(round(duration / 0.02))))
                        for step in range(1, steps + 1):
                            x = round(from_x + (to_x - from_x) * step / steps)
                            y = round(from_y + (to_y - from_y) * step / steps)
                            inject.mouse_move_to(x, y)
                            time.sleep(duration / steps)
                finally:
                    try:
                        inject.mouse_up(button)
                    except Exception:
                        logger.exception("Could not release mouse button {!r} after drag", button)
            finally:
                self._release_modifiers(inject, pressed)
        return {
            "ok": True,
            "from": {"x": from_x, "y": from_y},
            "to": {"x": to_x, "y": to_y},
            "button": button,
            "duration": duration,
            "modifiers": mods,
            **focused,
        }

    def type_text(
        self,
        text: str,
        *,
        interval: float = 0.0,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        if not text:
            raise ValueError("text must not be empty")
        if not 0.0 <= interval <= 5.0:
            raise ValueError("interval must be between 0 and 5 seconds")
        inject = self._inject()
        with self._focused_gated_injection(
            keyboard=True, mouse=False, window_title=window_title
        ) as focused:
            if interval <= 0:
                inject.type_text(text)
            else:
                for character in text:
                    inject.type_text(character)
                    time.sleep(interval)
        return {"ok": True, "characters": len(text), **focused}

    def press_key(
        self,
        key: str,
        *,
        hold: float = 0.02,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        parts = [part.strip() for part in key.split("+") if part.strip()]
        if not parts:
            raise ValueError("key must not be empty")
        if not 0.0 <= hold <= 5.0:
            raise ValueError("hold must be between 0 and 5 seconds")
        inject = self._inject()
        with self._focused_gated_injection(
            keyboard=True, mouse=False, window_title=window_title
        ) as focused:
            if len(parts) > 1:
                inject.combo(parts, hold=hold)
            else:
                inject.tap(parts[0], hold=hold)
        return {"ok": True, "keys": parts, "hold": hold, **focused}

    def scroll(
        self,
        clicks: int,
        *,
        x: int | None = None,
        y: int | None = None,
        horizontal: bool = False,
        window_title: str | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        if clicks == 0:
            raise ValueError("clicks must not be zero")
        if (x is None) != (y is None):
            raise ValueError("provide both x and y, or neither")
        if x is not None and y is not None:
            self._validate_point(x, y)
        inject = self._inject()
        with self._focused_gated_injection(
            keyboard=False, mouse=True, window_title=window_title
        ) as focused:
            if x is not None and y is not None:
                inject.mouse_move_to(x, y)
            delta = int(clicks) * self._WHEEL_UNIT
            inject.mouse_scroll(delta, horizontal=horizontal)
        return {
            "ok": True,
            "clicks": clicks,
            "delta": delta,
            "horizontal": horizontal,
            "x": x,
            "y": y,
            **focused,
        }

    def cursor_position(self) -> dict[str, Any]:
        self._require_input()
        return {**_cursor_position(), "ok": True}

    # ------------------------------------------------------------------
    # physical input gate
    # ------------------------------------------------------------------
    def begin_input_task(self) -> dict[str, Any]:
        self._require_input()
        with self._input_lock:
            payload = self.input_gate.begin_task()
            if payload.get("task_started") and self.settings.beep:
                beep.task_start_cue()
            return payload

    def end_input_task(self) -> dict[str, Any]:
        # Ending a task is a safety path: never hide it behind allow_input.
        with self._input_lock:
            payload = self.input_gate.end_task()
            if payload.get("task_ended") and self.settings.beep:
                beep.task_end_cue()
            return payload

    def block_user_input(
        self,
        *,
        keyboard: bool = True,
        mouse: bool = True,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        self._require_input()
        with self._input_lock:
            payload = self.input_gate.arm(keyboard=keyboard, mouse=mouse, ttl=ttl)
            if self.settings.beep:
                beep.task_start_cue()
            return payload

    def unblock_user_input(
        self,
        *,
        keyboard: bool = True,
        mouse: bool = True,
    ) -> dict[str, Any]:
        # Release must never be hidden behind allow_input: it is the safety path.
        with self._input_lock:
            was_blocked = self.input_gate.status().get("any_blocked", False)
            payload = self.input_gate.release(
                keyboard=keyboard, mouse=mouse, reason="manual"
            )
            if (
                was_blocked
                and not payload.get("any_blocked", False)
                and self.settings.beep
            ):
                beep.task_end_cue()
            return payload

    def input_block_status(self) -> dict[str, Any]:
        return self.input_gate.status()
