"""Tests for the runtime behavior a command queue relies on.

The queue runs commands back-to-back on the server. These tests pin the
behaviour the queue and the agent guidance depend on: commands run in the order
they are called, every command gets its own gate lease, and a command that lost
the target refocuses it before injecting.
"""

from __future__ import annotations

import pytest

import custom_agent_mcp.runtime as runtime_module
from custom_agent_mcp.exceptions import FocusNotAcquiredError
from custom_agent_mcp.runtime import PyFindRuntime
from custom_agent_mcp.settings import Settings

PREVIOUS = (7, "OpenCode")
TARGET = 42


class FakeGate:
    def __init__(self, fail_begin: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail_begin = fail_begin

    def begin_task(self) -> dict:
        self.calls.append(("begin_task",))
        return {"task_active": True, "task_started": True}

    def end_task(self) -> dict:
        self.calls.append(("end_task",))
        return {"task_active": False, "task_ended": True}

    def begin_injection(self, *, keyboard: bool, mouse: bool) -> dict[str, bool]:
        self.calls.append(("begin_injection", keyboard, mouse))
        if self.fail_begin:
            raise RuntimeError("gate failed")
        return {"keyboard": keyboard, "mouse": mouse, "escape": False}

    def end_injection(self, *, keyboard: bool, mouse: bool, escape: bool) -> None:
        self.calls.append(("end_injection", keyboard, mouse, escape))

    def close(self) -> None:
        pass


class FakeInject:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", text))

    def combo(self, keys, hold: float = 0.02) -> None:
        self.calls.append(("combo", tuple(keys), hold))

    def tap(self, key: str, hold: float = 0.02) -> None:
        self.calls.append(("tap", key, hold))


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "templates_dir": tmp_path / "templates",
        "output_dir": tmp_path / "out",
        "allow_input": True,
        "allow_input_gate": True,
        "beep": False,
    }
    values.update(overrides)
    return Settings(**values)


def _runtime(tmp_path, **overrides) -> tuple[PyFindRuntime, FakeGate, FakeInject]:
    runtime = PyFindRuntime(_settings(tmp_path, **overrides))
    gate = FakeGate()
    inject = FakeInject()
    runtime.input_gate = gate
    runtime._inject = lambda: inject
    return runtime, gate, inject


def _fake_window(runtime, handle: int = TARGET, title: str = "Notepad") -> None:
    runtime.find_window = lambda query: {"title": title, "handle": handle}


def _stub_focus(monkeypatch, gate, *, owns, ensure=None, restore=None):
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", owns)
    monkeypatch.setattr(
        runtime_module.focus_support, "capture_foreground", lambda: PREVIOUS
    )
    monkeypatch.setattr(
        runtime_module.focus_support,
        "ensure_foreground",
        ensure
        or (lambda handle, *, title: gate.calls.append(("focus", handle, title))),
    )
    monkeypatch.setattr(
        runtime_module.focus_support,
        "restore_foreground",
        restore
        or (lambda handle, *, title: gate.calls.append(("restore", handle, title))),
    )


def _stateful_focus(monkeypatch, state: dict):
    """Focus stubs that remember which window is in front."""

    def record(kind: str, handle: int, title: str) -> None:
        state["foreground"] = handle
        state.setdefault("calls", []).append((kind, handle, title))

    def owns(handle: int) -> bool:
        return state["foreground"] == handle

    def ensure(handle: int, *, title: str, timeout: float | None = None) -> None:
        record("focus", handle, title)

    def restore(handle: int, *, title: str | None = None, timeout: float | None = None) -> None:
        record("restore", handle, title or "")

    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", owns)
    monkeypatch.setattr(
        runtime_module.focus_support, "capture_foreground", lambda: PREVIOUS
    )
    monkeypatch.setattr(runtime_module.focus_support, "ensure_foreground", ensure)
    monkeypatch.setattr(runtime_module.focus_support, "restore_foreground", restore)


def test_ctrl_a_then_type_sends_events_in_await_order(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(monkeypatch, gate, owns=lambda handle: False)

    runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
    runtime.type_text(
        "replacement", window_title="Notepad", interval=0, settle=0
    )
    runtime.close()

    assert inject.calls == [
        ("combo", ("ctrl", "a"), 0.02),
        ("type_text", "r"),
        ("type_text", "e"),
        ("type_text", "p"),
        ("type_text", "l"),
        ("type_text", "a"),
        ("type_text", "c"),
        ("type_text", "e"),
        ("type_text", "m"),
        ("type_text", "e"),
        ("type_text", "n"),
        ("type_text", "t"),
    ]


def test_each_command_in_a_batch_gets_its_own_gate_lease(tmp_path, monkeypatch):
    runtime, gate, _ = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(monkeypatch, gate, owns=lambda handle: False)

    runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
    runtime.type_text("x", window_title="Notepad", interval=0, settle=0)
    runtime.close()

    begins = [call for call in gate.calls if call[0] == "begin_injection"]
    ends = [call for call in gate.calls if call[0] == "end_injection"]

    # One focus-hold lease and one injection lease per command.
    assert begins == [
        ("begin_injection", True, True),
        ("begin_injection", True, True),
    ] * 2
    assert ends == [
        ("end_injection", True, True, False),
        ("end_injection", True, True, False),
    ] * 2


def test_each_command_reacquires_the_target_between_awaits(tmp_path, monkeypatch):
    runtime, _, _ = _runtime(tmp_path)
    _fake_window(runtime)
    state = {"foreground": PREVIOUS[0]}
    _stateful_focus(monkeypatch, state)

    runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
    # Focus has already been given back to OpenCode, so the next command must
    # force Notepad to the foreground again; this is where a selection can die.
    assert state["foreground"] == PREVIOUS[0]

    runtime.type_text("x", window_title="Notepad", interval=0, settle=0)
    runtime.close()

    assert state["foreground"] == PREVIOUS[0]
    focus_calls = [call for call in state["calls"] if call[0] == "focus"]
    restore_calls = [call for call in state["calls"] if call[0] == "restore"]
    assert focus_calls == [("focus", TARGET, "Notepad")] * 2
    assert restore_calls == [("restore", *PREVIOUS)] * 2


def test_focusing_once_then_batching_keeps_the_target_in_front(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    state = {"foreground": PREVIOUS[0]}
    _stateful_focus(monkeypatch, state)

    # An explicit focus leaves the target in front, as documented.
    runtime.focus_window("Notepad")
    assert state["foreground"] == TARGET
    state["calls"].clear()

    runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
    runtime.type_text("x", window_title="Notepad", interval=0, settle=0)
    runtime.close()

    # The target already owned the foreground, so no command stole or restored
    # focus and the selection is never deactivated.
    assert state["foreground"] == TARGET
    assert [call for call in state["calls"] if call[0] in {"focus", "restore"}] == []
    assert inject.calls == [
        ("combo", ("ctrl", "a"), 0.02),
        ("type_text", "x"),
    ]


def test_a_restore_failure_aborts_the_rest_of_the_batch(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)

    def fail_restore(handle: int, *, title: str) -> None:
        raise FocusNotAcquiredError("Focus was not restored to OpenCode")

    _stub_focus(
        monkeypatch, gate, owns=lambda handle: False, restore=fail_restore
    )

    def run_batch() -> None:
        # This mirrors awaited calls in one execute script: an uncaught throw
        # stops the rest of the script.
        runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
        runtime.type_text("replacement", window_title="Notepad", interval=0, settle=0)

    with pytest.raises(FocusNotAcquiredError, match="Focus was not restored"):
        run_batch()
    runtime.close()

    # ctrl+a was sent, but the replacement typing never ran. The script must
    # surface this instead of continuing with a stale selection.
    assert inject.calls == [("combo", ("ctrl", "a"), 0.02)]


def test_batch_events_are_all_sent_with_both_devices_blocked(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(monkeypatch, gate, owns=lambda handle: False)

    runtime.begin_input_task()
    try:
        runtime.press_key("ctrl+a", window_title="Notepad", settle=0)
        runtime.type_text("x", window_title="Notepad", interval=0, settle=0)
    finally:
        runtime.end_input_task()
    runtime.close()

    assert gate.calls[0] == ("begin_task",)
    assert gate.calls[-1] == ("end_task",)
    every_lease_blocks_both = all(
        call == ("begin_injection", True, True)
        for call in gate.calls
        if call[0] == "begin_injection"
    )
    assert every_lease_blocks_both
    assert inject.calls == [
        ("combo", ("ctrl", "a"), 0.02),
        ("type_text", "x"),
    ]
