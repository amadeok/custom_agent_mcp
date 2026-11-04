from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .exceptions import Loge2NotInstalledError

INSTALL_HINT = (
    "The 'loge2' package is required for input blocking and tagged injection. "
    "Install it from the loge2 repository first "
    "(for example: python -m pip install -e F:\\all\\GitHub\\loge2)."
)


def import_inject() -> Any:
    """Import :mod:`loge2.inject` without importing it at server startup."""
    try:
        from loge2 import inject
    except ImportError as exc:
        raise Loge2NotInstalledError(f"{INSTALL_HINT} Import error: {exc}") from exc
    return inject


def import_local() -> SimpleNamespace:
    """Import the in-process keyboard and mouse hook modules."""
    try:
        from loge2 import local_keyboard, local_mouse
    except ImportError as exc:
        raise Loge2NotInstalledError(f"{INSTALL_HINT} Import error: {exc}") from exc
    return SimpleNamespace(keyboard=local_keyboard, mouse=local_mouse)