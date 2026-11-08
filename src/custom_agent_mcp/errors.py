from __future__ import annotations

from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from .exceptions import PyFindRuntimeError, SearxngError

P = ParamSpec("P")
R = TypeVar("R")


def tool_errors(fn: Callable[P, R]) -> Callable[P, R]:
    """Turn expected runtime failures into clean MCP tool errors.

    Unexpected exceptions are left alone so they are reported as crashes with a
    server-side traceback.
    """

    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (PyFindRuntimeError, SearxngError, ValueError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper