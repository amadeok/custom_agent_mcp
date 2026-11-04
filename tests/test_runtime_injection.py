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

    def begin_task(self, *, mode=None) -> dict:
        self.calls.append(("begin_task", mode))
        return {"task_active": True, "task_started": True, "task_mode": mode}

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


def test_every_injection_blocks_keyboard_and_mouse(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.click(10, 10)
    runtime.type_text("hi")
    runtime.press_key("enter")
    runtime.scroll(1)
    runtime.close()

    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]


def test_click_modifiers_are_held_while_both_devices_are_blocked(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.click(10, 10, modifiers=["ctrl"])
    runtime.close()

    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]


def test_drag_blocks_both_devices_for_the_whole_drag(tmp_path):
    runtime, gate, _ = _runtime(tmp_path)

    runtime.drag(10, 10, 20, 20, duration=0)
    runtime.close()

    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]


def test_injection_releases_the_gate_when_inject_raises(tmp_path):
    runtime, gate, inject = _runtime(tmp_path)
    inject.fail_on = "mouse_click_at"

    with pytest.raises(RuntimeError, match="mouse_click_at failed"):
        runtime.click(10, 10)

    runtime.close()
    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
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
        ("begin_task", None),
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
        ("end_task",),
    ]


def test_begin_input_task_passes_the_mode_to_the_gate(tmp_path):
    runtime, gate, _ = _runtime(tmp_path, beep=False)

    runtime.begin_input_task(mode="task")
    runtime.end_input_task()
    runtime.close()

    assert gate.calls == [("begin_task", "task"), ("end_task",)]


def _fake_window(runtime, handle=42, title="Notepad"):
    runtime.find_window = lambda query: {"title": title, "handle": handle}


PREVIOUS = (7, "OpenCode")


def _stub_focus(monkeypatch, gate, *, owns, ensure=None, restore=None, previous=PREVIOUS):
    monkeypatch.setattr(runtime_module.focus_support, "owns_foreground", owns)
    monkeypatch.setattr(
        runtime_module.focus_support, "capture_foreground", lambda: previous
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


def test_press_key_focuses_target_then_restores_previous_focus(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(monkeypatch, gate, owns=lambda h: False)

    result = runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert result["window_title"] == "Notepad"
    assert result["window_handle"] == 42
    assert result["focus_recovered"] is True
    assert result["focus_restored"] is True
    assert result["previous_window_handle"] == 7
    assert result["previous_window_title"] == "OpenCode"
    assert gate.calls == [
        ("begin_injection", True, True),
        ("focus", 42, "Notepad"),
        ("begin_injection", True, True),
        ("restore", 7, "OpenCode"),
        ("end_injection", True, True, False),
        ("end_injection", True, True, False),
    ]
    assert inject.calls == [("tap", "enter", 0.02)]


def test_press_key_skips_focus_when_window_is_already_foreground(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(
        monkeypatch,
        gate,
        owns=lambda h: True,
        ensure=lambda handle, *, title: pytest.fail("focus must not be forced"),
        restore=lambda handle, *, title: pytest.fail("focus must not be restored"),
    )

    result = runtime.press_key("ctrl+s", window_title="Notepad")
    runtime.close()

    assert result["focus_recovered"] is False
    assert result["focus_restored"] is False
    assert result["previous_window_handle"] is None
    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]
    assert inject.calls == [("combo", ("ctrl", "s"), 0.02)]


def test_press_key_does_not_inject_when_focus_cannot_be_recovered(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)

    def fail(handle, *, title):
        raise FocusNotAcquiredError("could not focus")

    # The previous window still owns the foreground, so nothing is restored.
    _stub_focus(
        monkeypatch,
        gate,
        owns=lambda h: h == PREVIOUS[0],
        ensure=fail,
        restore=lambda handle, *, title: pytest.fail("focus must not be restored"),
    )

    with pytest.raises(FocusNotAcquiredError, match="could not focus"):
        runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert inject.calls == []
    # The focus hold is released even though the key was never sent.
    assert gate.calls == [
        ("begin_injection", True, True),
        ("end_injection", True, True, False),
    ]


def test_failed_focus_acquisition_restores_the_previous_window(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    state = {"focused": PREVIOUS[0]}

    def fail_after_moving(handle, *, title):
        # Windows moved focus but verification timed out.
        state["focused"] = handle
        raise FocusNotAcquiredError("focus verification timed out")

    def restore(handle, *, title):
        gate.calls.append(("restore", handle, title))
        state["focused"] = handle

    _stub_focus(
        monkeypatch,
        gate,
        owns=lambda h: h == state["focused"],
        ensure=fail_after_moving,
        restore=restore,
    )

    with pytest.raises(FocusNotAcquiredError, match="focus verification timed out"):
        runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert inject.calls == []
    assert gate.calls == [
        ("begin_injection", True, True),
        ("restore", 7, "OpenCode"),
        ("end_injection", True, True, False),
    ]


def test_failed_focus_restore_raises_but_still_releases_the_gate(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)

    def fail_restore(handle, *, title):
        raise FocusNotAcquiredError("Focus was not restored to OpenCode")

    _stub_focus(monkeypatch, gate, owns=lambda h: False, restore=fail_restore)

    with pytest.raises(FocusNotAcquiredError, match="Focus was not restored"):
        runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    # The key was sent; only the restore failed, and the gate still opened.
    assert inject.calls == [("tap", "enter", 0.02)]
    assert gate.calls[-1] == ("end_injection", True, True, False)


def test_injection_and_focus_restore_failures_are_both_reported(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    inject.fail_on = "tap"

    def fail_restore(handle, *, title):
        raise FocusNotAcquiredError("Focus was not restored to OpenCode")

    _stub_focus(monkeypatch, gate, owns=lambda h: False, restore=fail_restore)

    with pytest.raises(PyFindRuntimeError, match="also failed"):
        runtime.press_key("enter", window_title="Notepad")
    runtime.close()

    assert gate.calls[-1] == ("end_injection", True, True, False)


def test_click_focuses_target_then_restores_previous_focus(tmp_path, monkeypatch):
    runtime, gate, inject = _runtime(tmp_path)
    _fake_window(runtime)
    _stub_focus(monkeypatch, gate, owns=lambda h: False)

    # Cursor restoration has its own tests; disable it to isolate focus.
    result = runtime.click(
        10, 10, window_title="Notepad", restore_cursor=False
    )
    runtime.close()

    assert result["focus_restored"] is True
    assert gate.calls == [
        ("begin_injection", True, True),
        ("focus", 42, "Notepad"),
        ("begin_injection", True, True),
        ("restore", 7, "OpenCode"),
        ("end_injection", True, True, False),
        ("end_injection", True, True, False),
    ]
    assert inject.calls == [("mouse_click_at", 10, 10, "left", 1, 0.0)]


def _stub_cursor(monkeypatch, x=5, y=6):
    monkeypatch.setattr(
        runtime_module, "_cursor_position", lambda: {"x": x, "y": y}
    )


def test_click_restores_the_previous_pointer_position(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.click(10, 10)
    runtime.close()

    assert inject.calls == [
        ("mouse_click_at", 10, 10, "left", 1, 0.0),
        ("mouse_move_to", 5, 6),
    ]
    assert result["cursor_restored"] is True


def test_click_skips_the_restore_when_the_pointer_is_already_there(
    tmp_path, monkeypatch
):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 10, 10)

    result = runtime.click(10, 10)
    runtime.close()

    assert inject.calls == [("mouse_click_at", 10, 10, "left", 1, 0.0)]
    assert result["cursor_restored"] is False


def test_click_can_leave_the_pointer_at_the_target(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.click(10, 10, restore_cursor=False)
    runtime.close()

    assert inject.calls == [("mouse_click_at", 10, 10, "left", 1, 0.0)]
    assert result["cursor_restored"] is False


def test_move_click_does_not_restore(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.click(10, 10, button="move")
    runtime.close()

    assert inject.calls == [("mouse_move_to", 10, 10)]
    assert result["cursor_restored"] is False


def test_click_restores_the_pointer_even_when_the_click_raises(
    tmp_path, monkeypatch
):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)
    inject.fail_on = "mouse_click_at"

    with pytest.raises(RuntimeError, match="mouse_click_at failed"):
        runtime.click(10, 10)
    runtime.close()

    assert inject.calls == [
        ("mouse_click_at", 10, 10, "left", 1, 0.0),
        ("mouse_move_to", 5, 6),
    ]


def test_click_survives_a_failed_cursor_restore(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)
    inject.fail_on = "mouse_move_to"

    result = runtime.click(10, 10)
    runtime.close()

    assert result["ok"] is True
    assert result["cursor_restored"] is False


def test_drag_returns_the_pointer_to_where_it_started(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.drag(10, 10, 20, 20, duration=0)
    runtime.close()

    assert inject.calls == [
        ("mouse_move_to", 10, 10),
        ("mouse_down", "left"),
        ("mouse_move_to", 20, 20),
        ("mouse_up", "left"),
        ("mouse_move_to", 5, 6),
    ]
    assert result["cursor_restored"] is True


def test_drag_can_stay_at_the_drop_point(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.drag(10, 10, 20, 20, duration=0, restore_cursor=False)
    runtime.close()

    assert inject.calls[-1] == ("mouse_up", "left")
    assert result["cursor_restored"] is False


def test_scroll_restores_the_pointer_after_moving_to_the_target(
    tmp_path, monkeypatch
):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.scroll(1, x=10, y=10)
    runtime.close()

    assert inject.calls == [
        ("mouse_move_to", 10, 10),
        ("mouse_scroll", 120, False),
        ("mouse_move_to", 5, 6),
    ]
    assert result["cursor_restored"] is True


def test_scroll_without_a_target_never_moves_the_pointer(tmp_path, monkeypatch):
    runtime, _, inject = _runtime(tmp_path)
    _stub_cursor(monkeypatch, 5, 6)

    result = runtime.scroll(1)
    runtime.close()

    assert inject.calls == [("mouse_scroll", 120, False)]
    assert result["cursor_restored"] is False


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