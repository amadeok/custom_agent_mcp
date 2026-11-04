from __future__ import annotations

import time

import pytest

from custom_agent_mcp.input_gate import InputGate
from custom_agent_mcp.runtime import PyFindRuntime, _cursor_position
from custom_agent_mcp.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _restore_cursor_after_test():
    from custom_agent_mcp.runtime import _cursor_position

    original = _cursor_position()
    yield
    try:
        from loge2 import inject

        inject.mouse_move_to(original["x"], original["y"])
    except Exception:
        pass


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "templates_dir": tmp_path / "templates",
        "output_dir": tmp_path / "out",
        "allow_input": True,
        "allow_input_gate": True,
        "input_gate_hotkey": "ctrl+alt+shift+f12",
        "beep": False,
    }
    values.update(overrides)
    return Settings(**values)


def _near(point: dict[str, int], target: tuple[int, int], tolerance: int = 1) -> bool:
    return (
        abs(point["x"] - target[0]) <= tolerance
        and abs(point["y"] - target[1]) <= tolerance
    )


def _send_untagged_key(key: str, down: bool) -> None:
    from loge2 import inject

    scan, extended = inject._resolve_key(key)
    item = inject._key_input(scan, down, extended=extended)
    item.u.ki.dwExtraInfo = 0
    inject._send([item])


def _send_untagged_chord(keys: list[str]) -> None:
    for key in keys:
        _send_untagged_key(key, True)
    for key in reversed(keys):
        _send_untagged_key(key, False)


def test_mouse_gate_blocks_untagged_and_allows_tagged(tmp_path):
    from loge2 import inject

    gate = InputGate(_settings(tmp_path))
    anchor = (300, 300)
    decoy = (500, 500)

    gate.arm(keyboard=False, mouse=True)
    try:
        inject.mouse_move_to(*anchor)
        time.sleep(0.05)
        before = _cursor_position()
        assert _near(before, anchor)

        # An untagged move is indistinguishable from physical user input.
        untagged = inject._absolute_move_input(*decoy)
        untagged.u.mi.dwExtraInfo = 0
        inject._send([untagged])
        time.sleep(0.1)

        blocked = _cursor_position()
        assert (blocked["x"], blocked["y"]) == (before["x"], before["y"])

        # The tagged injection path still reaches the desktop.
        inject.mouse_move_to(*decoy)
        time.sleep(0.1)
        allowed = _cursor_position()
        assert _near(allowed, decoy)
    finally:
        gate.release()
        gate.close()


def test_runtime_move_tools_work_while_mouse_is_blocked(tmp_path):
    runtime = PyFindRuntime(_settings(tmp_path))
    target = (320, 240)

    runtime.block_user_input(keyboard=False, mouse=True)
    try:
        runtime.move_mouse(*target)
        time.sleep(0.05)
        assert _near(runtime.cursor_position(), target)

        runtime.move_mouse_by(20, 10)
        time.sleep(0.05)
        assert _near(runtime.cursor_position(), (target[0] + 20, target[1] + 10))
    finally:
        runtime.unblock_user_input()
        runtime.close()


def test_task_mode_blocks_only_during_injection(tmp_path):
    runtime = PyFindRuntime(_settings(tmp_path))
    runtime.begin_input_task()
    try:
        status = runtime.input_block_status()
        assert status["task_active"] is True
        assert status["any_blocked"] is False

        runtime.move_mouse(100, 100)

        status = runtime.input_block_status()
        assert status["task_active"] is True
        assert status["any_blocked"] is False
    finally:
        runtime.end_input_task()
        runtime.close()


def test_keyboard_gate_blocks_untagged_and_allows_tagged(tmp_path):
    from loge2.ll_keyboard import LLKeyboardHook

    seen = []
    witness = LLKeyboardHook(
        callback=lambda event: seen.append(event) or False,
        name="pyfind-integration-witness",
    )
    witness.start()
    runtime = PyFindRuntime(_settings(tmp_path, input_gate_hotkey=""))
    try:
        runtime.block_user_input(keyboard=True, mouse=False)
        time.sleep(0.1)

        seen.clear()
        _send_untagged_chord(["f16"])
        time.sleep(0.1)
        assert seen == []

        seen.clear()
        runtime.press_key("f16")
        time.sleep(0.1)
        assert any(event.name == "f16" for event in seen)
    finally:
        runtime.unblock_user_input()
        runtime.close()
        witness.stop()


def test_emergency_hotkey_releases_real_gate(tmp_path):
    from loge2 import inject

    hotkey = "ctrl+alt+shift+f16"
    gate = InputGate(_settings(tmp_path, input_gate_hotkey=hotkey))
    gate.arm(keyboard=True, mouse=True)
    try:
        # A tagged hotkey is synthetic and must not release the gate.
        inject.combo(hotkey, hold=0.01)
        time.sleep(0.1)
        assert gate.status()["keyboard_blocked"] is True
        assert gate.status()["mouse_blocked"] is True

        # An untagged hotkey looks physical and releases the gate.
        keys = ["ctrl", "alt", "shift", "f16"]
        for key in keys:
            _send_untagged_key(key, True)
        time.sleep(0.1)

        status = gate.status()
        assert status["keyboard_blocked"] is False
        assert status["mouse_blocked"] is False
        assert status["last_release_reason"] == "emergency_hotkey"

        for key in reversed(keys):
            _send_untagged_key(key, False)
    finally:
        gate.release()
        gate.close()