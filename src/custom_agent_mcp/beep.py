from __future__ import annotations

import sys
import threading
from typing import Sequence

from loguru import logger

# Ascending tones bracket a gated input task. A single short, higher blip marks
# each individual injection. All cues are played asynchronously.
GATE_ARMING_NOTES: tuple[tuple[int, int], ...] = ((880, 80), (1320, 130))
GATE_RELEASE_NOTES: tuple[tuple[int, int], ...] = ((1320, 90), (880, 150))
INJECTION_NOTES: tuple[tuple[int, int], ...] = ((1760, 35),)


def beep(frequency: int = 880, duration_ms: int = 120) -> bool:
    """Play one Windows beep. Returns False when audio is unavailable."""
    if sys.platform != "win32":
        return False
    try:
        import winsound
    except ImportError:
        logger.debug("winsound is unavailable; skipping beep")
        return False
    try:
        winsound.Beep(int(frequency), int(duration_ms))
    except Exception as exc:
        logger.debug("Could not play beep: {}", exc)
        return False
    return True


def play_notes(notes: Sequence[tuple[int, int]]) -> bool:
    """Play a sequence of (frequency_hz, duration_ms) notes."""
    played = False
    for frequency, duration_ms in notes:
        played = beep(frequency, duration_ms) or played
    return played


def play_notes_async(notes: Sequence[tuple[int, int]]) -> threading.Thread | None:
    """Play notes on a daemon thread so tool calls and hooks never wait."""
    try:
        thread = threading.Thread(
            target=play_notes,
            args=(tuple(notes),),
            daemon=True,
            name="pyfind-beep",
        )
        thread.start()
    except Exception as exc:
        logger.debug("Could not start beep thread: {}", exc)
        return None
    return thread


def task_start_cue() -> threading.Thread | None:
    """Ascending cue for the start of an injected-input task."""
    return play_notes_async(GATE_ARMING_NOTES)


def task_end_cue() -> threading.Thread | None:
    """Descending cue for the end of an injected-input task."""
    return play_notes_async(GATE_RELEASE_NOTES)


def injection_cue() -> threading.Thread | None:
    """Short, distinct blip played for each individual injection."""
    return play_notes_async(INJECTION_NOTES)