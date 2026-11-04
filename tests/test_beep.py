from __future__ import annotations

import pytest

from custom_agent_mcp import beep


def test_cues_have_distinct_patterns():
    assert beep.GATE_ARMING_NOTES[0][0] < beep.GATE_ARMING_NOTES[1][0]
    assert beep.GATE_RELEASE_NOTES[0][0] > beep.GATE_RELEASE_NOTES[1][0]
    assert len(beep.INJECTION_NOTES) == 1
    assert beep.INJECTION_NOTES[0] not in beep.GATE_ARMING_NOTES
    assert beep.INJECTION_NOTES[0] not in beep.GATE_RELEASE_NOTES


@pytest.mark.parametrize(
    ("cue", "notes"),
    [
        (beep.task_start_cue, beep.GATE_ARMING_NOTES),
        (beep.task_end_cue, beep.GATE_RELEASE_NOTES),
        (beep.injection_cue, beep.INJECTION_NOTES),
    ],
)
def test_each_cue_is_played_asynchronously(monkeypatch, cue, notes):
    import custom_agent_mcp.beep as beep_module

    calls: list[tuple[tuple[int, int], ...]] = []
    monkeypatch.setattr(beep_module, "play_notes_async", lambda notes: calls.append(tuple(notes)))

    cue()

    assert calls == [notes]


def test_beep_failure_is_reported_and_swallowed(monkeypatch):
    winsound = pytest.importorskip("winsound")

    def fail(frequency, duration):
        raise OSError("no audio device")

    monkeypatch.setattr(winsound, "Beep", fail)

    assert beep.beep(440, 50) is False


def test_play_notes_keeps_going_after_a_failed_note(monkeypatch):
    winsound = pytest.importorskip("winsound")
    calls: list[tuple[int, int]] = []

    def flaky(frequency, duration):
        calls.append((frequency, duration))
        if len(calls) == 1:
            raise OSError("first note failed")

    monkeypatch.setattr(winsound, "Beep", flaky)

    assert beep.play_notes(((440, 50), (660, 50))) is True
    assert calls == [(440, 50), (660, 50)]


def test_async_notes_play_on_a_daemon_thread(monkeypatch):
    import custom_agent_mcp.beep as beep_module

    calls: list[tuple[tuple[int, int], ...]] = []
    monkeypatch.setattr(beep_module, "play_notes", lambda notes: calls.append(tuple(notes)))

    thread = beep.play_notes_async(beep.GATE_RELEASE_NOTES)
    thread.join(timeout=2)

    assert thread.daemon is True
    assert calls == [beep.GATE_RELEASE_NOTES]