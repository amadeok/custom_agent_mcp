"""Documentation guards for the command-queue guidance.

These assertions are intentionally narrow: they fail when the user-facing
contract (queue dependent input in one run_commands call, no task-wide mode)
drifts out of the README, not when wording changes around it.
"""

from __future__ import annotations

from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_documents_the_command_queue():
    text = _readme().lower()

    assert "`run_commands`" in text
    assert '"name"' in text
    assert '"args"' in text
    assert "sleep" in text
    assert "ctrl+a" in text
    assert "type_text" in text
    assert "selection" in text


def test_readme_explains_why_the_queue_is_needed():
    text = _readme().lower()

    assert "restores focus" in text or "restores the captured window" in text
    assert "can be lost" in text or "may append instead of replace" in text
    assert "captures the window that currently has focus" in text
    assert "restores the captured window" in text
    assert "screenshot" in text


def test_readme_no_longer_documents_a_task_mode():
    text = _readme()

    assert 'mode="task"' not in text
    assert "PYFIND_MCP_INPUT_GATE_MODE" not in text
    assert "task_mode" not in text


def test_readme_lists_begin_input_task_without_a_mode():
    rows = [
        line
        for line in _readme().splitlines()
        if line.startswith("| `begin_input_task`")
    ]

    assert len(rows) == 1
    assert "mode=" not in rows[0]