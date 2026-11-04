from __future__ import annotations

import pytest

import custom_agent_mcp.focus as focus
from custom_agent_mcp.exceptions import FocusNotAcquiredError


def test_existing_foreground_is_not_touched(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: True)
    monkeypatch.setattr(
        focus, "force_foreground", lambda handle: pytest.fail("must not refocus")
    )

    focus.ensure_foreground(42, title="Notepad")


def test_force_foreground_is_verified(monkeypatch):
    owned = iter([False, False, True])
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: next(owned))
    monkeypatch.setattr(focus, "force_foreground", lambda handle: True)
    monkeypatch.setattr(focus.time, "sleep", lambda seconds: None)

    focus.ensure_foreground(42, title="Notepad", timeout=1.0)


def test_missing_window_cannot_be_focused(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: False)
    monkeypatch.setattr(focus, "force_foreground", lambda handle: False)

    with pytest.raises(FocusNotAcquiredError, match="no longer exists"):
        focus.ensure_foreground(42, title="Notepad")


def test_focus_that_never_arrives_raises(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: False)
    monkeypatch.setattr(focus, "force_foreground", lambda handle: True)

    with pytest.raises(FocusNotAcquiredError, match="could not be focused"):
        focus.ensure_foreground(42, title="Notepad", timeout=0.0)


def test_zero_handle_is_rejected():
    with pytest.raises(FocusNotAcquiredError, match="must not be 0"):
        focus.ensure_foreground(0, title="?")


def test_capture_foreground_reports_handle_and_title(monkeypatch):
    monkeypatch.setattr(focus, "foreground_window", lambda: 7)
    monkeypatch.setattr(focus, "window_title", lambda handle: "OpenCode")

    assert focus.capture_foreground() == (7, "OpenCode")


def test_capture_foreground_without_a_window_is_empty(monkeypatch):
    monkeypatch.setattr(focus, "foreground_window", lambda: 0)
    monkeypatch.setattr(
        focus, "window_title", lambda handle: pytest.fail("must not read a title")
    )

    assert focus.capture_foreground() == (0, None)


def test_restore_foreground_is_a_noop_without_a_previous_window(monkeypatch):
    monkeypatch.setattr(
        focus, "force_foreground", lambda handle: pytest.fail("must not refocus")
    )

    focus.restore_foreground(0)


def test_restore_foreground_puts_the_previous_window_back(monkeypatch):
    owned = iter([False, False, True])
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: next(owned))
    monkeypatch.setattr(focus, "force_foreground", lambda handle: True)
    monkeypatch.setattr(focus.time, "sleep", lambda seconds: None)

    focus.restore_foreground(7, title="OpenCode", timeout=1.0)


def test_restore_foreground_skips_a_window_that_still_has_focus(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: True)
    monkeypatch.setattr(
        focus, "force_foreground", lambda handle: pytest.fail("must not refocus")
    )

    focus.restore_foreground(7, title="OpenCode")


def test_restore_foreground_that_never_arrives_raises(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: False)
    monkeypatch.setattr(focus, "force_foreground", lambda handle: True)

    with pytest.raises(FocusNotAcquiredError, match="Focus was not restored"):
        focus.restore_foreground(7, title="OpenCode", timeout=0.0)


def test_restore_foreground_of_a_closed_window_raises(monkeypatch):
    monkeypatch.setattr(focus, "owns_foreground", lambda handle: False)
    monkeypatch.setattr(focus, "force_foreground", lambda handle: False)

    with pytest.raises(FocusNotAcquiredError, match="no longer exists"):
        focus.restore_foreground(7, title="OpenCode")
