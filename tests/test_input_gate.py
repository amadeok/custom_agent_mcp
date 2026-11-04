from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from custom_agent_mcp.exceptions import PyFindRuntimeError
from custom_agent_mcp.input_gate import InputGate
from custom_agent_mcp.settings import Settings


class FakeGateBackend:
    def __init__(self) -> None:
        self.keyboard_blocked = False
        self.mouse_blocked = False
        self.keyboard_running = False
        self.mouse_running = False
        self.hotkey_valid = True
        self.hotkey: str | None = None
        self.escape_callback = None
        self.calls: list[tuple] = []
        self.pressed = False
        self.fail_mouse_block = False

    def validate_hotkey(self, hotkey: str) -> None:
        self.calls.append(("validate_hotkey", hotkey))
        if not self.hotkey_valid:
            raise PyFindRuntimeError("bad hotkey")

    def block_keyboard(self) -> None:
        self.calls.append(("block_keyboard",))
        self.keyboard_blocked = True
        self.keyboard_running = True

    def unblock_keyboard(self) -> None:
        self.calls.append(("unblock_keyboard",))
        self.keyboard_blocked = False

    def keyboard_state(self) -> tuple[bool, bool]:
        return self.keyboard_blocked, self.keyboard_running

    def block_mouse(self) -> None:
        self.calls.append(("block_mouse",))
        if self.fail_mouse_block:
            raise PyFindRuntimeError("mouse hook failed")
        self.mouse_blocked = True
        self.mouse_running = True

    def unblock_mouse(self) -> None:
        self.calls.append(("unblock_mouse",))
        self.mouse_blocked = False

    def mouse_state(self) -> tuple[bool, bool]:
        return self.mouse_blocked, self.mouse_running

    def hotkey_pressed(self, keys: str) -> bool:
        self.calls.append(("hotkey_pressed", keys))
        return self.pressed

    def install_escape(self, callback, hotkey: str) -> None:
        self.calls.append(("install_escape", hotkey))
        self.hotkey = hotkey
        self.escape_callback = callback

    def remove_escape(self) -> None:
        self.calls.append(("remove_escape",))
        self.hotkey = None
        self.escape_callback = None

    def stop(self) -> None:
        self.calls.append(("stop",))
        self.keyboard_blocked = False
        self.mouse_blocked = False


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "templates_dir": tmp_path / "templates",
        "output_dir": tmp_path / "out",
        "allow_input": True,
        "allow_input_gate": True,
        "input_gate_hotkey": "ctrl+alt+shift+f12",
        "input_gate_max_ttl": 600.0,
        "beep": False,
    }
    values.update(overrides)
    return Settings(**values)


def _key_event(name: str = "f12", *, passthrough: bool = False, down: bool = True):
    return SimpleNamespace(
        name=name,
        is_key_down=down,
        is_passthrough=passthrough,
    )


def test_arm_blocks_both_and_installs_escape(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)

    status = gate.arm()

    assert status["keyboard_blocked"] is True
    assert status["mouse_blocked"] is True
    assert status["keyboard_hook_running"] is True
    assert status["mouse_hook_running"] is True
    assert status["any_blocked"] is True
    assert status["ttl"] is None
    assert status["expires_in"] is None
    assert status["hotkey"] == "ctrl+alt+shift+f12"
    assert backend.escape_callback is not None
    assert backend.hotkey == "ctrl+alt+shift+f12"


def test_release_unblocks_and_removes_escape(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()

    status = gate.release()

    assert status["keyboard_blocked"] is False
    assert status["mouse_blocked"] is False
    assert status["any_blocked"] is False
    assert backend.escape_callback is None
    assert status["last_release_reason"] == "manual"


def test_rearm_clears_last_release_reason(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()
    gate.release()
    assert gate.status()["last_release_reason"] == "manual"

    gate.arm()

    status = gate.status()
    assert status["keyboard_blocked"] is True
    assert status["last_release_reason"] is None
    assert status["last_release_at"] is None


def test_release_one_device_keeps_escape_until_mouse_is_released(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()

    status = gate.release(keyboard=True, mouse=False)

    assert status["keyboard_blocked"] is False
    assert status["mouse_blocked"] is True
    assert backend.escape_callback is not None

    status = gate.release(keyboard=False, mouse=True)

    assert status["mouse_blocked"] is False
    assert backend.escape_callback is None


def test_arm_rolls_back_keyboard_when_mouse_fails(tmp_path):
    backend = FakeGateBackend()
    backend.fail_mouse_block = True
    gate = InputGate(_settings(tmp_path), backend=backend)

    with pytest.raises(PyFindRuntimeError, match="mouse hook failed"):
        gate.arm()

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert backend.escape_callback is None


def test_invalid_hotkey_prevents_arming(tmp_path):
    backend = FakeGateBackend()
    backend.hotkey_valid = False
    gate = InputGate(_settings(tmp_path), backend=backend)

    with pytest.raises(PyFindRuntimeError, match="bad hotkey"):
        gate.arm()

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False


def test_ttl_auto_releases(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path, input_gate_max_ttl=5.0), backend=backend)
    gate.arm(ttl=0.1)

    assert gate.status()["expires_in"] is not None
    deadline = time.monotonic() + 2.0
    while backend.keyboard_blocked and time.monotonic() < deadline:
        time.sleep(0.01)

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert gate.status()["last_release_reason"] == "ttl"


def test_ttl_validation(tmp_path):
    gate = InputGate(_settings(tmp_path, input_gate_max_ttl=5.0), backend=FakeGateBackend())

    with pytest.raises(ValueError, match="ttl"):
        gate.arm(ttl=0)
    with pytest.raises(ValueError, match="ttl"):
        gate.arm(ttl=10)
    with pytest.raises(ValueError, match="at least one"):
        gate.arm(keyboard=False, mouse=False)


def test_emergency_hotkey_releases_the_gate(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()
    callback = backend.escape_callback
    backend.pressed = True

    callback(_key_event())

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert backend.escape_callback is None
    assert gate.status()["last_release_reason"] == "emergency_hotkey"


def test_emergency_hotkey_ignores_injected_events(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()
    callback = backend.escape_callback
    backend.pressed = True

    callback(_key_event(passthrough=True))

    assert backend.keyboard_blocked is True
    assert backend.mouse_blocked is True
    assert backend.escape_callback is not None


def test_emergency_hotkey_ignores_wrong_key_and_key_up(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()
    callback = backend.escape_callback
    backend.pressed = True

    callback(_key_event(name="f11"))
    callback(_key_event(down=False))

    assert backend.keyboard_blocked is True
    assert backend.mouse_blocked is True


def test_emergency_hotkey_requires_modifiers(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()
    callback = backend.escape_callback
    backend.pressed = False

    callback(_key_event())

    assert backend.keyboard_blocked is True
    assert backend.mouse_blocked is True


def test_close_releases_and_stops_backend(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm()

    gate.close()

    assert backend.calls[-1] == ("stop",)
    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert gate.status()["closed"] is True


def test_begin_task_installs_escape_without_blocking(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)

    status = gate.begin_task()

    assert status["task_active"] is True
    assert status["task_started"] is True
    assert status["any_blocked"] is False
    assert backend.escape_callback is not None
    assert ("block_keyboard",) not in backend.calls
    assert ("block_mouse",) not in backend.calls


def test_begin_task_is_idempotent(tmp_path):
    gate = InputGate(_settings(tmp_path), backend=FakeGateBackend())

    gate.begin_task()
    status = gate.begin_task()

    assert status["task_active"] is True
    assert status["task_started"] is False


def test_injection_blocks_only_its_devices_and_restores_them(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.begin_task()

    lease = gate.begin_injection(keyboard=True, mouse=False)

    assert lease == {"keyboard": True, "mouse": False, "escape": False}
    assert backend.keyboard_blocked is True
    assert backend.mouse_blocked is False

    gate.end_injection(**lease)

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert backend.escape_callback is not None


def test_injection_without_task_removes_temporary_escape(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)

    lease = gate.begin_injection(keyboard=False, mouse=True)

    assert lease == {"keyboard": False, "mouse": True, "escape": True}
    assert backend.mouse_blocked is True
    assert backend.escape_callback is not None

    gate.end_injection(**lease)

    assert backend.mouse_blocked is False
    assert backend.escape_callback is None


def test_injection_does_not_open_a_manual_block(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm(keyboard=False, mouse=True)

    lease = gate.begin_injection(keyboard=True, mouse=True)

    assert lease == {"keyboard": True, "mouse": False, "escape": False}

    gate.end_injection(**lease)

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is True

    gate.release()


def test_injection_rolls_back_a_partial_block(tmp_path):
    backend = FakeGateBackend()
    backend.fail_mouse_block = True
    gate = InputGate(_settings(tmp_path), backend=backend)

    with pytest.raises(PyFindRuntimeError):
        gate.begin_injection(keyboard=True, mouse=True)

    assert backend.keyboard_blocked is False
    assert backend.escape_callback is None


def test_injection_rollback_does_not_open_a_manual_block(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.arm(keyboard=True, mouse=False)
    backend.fail_mouse_block = True

    with pytest.raises(PyFindRuntimeError):
        gate.begin_injection(keyboard=True, mouse=True)

    assert backend.keyboard_blocked is True
    gate.release()


def test_end_task_releases_everything_and_is_idempotent(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.begin_task()
    gate.begin_injection(keyboard=True, mouse=True)

    status = gate.end_task()

    assert status["task_active"] is False
    assert status["task_ended"] is True
    assert status["any_blocked"] is False
    assert backend.escape_callback is None

    status = gate.end_task()

    assert status["task_ended"] is False


def test_emergency_hotkey_ends_the_task(tmp_path):
    backend = FakeGateBackend()
    gate = InputGate(_settings(tmp_path), backend=backend)
    gate.begin_task()
    gate.begin_injection(keyboard=True, mouse=True)
    callback = backend.escape_callback
    backend.pressed = True

    callback(_key_event())

    assert backend.keyboard_blocked is False
    assert backend.mouse_blocked is False
    assert gate.status()["task_active"] is False


def test_release_before_arm_reports_available(tmp_path):
    pytest.importorskip("loge2")
    gate = InputGate(_settings(tmp_path))

    status = gate.release()

    assert status["available"] is True
    assert status["any_blocked"] is False
    assert status["last_release_reason"] == "manual"


def test_status_reports_unavailable_without_loge2(monkeypatch, tmp_path):
    import custom_agent_mcp.input_gate as gate_module

    monkeypatch.setattr(gate_module.importlib.util, "find_spec", lambda name: None)
    gate = InputGate(_settings(tmp_path))

    status = gate.status()

    assert status["available"] is False
    assert status["keyboard_blocked"] is False
    assert "loge2" in status["error"]