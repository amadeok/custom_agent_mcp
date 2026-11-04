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
