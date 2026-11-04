from __future__ import annotations

import pytest

import custom_agent_mcp.runtime as runtime_module
from custom_agent_mcp.exceptions import FocusNotAcquiredError, PyFindRuntimeError
from custom_agent_mcp.runtime import PyFindRuntime
from custom_agent_mcp.settings import Settings


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
            raise PyFindRuntimeError("gate failed")
        return {"keyboard": keyboard, "mouse": mouse, "escape": False}

    def end_injection(self, *, keyboard: bool, mouse: bool, escape: bool) -> None:
        self.calls.append(("end_injection", keyboard, mouse, escape))

    def close(self) -> None:
        pass


class FakeInject:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_on: str | None = None

    def _record(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if self.fail_on == name:
            raise RuntimeError(f"{name} failed")

    def mouse_move_to(self, x, y):
        self._record("mouse_move_to", x, y)

    def mouse_click_at(self, x, y, button="left", count=1, interval=0.0):
        self._record("mouse_click_at", x, y, button, count, interval)

    def mouse_down(self, button):
        self._record("mouse_down", button)

    def mouse_up(self, button):
        self._record("mouse_up", button)

    def mouse_scroll(self, delta, horizontal=False):
        self._record("mouse_scroll", delta, horizontal)

    def key_down(self, key):
        self._record("key_down", key)

    def key_up(self, key):
        self._record("key_up", key)

    def type_text(self, text):
        self._record("type_text", text)

    def combo(self, keys, hold=0.02):
        self._record("combo", tuple(keys), hold)

    def tap(self, key, hold=0.02):
        self._record("tap", key, hold)


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


def test_each_input_call_blocks_only_for_its_own_injection(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.click(10, 10)
    runtime.type_text("hi")
    runtime.press_key("enter")
    runtime.scroll(1)
    runtime.close()

    assert gate.calls == [
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
        ("begin_injection", True, False),
        ("end_injection", True, False, False),
        ("begin_injection", True, False),
        ("end_injection", True, False, False),
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
    ]


def test_click_modifiers_block_the_keyboard_too(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.click(10, 10, modifiers=["ctrl"])
    runtime.close()

    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]


def test_drag_blocks_for_the_whole_drag(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.drag(10, 10, 20, 20, duration=0)
    runtime.close()

    assert gate.calls == [
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
    ]


def test_injection_releases_the_gate_when_inject_raises(tmp_path):
    runtime, gate, inject = _runtime(tmp_path)
    inject.fail_on = "mouse_click_at"

    with pytest.raises(RuntimeError, match="mouse_click_at failed"):
        runtime.click(10, 10)

    runtime.close()
    assert gate.calls == [
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
    ]


def test_without_gate_enabled_input_is_not_wrapped(tmp_path):
    runtime, gate, _ = _runtime(tmp_path, allow_input_gate=False)

    runtime.click(10, 10)
    runtime.close()

    assert gate.calls == []


def test_task_and_injection_cues_fire_around_actions(tmp_path, monkeypatch):
    runtime, gate, _ = _runtime(tmp_path, beep=True)
    cues: list[str] = []
    monkeypatch.setattr(runtime_module.beep, "task_start_cue", lambda: cues.append("start"))
    monkeypatch.setattr(runtime_module.beep, "injection_cue", lambda: cues.append("injection"))
    monkeypatch.setattr(runtime_module.beep, "task_end_cue", lambda: cues.append("end"))

    runtime.begin_input_task()
    runtime.click(10, 10)
    runtime.end_input_task()
    runtime.close()

    assert cues == ["start", "injection", "end"]
    assert gate.calls == [
        ("begin_task",),
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
        ("end_task",),
    ]


def _fake_window(runtime, handle=42, title="Notepad"):
    runtime.find_window = lambda query: {"title": title, "handle": handle}


def test_press_key_focuses_target_before_injecting(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", lambda h: False)
    monkeypatch.setattr(
        runtime_module.focus_support,
        "ensure_foreground",
        lambda handle, *, title: gate.calls.append(("focus", handle, title)),
    )

    result = runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert result["window_title"] == "Notepad"
    assert result["window_handle"] == 42
    assert result["focus_recovered"] is True
    assert gate.calls == [
        ("begin_injection", True, False),
        ("focus", 42, "Notepad"),
        ("begin_injection", True, False),
        ("end_injection", True, False, False),
        ("end_injection", True, False, False),
    ]
    assert inject.calls == [("tap", "enter", 0.02)]


def test_press_key_skips_focus_when_window_is_already_foreground(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", lambda h: True)
    monkeypatch.setattr(
        runtime_module.focus_support,
        "ensure_foreground",
        lambda handle, *, title: pytest.fail("focus must not be forced"),
    )

    result = runtime.press_key("ctrl+s", window_title="Notepad")
    runtime.close()

    assert result["focus_recovered"] is False
    assert gate.calls == [
        ("begin_injection", True, False),
        ("end_injection", True, False, False),
    ]
    assert inject.calls == [("combo", ("ctrl", "s"), 0.02)]


def test_press_key_does_not_inject_when_focus_cannot_be_recovered(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", lambda h: False)

    def fail(handle, *, title):
        raise FocusNotAcquiredError("could not focus")

    monkeypatch.setattr(runtime_module.focus_support, "ensure_foreground", fail)

    with pytest.raises(FocusNotAcquiredError, match="could not focus"):
        runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert inject.calls == []
    # The focus hold is released even though the key was never sent.
    assert gate.calls == [
        ("begin_injection", True, False),
        ("end_injection", True, False, False),
    ]


def test_click_focuses_target_before_sending_mouse_event(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", lambda h: False)
    monkeypatch.setattr(
        runtime_module.focus_support,
        "ensure_foreground",
        lambda handle, *, title: gate.calls.append(("focus", handle, title)),
    )

    runtime.click(10, 10, window_title="Notepad")
    runtime.close()

    assert gate.calls == [
        ("begin_injection", False, True),
        ("focus", 42, "Notepad"),
        ("begin_injection", False, True),
        ("end_injection", False, True, False),
        ("end_injection", False, True, False),
    ]
    assert inject.calls == [("mouse_click_at", 10, 10, "left", 1, 0.0)]


def test_focus_window_forces_foreground(tmp_path, monkeypatch):
    runtime, _, _ = _runtime(tmp_path)
    _fake_window(runtime)
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", lambda h: False)
    calls = []
    monkeypatch.setattr(
        runtime_module.focus_support,
        "ensure_foreground",
        lambda handle, *, title, timeout: calls.append((handle, title, timeout)),
    )

    result = runtime.focus_window("Notepad", timeout=0.2)
    runtime.close()

    assert calls == [(42, "Notepad", 0.2)]
    assert result["was_focused"] is False
    assert result["focused"] is True


def test_no_cues_when_beep_is_disabled(tmp_path, monkeypatch):
    runtime, _, _ = _runtime(tmp_path, beep=False)
    cues: list[str] = []
    monkeypatch.setattr(runtime_module.beep, "task_start_cue", lambda: cues.append("start"))
    monkeypatch.setattr(runtime_module.beep, "injection_cue", lambda: cues.append("injection"))
    monkeypatch.setattr(runtime_module.beep, "task_end_cue", lambda: cues.append("end"))

    runtime.begin_input_task()
    runtime.click(10, 10)
    runtime.end_input_task()
    runtime.close()

    assert cues == []