from __future__ import annotations

import importlib.util
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from loguru import logger

from . import beep
from .exceptions import Loge2NotInstalledError, PyFindRuntimeError
from .loge2_support import INSTALL_HINT, import_local
from .settings import Settings


class GateBackend(Protocol):
    """Operations the input gate needs from the hook backend.

    The protocol exists so the gate logic can be unit-tested with a fake.
    """

    def validate_hotkey(self, hotkey: str) -> None: ...

    def block_keyboard(self) -> None: ...

    def unblock_keyboard(self) -> None: ...

    def keyboard_state(self) -> tuple[bool, bool]: ...

    def block_mouse(self) -> None: ...

    def unblock_mouse(self) -> None: ...

    def mouse_state(self) -> tuple[bool, bool]: ...

    def hotkey_pressed(self, keys: str) -> bool: ...

    def install_escape(self, callback: Callable[[Any], object], hotkey: str) -> None: ...

    def remove_escape(self) -> None: ...

    def stop(self) -> None: ...


class Loge2GateBackend:
    """In-process loge2 hooks, with errors normalized for MCP tools."""

    def __init__(self) -> None:
        modules = import_local()
        self._keyboard = modules.keyboard
        self._mouse = modules.mouse

    def validate_hotkey(self, hotkey: str) -> None:
        try:
            from loge2.common import combo_to_scan_codes

            combo_to_scan_codes(hotkey)
        except Exception as exc:
            raise PyFindRuntimeError(
                f"Invalid input gate hotkey {hotkey!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # keyboard
    # ------------------------------------------------------------------
    def block_keyboard(self) -> None:
        try:
            self._keyboard.block_all_keys()
        except Exception as exc:
            raise PyFindRuntimeError(
                f"Could not install the keyboard block: {exc}"
            ) from exc

    def unblock_keyboard(self) -> None:
        self._keyboard.unblock_all_keys()

    def keyboard_state(self) -> tuple[bool, bool]:
        instance = getattr(self._keyboard, "_keyboard", None)
        if instance is None:
            return False, False
        try:
            blocked = bool(instance.is_block_all())
        except Exception:
            blocked = False
        try:
            running = bool(instance.is_running)
        except Exception:
            running = False
        return blocked, running

    # ------------------------------------------------------------------
    # mouse
    # ------------------------------------------------------------------
    def block_mouse(self) -> None:
        try:
            self._mouse.block_all()
        except Exception as exc:
            raise PyFindRuntimeError(
                f"Could not install the mouse block: {exc}"
            ) from exc

    def unblock_mouse(self) -> None:
        self._mouse.unblock_all()

    def mouse_state(self) -> tuple[bool, bool]:
        try:
            return bool(self._mouse.is_blocked()), bool(self._mouse.is_running())
        except Exception:
            return False, False

    # ------------------------------------------------------------------
    # emergency hotkey
    # ------------------------------------------------------------------
    def hotkey_pressed(self, keys: str) -> bool:
        return bool(self._keyboard.is_pressed(keys))

    def install_escape(
        self,
        callback: Callable[[Any], object],
        hotkey: str,
    ) -> None:
        try:
            self._keyboard.hook(callback)
            self._keyboard.set_blocked_combos([hotkey])
        except Exception as exc:
            try:
                self._keyboard.unhook()
            except Exception:
                pass
            raise PyFindRuntimeError(
                f"Could not install the input gate hotkey: {exc}"
            ) from exc

    def remove_escape(self) -> None:
        # Do not let set_blocked_combos() create a hook just to clear rules.
        if getattr(self._keyboard, "_keyboard", None) is None:
            return
        try:
            self._keyboard.unhook()
        except Exception:
            pass
        try:
            self._keyboard.set_blocked_combos([])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # shutdown
    # ------------------------------------------------------------------
    def stop(self) -> None:
        for action in (
            self._keyboard.unblock_all_keys,
            self._mouse.unblock_all,
            self._keyboard.unhook,
        ):
            try:
                action()
            except Exception:
                pass

        cleanup = getattr(self._keyboard, "_cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass

        try:
            self._mouse.stop()
        except Exception:
            pass


class _EmergencyRelease:
    """Keyboard hook callback that releases the gate on a physical hotkey."""

    def __init__(
        self,
        gate: "InputGate",
        is_pressed: Callable[[str], bool],
        hotkey: str,
    ) -> None:
        parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
        self._gate = gate
        self._is_pressed = is_pressed
        self._trigger = parts[-1]
        self._modifiers = "+".join(parts[:-1])

    def __call__(self, event: Any) -> None:
        if not getattr(event, "is_key_down", False):
            return
        # Injected events must never release the gate; only the physical user may.
        if getattr(event, "is_passthrough", False):
            return
        if getattr(event, "name", None) != self._trigger:
            return
        if self._modifiers and not self._is_pressed(self._modifiers):
            return
        self._gate.trigger_emergency_release()


class InputGate:
    """Global physical-input gate with an optional TTL and emergency hotkey.

    By default the gate stays closed until it is released explicitly, the
    configured physical hotkey is pressed, or the server process exits. Passing
    ``ttl`` arms a timer that releases the gate automatically.
    """

    def __init__(
        self,
        settings: Settings,
        backend: GateBackend | None = None,
        preload: bool = False,
    ) -> None:
        self._settings = settings
        self._backend = backend
        # Preloading imports the hook modules at server start instead of in the
        # middle of an injection, so focus work is not interrupted by an import.
        self._preload_backend = preload and backend is None
        self._lock = threading.RLock()
        self._closed = False
        self._task_active = False
        self._task_mode: str | None = None
        self._timer: threading.Timer | None = None
        self._deadline: float | None = None
        self._ttl: float | None = None
        self._generation = 0
        self._escape_callback: _EmergencyRelease | None = None
        self._last_release_reason: str | None = None
        self._last_release_at: str | None = None
        if self._preload_backend:
            self.preload()

    @property
    def backend(self) -> GateBackend | None:
        """The installed hook backend, or None when it is not available yet."""
        return self._backend

    def preload(self) -> bool:
        """Create the hook backend now. Returns False when loge2 is missing."""
        if not self._preload_backend:
            return self._backend is not None
        try:
            with self._lock:
                self._ensure_backend_locked()
        except PyFindRuntimeError as exc:
            logger.warning("Input gate backend is not available yet: {}", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def arm(
        self,
        *,
        keyboard: bool = True,
        mouse: bool = True,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        if not keyboard and not mouse:
            raise ValueError("Select at least one of keyboard or mouse.")
        if ttl is not None and not 0 < ttl <= self._settings.input_gate_max_ttl:
            raise ValueError(
                f"ttl must be greater than 0 and at most "
                f"{self._settings.input_gate_max_ttl:g} seconds"
            )

        hotkey = self._settings.input_gate_hotkey
        with self._lock:
            if self._closed:
                raise PyFindRuntimeError("The input gate is closed.")
            backend = self._ensure_backend_locked()
            if hotkey:
                backend.validate_hotkey(hotkey)

            armed: list[str] = []
            try:
                # Arm the escape path before closing any gate, so there is never
                # a moment where physical input is blocked without a way out.
                if hotkey:
                    self._install_escape_locked(backend, hotkey)
                if keyboard:
                    backend.block_keyboard()
                    armed.append("keyboard")
                if mouse:
                    backend.block_mouse()
                    armed.append("mouse")
            except Exception:
                for device in reversed(armed):
                    try:
                        if device == "keyboard":
                            backend.unblock_keyboard()
                        else:
                            backend.unblock_mouse()
                    except Exception:
                        logger.exception("Failed to roll back the %s block", device)
                try:
                    self._remove_escape_locked(backend)
                except Exception:
                    logger.exception("Failed to remove the input gate hotkey")
                raise

            self._set_deadline_locked(ttl)
            self._last_release_reason = None
            self._last_release_at = None
            if not hotkey and ttl is None:
                logger.warning(
                    "Input gate armed without an emergency hotkey or TTL; "
                    "use unblock_user_input or end the MCP process to recover."
                )
            logger.info(
                "Input gate armed (keyboard={}, mouse={}, ttl={})",
                keyboard,
                mouse,
                ttl,
            )
            return self._status_locked(backend)

    def release(
        self,
        *,
        keyboard: bool = True,
        mouse: bool = True,
        reason: str = "manual",
        log: bool = True,
    ) -> dict[str, Any]:
        if not keyboard and not mouse:
            raise ValueError("Select at least one of keyboard or mouse.")

        with self._lock:
            if self._closed:
                return self._status_locked(self._backend)
            backend = self._backend
            if backend is None:
                self._last_release_reason = reason
                self._last_release_at = _now_iso()
                available = _loge2_available()
                return self._status_locked(
                    None,
                    available=available,
                    error=None if available else INSTALL_HINT,
                )

            errors: list[str] = []
            if keyboard:
                try:
                    backend.unblock_keyboard()
                except Exception as exc:
                    errors.append(f"keyboard: {exc}")
            if mouse:
                try:
                    backend.unblock_mouse()
                except Exception as exc:
                    errors.append(f"mouse: {exc}")

            if not self._any_blocked(backend):
                self._remove_escape_locked(backend)
                self._cancel_timer_locked()

            self._last_release_reason = reason
            self._last_release_at = _now_iso()
            if log:
                logger.info(
                    "Input gate released (keyboard={}, mouse={}, reason={})",
                    keyboard,
                    mouse,
                    reason,
                )
            if errors:
                raise PyFindRuntimeError("; ".join(errors))
            return self._status_locked(backend)

    def begin_task(self, *, mode: str | None = None) -> dict[str, Any]:
        """Start a gated input task.

        ``mode="injection"`` (the default) keeps physical input available
        between calls and blocks only for each injection. ``mode="task"``
        blocks keyboard and mouse immediately and keeps them blocked until
        :meth:`end_task`, the emergency hotkey, or shutdown.
        """
        resolved = (mode or self._settings.input_gate_mode).strip().lower()
        if resolved not in {"injection", "task"}:
            raise ValueError("mode must be 'injection' or 'task'")

        hotkey = self._settings.input_gate_hotkey
        with self._lock:
            if self._closed:
                raise PyFindRuntimeError("The input gate is closed.")
            backend = self._ensure_backend_locked()
            started = not self._task_active
            if started:
                if resolved == "task":
                    # arm() installs the escape hotkey before closing the gate
                    # and rolls back cleanly if either device fails.
                    self.arm(keyboard=True, mouse=True)
                elif hotkey:
                    backend.validate_hotkey(hotkey)
                    self._install_escape_locked(backend, hotkey)
                self._task_active = True
                self._task_mode = resolved
                logger.info(
                    "Input task started in {} mode{}",
                    resolved,
                    " (input blocked until end_input_task)"
                    if resolved == "task"
                    else " (input blocks only during each injection)",
                )
            payload = self._status_locked(backend)
            payload["task_started"] = started
            return payload

    def end_task(self) -> dict[str, Any]:
        """End the current task and release any remaining block."""
        with self._lock:
            was_active = self._task_active
            self._task_active = False
            self._task_mode = None
            payload = self.release(reason="task_end")
            payload["task_ended"] = was_active
            return payload

    def begin_injection(
        self,
        *,
        keyboard: bool = True,
        mouse: bool = True,
    ) -> dict[str, bool]:
        """Block the requested devices for one injection.

        Returns a lease describing what this call newly blocked. Devices that
        were already blocked (a manual block or another injection) are not
        leased, so :meth:`end_injection` will not open them.
        """
        if not keyboard and not mouse:
            raise ValueError("Select at least one of keyboard or mouse.")

        hotkey = self._settings.input_gate_hotkey
        with self._lock:
            if self._closed:
                raise PyFindRuntimeError("The input gate is closed.")
            backend = self._ensure_backend_locked()

            escape_owned = False
            if self._escape_callback is None and hotkey:
                backend.validate_hotkey(hotkey)
                self._install_escape_locked(backend, hotkey)
                escape_owned = True

            own_keyboard = keyboard and not self._device_blocked(backend, "keyboard")
            own_mouse = mouse and not self._device_blocked(backend, "mouse")

            armed: list[str] = []
            try:
                # The escape path is installed before any block, so there is
                # never a blocked moment without a physical way out.
                if keyboard:
                    backend.block_keyboard()
                    armed.append("keyboard")
                if mouse:
                    backend.block_mouse()
                    armed.append("mouse")
            except Exception:
                for device in reversed(armed):
                    owned = own_keyboard if device == "keyboard" else own_mouse
                    if not owned:
                        continue
                    try:
                        if device == "keyboard":
                            backend.unblock_keyboard()
                        else:
                            backend.unblock_mouse()
                    except Exception:
                        logger.exception("Failed to roll back the %s block", device)
                if escape_owned:
                    try:
                        self._remove_escape_locked(backend)
                    except Exception:
                        logger.exception("Failed to remove the input gate hotkey")
                raise

            return {
                "keyboard": own_keyboard,
                "mouse": own_mouse,
                "escape": escape_owned,
            }

    def end_injection(
        self,
        *,
        keyboard: bool = False,
        mouse: bool = False,
        escape: bool = False,
    ) -> None:
        """Release the devices leased by :meth:`begin_injection`."""
        with self._lock:
            backend = self._backend
            if backend is None:
                return

            errors: list[str] = []
            if keyboard:
                try:
                    backend.unblock_keyboard()
                except Exception as exc:
                    errors.append(f"keyboard: {exc}")
            if mouse:
                try:
                    backend.unblock_mouse()
                except Exception as exc:
                    errors.append(f"mouse: {exc}")

            if (
                escape
                and not self._task_active
                and not self._any_blocked(backend)
            ):
                try:
                    self._remove_escape_locked(backend)
                except Exception as exc:
                    errors.append(f"hotkey: {exc}")

            if errors:
                logger.warning("Could not fully release an injection: {}", "; ".join(errors))

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._backend is None:
                available = _loge2_available()
                if self._closed or not available:
                    return self._status_locked(
                        None,
                        available=available,
                        error=None if available else INSTALL_HINT,
                    )
                try:
                    self._backend = Loge2GateBackend()
                except Loge2NotInstalledError as exc:
                    return self._status_locked(None, error=str(exc))
            return self._status_locked(self._backend)

    def trigger_emergency_release(self) -> None:
        logger.warning("Input gate emergency hotkey pressed; releasing.")
        with self._lock:
            self._task_active = False
            self._task_mode = None
        try:
            # This runs on the keyboard hook thread; avoid a second log write.
            self.release(reason="emergency_hotkey", log=False)
        except Exception:
            logger.exception("Failed to release the input gate from the hotkey")
        else:
            if self._settings.beep:
                beep.task_end_cue()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._task_active = False
            self._task_mode = None
            self._cancel_timer_locked()
            backend = self._backend
            self._escape_callback = None
            self._last_release_reason = "shutdown"
            self._last_release_at = _now_iso()

        if backend is not None:
            try:
                backend.stop()
            except Exception:
                logger.exception("Error while stopping the input gate hooks")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _ensure_backend_locked(self) -> GateBackend:
        if self._closed:
            raise PyFindRuntimeError("The input gate is closed.")
        if self._backend is None:
            self._backend = Loge2GateBackend()
        return self._backend

    def _install_escape_locked(self, backend: GateBackend, hotkey: str) -> None:
        if self._escape_callback is not None:
            return
        callback = _EmergencyRelease(self, backend.hotkey_pressed, hotkey)
        backend.install_escape(callback, hotkey)
        self._escape_callback = callback

    def _remove_escape_locked(self, backend: GateBackend) -> None:
        if self._escape_callback is None:
            return
        try:
            backend.remove_escape()
        finally:
            self._escape_callback = None

    def _any_blocked(self, backend: GateBackend) -> bool:
        try:
            keyboard_blocked, _ = backend.keyboard_state()
        except Exception:
            keyboard_blocked = False
        try:
            mouse_blocked, _ = backend.mouse_state()
        except Exception:
            mouse_blocked = False
        return bool(keyboard_blocked or mouse_blocked)

    @staticmethod
    def _device_blocked(backend: GateBackend, device: str) -> bool:
        try:
            if device == "keyboard":
                return bool(backend.keyboard_state()[0])
            return bool(backend.mouse_state()[0])
        except Exception:
            return False

    def _set_deadline_locked(self, ttl: float | None) -> None:
        self._cancel_timer_locked()
        self._ttl = ttl
        if ttl is None:
            return
        self._generation += 1
        generation = self._generation
        self._deadline = time.monotonic() + ttl
        timer = threading.Timer(ttl, self._on_timer, args=(generation,))
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._generation += 1
        self._deadline = None
        self._ttl = None

    def _on_timer(self, generation: int) -> None:
        with self._lock:
            if self._closed or generation != self._generation:
                return
            logger.warning("Input gate TTL expired; releasing keyboard and mouse.")
            try:
                self.release(reason="ttl")
            except Exception:
                logger.exception("Failed to auto-release the input gate")
            else:
                if self._settings.beep:
                    beep.task_end_cue()

    def _status_locked(
        self,
        backend: GateBackend | None,
        *,
        available: bool | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if available is None:
            available = backend is not None
        keyboard_blocked = keyboard_running = False
        mouse_blocked = mouse_running = False
        if backend is not None:
            try:
                keyboard_blocked, keyboard_running = backend.keyboard_state()
            except Exception:
                pass
            try:
                mouse_blocked, mouse_running = backend.mouse_state()
            except Exception:
                pass

        expires_in = None
        expires_at = None
        if self._deadline is not None:
            expires_in = max(0.0, round(self._deadline - time.monotonic(), 1))
            expires_at = _iso_after(expires_in)

        payload: dict[str, Any] = {
            "available": bool(available),
            "closed": self._closed,
            "task_active": self._task_active,
            "task_mode": self._task_mode,
            "mode": self._settings.input_gate_mode,
            "keyboard_blocked": bool(keyboard_blocked),
            "mouse_blocked": bool(mouse_blocked),
            "keyboard_hook_running": bool(keyboard_running),
            "mouse_hook_running": bool(mouse_running),
            "any_blocked": bool(keyboard_blocked or mouse_blocked),
            "ttl": self._ttl,
            "expires_in": expires_in,
            "expires_at": expires_at,
            "hotkey": self._settings.input_gate_hotkey or None,
            "max_ttl": self._settings.input_gate_max_ttl,
            "last_release_reason": self._last_release_reason,
            "last_release_at": self._last_release_at,
        }
        if error is not None:
            payload["error"] = error
        return payload


def _loge2_available() -> bool:
    try:
        return importlib.util.find_spec("loge2") is not None
    except Exception:
        return False


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_after(seconds: float) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )