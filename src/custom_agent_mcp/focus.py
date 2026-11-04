from __future__ import annotations

import sys
import time
from typing import Any

from loguru import logger

from .exceptions import FocusNotAcquiredError, PyFindRuntimeError

# The physical input gate only closes during an injection, so the foreground
# window can change while the agent observes or thinks. A keystroke sent to the
# wrong window is silently lost, so keyboard injection checks focus first.

FOREGROUND_SETTLE_TIMEOUT = 0.5
FOREGROUND_POLL_INTERVAL = 0.02


def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    return user32


_GA_ROOT = 2
_GA_ROOTOWNER = 3


def foreground_window() -> int:
    """Return the handle of the current foreground window (0 when unknown)."""
    _require_windows()
    return int(_user32().GetForegroundWindow() or 0)


def window_title(handle: int) -> str | None:
    """Return the title of ``handle``, or None when it cannot be read."""
    _require_windows()
    if handle == 0:
        return None
    try:
        import ctypes

        user32 = _user32()
        length = int(user32.GetWindowTextLengthW(handle))
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        if not user32.GetWindowTextW(handle, buffer, length + 1):
            return None
        return buffer.value or None
    except Exception:
        return None


def capture_foreground() -> tuple[int, str | None]:
    """Return the foreground handle and its title before focus is taken away.

    ``(0, None)`` means Windows did not report a foreground window, so there
    is nothing to restore later.
    """
    handle = foreground_window()
    return handle, window_title(handle) if handle else None


def owns_foreground(handle: int) -> bool:
    """True when ``handle`` owns the current foreground focus.

    A focused child or an owned dialog of the target counts as focus: keyboard
    input already goes where the caller intended.
    """
    if handle == 0:
        return False
    try:
        user32 = _user32()
        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground == 0:
            return False
        if foreground == handle:
            return True
        for flag in (_GA_ROOT, _GA_ROOTOWNER):
            if int(user32.GetAncestor(foreground, flag) or 0) == handle:
                return True
    except Exception:
        return False
    return False


def force_foreground(handle: int) -> bool:
    """Ask pyutmisc to bring ``handle`` to the foreground."""
    _require_windows()
    try:
        from pyutmisc.windows.foreground import force_foreground as _force
    except ImportError as exc:
        raise PyFindRuntimeError(
            "The 'pyutmisc' package is required to focus a window before "
            f"keyboard injection. Import error: {exc}"
        ) from exc
    return bool(_force(handle))


def _request_foreground(
    handle: int,
    *,
    title: str,
    timeout: float,
    reason: str,
) -> str | None:
    """Make ``handle`` the foreground window.

    Returns ``None`` on success, ``"missing"`` when the window no longer
    exists, or ``"timeout"`` when Windows does not report it as foreground
    before ``timeout`` expires.
    """
    if owns_foreground(handle):
        return None

    logger.info("Bringing window {!r} (hwnd={}) to the foreground ({})", title, handle, reason)
    try:
        requested = force_foreground(handle)
    except PyFindRuntimeError:
        raise
    except Exception as exc:
        raise FocusNotAcquiredError(
            f"Could not bring window {title!r} (hwnd={handle}) to the foreground: {exc}"
        ) from exc
    if not requested:
        return "missing"

    deadline = time.monotonic() + timeout
    while True:
        if owns_foreground(handle):
            return None
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(FOREGROUND_POLL_INTERVAL)


def ensure_foreground(
    handle: int,
    *,
    title: str,
    timeout: float = FOREGROUND_SETTLE_TIMEOUT,
) -> None:
    """Raise :class:`FocusNotAcquiredError` unless ``handle`` gets focus.

    Returns immediately when the window already has focus. Otherwise it calls
    pyutmisc's force_foreground and waits up to ``timeout`` for Windows to
    report the window as foreground.
    """
    if handle == 0:
        raise FocusNotAcquiredError("window_handle must not be 0")
    problem = _request_foreground(
        handle,
        title=title,
        timeout=timeout,
        reason="focus lost before the injection",
    )
    if problem == "missing":
        raise FocusNotAcquiredError(
            f"Window {title!r} (hwnd={handle}) no longer exists, so it cannot be focused "
            "for keyboard input."
        )
    if problem == "timeout":
        raise FocusNotAcquiredError(
            f"Window {title!r} (hwnd={handle}) could not be focused; the keyboard event "
            "was not sent. Windows may have refused SetForegroundWindow, or another "
            "application may be holding focus (a UAC prompt or the lock screen)."
        )


def restore_foreground(
    handle: int,
    *,
    title: str | None = None,
    timeout: float = FOREGROUND_SETTLE_TIMEOUT,
) -> None:
    """Put ``handle`` back in the foreground after an injection.

    Raises :class:`FocusNotAcquiredError` when the window no longer exists or
    Windows refuses to hand focus back. ``handle == 0`` is a no-op: nothing had
    focus before the injection, so there is nothing to restore.
    """
    _require_windows()
    if handle == 0:
        return
    resolved_title = title or window_title(handle) or f"hwnd {handle}"
    problem = _request_foreground(
        handle,
        title=resolved_title,
        timeout=timeout,
        reason="restoring focus after the injection",
    )
    if problem == "missing":
        raise FocusNotAcquiredError(
            f"Window {resolved_title!r} (hwnd={handle}) no longer exists, so focus "
            "cannot be restored to it after the injection."
        )
    if problem == "timeout":
        raise FocusNotAcquiredError(
            f"Focus was not restored to {resolved_title!r} (hwnd={handle}) after the "
            "injection; it is not the foreground window. Windows may have refused "
            "SetForegroundWindow, or another application may be holding focus."
        )


def _require_windows() -> None:
    if sys.platform != "win32":
        raise PyFindRuntimeError("PyFind is Windows-only; this MCP server requires Windows.")