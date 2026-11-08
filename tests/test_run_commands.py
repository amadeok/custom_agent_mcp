from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from custom_agent_mcp.server import create_server
from custom_agent_mcp.settings import Settings
from custom_agent_mcp.tools.batch import (
    Command,
    execute_commands,
    register_batch_tools,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.focus_calls: list[str] = []
        self.focus_settles: list[float] = []
        self.events: list[tuple] = []

    @contextmanager
    def focus_sequence(self, window_title: str, *, settle: float = 0.2):
        self.focus_calls.append(window_title)
        self.focus_settles.append(settle)
        self.events.append(("focus_begin", window_title))
        try:
            yield {
                "ok": True,
                "window_title": window_title,
                "window_handle": 42,
                "focus_recovered": True,
                "focus_restored": True,
                "previous_window_title": "OpenCode",
                "previous_window_handle": 7,
            }
        finally:
            self.events.append(("focus_end", window_title))


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, annotations=None, description=None):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _handlers(calls: list[tuple]) -> dict:
    def noop() -> dict:
        calls.append(("noop",))
        return {"ok": True}

    def boom() -> dict:
        calls.append(("boom",))
        raise RuntimeError("boom")

    def press_key(
        key: str,
        hold: float = 0.02,
        settle: float = 0.2,
        window_title: str | None = None,
    ) -> dict:
        calls.append(("press_key", key, hold, settle, window_title))
        return {"ok": True}

    def type_text(
        text: str,
        interval: float = 0.03,
        settle: float = 0.2,
        window_title: str | None = None,
    ) -> dict:
        calls.append(("type_text", text, interval, settle, window_title))
        return {"ok": True}

    def find_text(
        texts: list[str],
        window_title: str | None = None,
    ) -> dict:
        calls.append(("find_text", texts, window_title))
        return {"ok": True}

    return {
        "noop": noop,
        "boom": boom,
        "press_key": press_key,
        "type_text": type_text,
        "find_text": find_text,
    }


def _settings(tmp_path) -> Settings:
    return Settings(
        templates_dir=tmp_path / "templates",
        output_dir=tmp_path / "out",
        allow_input=True,
        allow_input_gate=True,
        beep=False,
    )


def test_commands_run_in_list_order_with_positional_args():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    payload = execute_commands(
        runtime,
        _handlers(calls),
        [
            Command(name="press_key", args=["ctrl+a"]),
            Command(name="type_text", args=["replacement"]),
        ],
    )

    assert calls == [
        ("press_key", "ctrl+a", 0.02, 0.2, None),
        ("type_text", "replacement", 0.03, 0.2, None),
    ]
    assert payload["ok"] is True
    assert payload["command_count"] == 2
    assert payload["completed"] == 2
    assert payload["failed"] == 0
    assert payload["stopped_early"] is False
    assert [item["function"] for item in payload["results"]] == [
        "press_key",
        "type_text",
    ]


def test_window_title_focuses_once_before_the_queue():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    payload = execute_commands(
        runtime,
        _handlers(calls),
        [
            Command(name="press_key", args=["ctrl+a"]),
            Command(name="type_text", args=["replacement"]),
        ],
        window_title="Untitled - Notepad",
    )

    assert runtime.focus_calls == ["Untitled - Notepad"]
    assert runtime.focus_settles == [0.2]
    assert payload["focus"]["window_title"] == "Untitled - Notepad"
    assert payload["focus"]["window_handle"] == 42
    assert payload["focus"]["focus_restored"] is True
    assert calls[0][0] == "press_key"
    assert calls[1][0] == "type_text"


def test_the_queue_passes_settle_to_the_focus_sequence():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    execute_commands(
        runtime,
        _handlers(calls),
        [Command(name="type_text", args=["replacement"])],
        window_title="Notepad",
        settle=0.5,
    )

    assert runtime.focus_settles == [0.5]


def test_the_first_command_window_title_is_the_default_focus_target():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    execute_commands(
        runtime,
        _handlers(calls),
        [
            Command(name="noop"),
            Command(name="press_key", args=["ctrl+a", 0.02, 0.2, "Notepad"]),
        ],
    )

    assert runtime.focus_calls == ["Notepad"]
    assert calls == [
        ("noop",),
        ("press_key", "ctrl+a", 0.02, 0.2, "Notepad"),
    ]


def test_observation_commands_do_not_trigger_the_focus_sequence():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    execute_commands(
        runtime,
        _handlers(calls),
        [Command(name="find_text", args=[["Save"], "Notepad"])],
    )

    assert runtime.focus_calls == []
    assert calls == [("find_text", ["Save"], "Notepad")]


def test_a_failed_command_stops_the_queue_by_default():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    payload = execute_commands(
        runtime,
        _handlers(calls),
        [
            Command(name="noop"),
            Command(name="boom"),
            Command(name="noop"),
        ],
    )

    assert calls == [("noop",), ("boom",)]
    assert payload["ok"] is False
    assert payload["completed"] == 1
    assert payload["failed"] == 1
    assert payload["stopped_early"] is True
    failed = payload["results"][1]
    assert failed["ok"] is False
    assert failed["function"] == "boom"
    assert failed["error_type"] == "RuntimeError"
    assert failed["error"] == "boom"


def test_continue_on_error_runs_the_remaining_commands():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    payload = execute_commands(
        runtime,
        _handlers(calls),
        [
            Command(name="noop"),
            Command(name="boom"),
            Command(name="noop"),
        ],
        continue_on_error=True,
    )

    assert calls == [("noop",), ("boom",), ("noop",)]
    assert payload["ok"] is False
    assert payload["completed"] == 2
    assert payload["failed"] == 1
    assert payload["stopped_early"] is False


def test_an_unknown_function_rejects_the_queue_before_anything_runs():
    calls: list[tuple] = []
    runtime = FakeRuntime()

    with pytest.raises(ValueError, match="unknown function 'nope'"):
        execute_commands(
            runtime,
            _handlers(calls),
            [
                Command(name="noop"),
                Command(name="nope"),
            ],
            window_title="Notepad",
        )

    assert calls == []
    assert runtime.focus_calls == []


def test_a_bad_argument_count_rejects_the_queue_before_anything_runs():
    calls: list[tuple] = []

    with pytest.raises(ValueError, match="commands\\[0\\] 'press_key'"):
        execute_commands(
            FakeRuntime(),
            _handlers(calls),
            [Command(name="press_key", args=[])],
        )

    assert calls == []


def test_too_many_positional_arguments_are_rejected():
    calls: list[tuple] = []

    with pytest.raises(ValueError, match="too many positional arguments"):
        execute_commands(
            FakeRuntime(),
            _handlers(calls),
            [Command(name="noop", args=["extra"])],
        )

    assert calls == []


def test_an_empty_queue_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        execute_commands(FakeRuntime(), _handlers([]), [])


def test_settle_must_be_within_range():
    with pytest.raises(ValueError, match="settle"):
        execute_commands(
            FakeRuntime(),
            _handlers([]),
            [Command(name="noop")],
            settle=3,
        )


def test_sleep_waits_for_the_requested_duration(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "custom_agent_mcp.tools.batch.time.sleep", sleeps.append
    )
    calls: list[tuple] = []

    payload = execute_commands(
        FakeRuntime(),
        _handlers(calls),
        [Command(name="sleep", args=[0.1])],
    )

    assert sleeps == [0.1]
    assert calls == []
    assert payload["ok"] is True
    assert payload["results"][0]["result"] == {"ok": True, "seconds": 0.1}


def test_sleep_accepts_parentheses_in_the_name(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "custom_agent_mcp.tools.batch.time.sleep", sleeps.append
    )

    execute_commands(
        FakeRuntime(),
        _handlers([]),
        [Command(name="sleep()", args=[0.25])],
    )

    assert sleeps == [0.25]


@pytest.mark.parametrize(
    "seconds", [-1, 31, "0.1", True, float("nan"), float("inf")]
)
def test_sleep_rejects_bad_durations_before_anything_runs(seconds):
    calls: list[tuple] = []

    with pytest.raises(ValueError, match="sleep"):
        execute_commands(
            FakeRuntime(),
            _handlers(calls),
            [
                Command(name="noop"),
                Command(name="sleep", args=[seconds]),
            ],
        )

    assert calls == []


def test_sleep_alone_does_not_trigger_the_focus_sequence(monkeypatch):
    monkeypatch.setattr(
        "custom_agent_mcp.tools.batch.time.sleep", lambda seconds: None
    )
    runtime = FakeRuntime()

    execute_commands(
        runtime,
        _handlers([]),
        [Command(name="sleep", args=[0.1])],
    )

    assert runtime.focus_calls == []


def test_sleep_runs_inside_the_focus_guard(monkeypatch):
    runtime = FakeRuntime()

    def fake_sleep(seconds: float) -> None:
        runtime.events.append(("sleep", seconds))

    monkeypatch.setattr("custom_agent_mcp.tools.batch.time.sleep", fake_sleep)

    execute_commands(
        runtime,
        _handlers([]),
        [Command(name="sleep", args=[0.1])],
        window_title="Notepad",
        settle=0,
    )

    assert runtime.events == [
        ("focus_begin", "Notepad"),
        ("sleep", 0.1),
        ("focus_end", "Notepad"),
    ]


def test_screenshot_cannot_be_queued(tmp_path):
    calls: list[tuple] = []
    mcp = FakeMCP()
    handlers = _handlers(calls)
    handlers["screenshot"] = lambda *args: {"ok": True}
    register_batch_tools(
        mcp,
        FakeRuntime(),
        _settings(tmp_path),
        handlers,
    )

    run_commands = mcp.tools["run_commands"]

    with pytest.raises(ToolError, match="unknown function 'screenshot'"):
        run_commands(commands=[Command(name="screenshot")])

    assert calls == []


def test_run_commands_cannot_recurse(tmp_path):
    calls: list[tuple] = []
    mcp = FakeMCP()
    handlers = _handlers(calls)
    handlers["run_commands"] = lambda *args: {"ok": True}
    register_batch_tools(
        mcp,
        FakeRuntime(),
        _settings(tmp_path),
        handlers,
    )

    run_commands = mcp.tools["run_commands"]

    with pytest.raises(ToolError, match="unknown function 'run_commands'"):
        run_commands(commands=[Command(name="run_commands")])

    assert calls == []


def test_web_tools_cannot_be_queued(tmp_path):
    mcp = FakeMCP()
    handlers = _handlers([])
    handlers["web_search"] = lambda *args: {"ok": True}
    handlers["web_fetch"] = lambda *args: {"ok": True}
    register_batch_tools(
        mcp,
        FakeRuntime(),
        _settings(tmp_path),
        handlers,
    )

    run_commands = mcp.tools["run_commands"]

    for name in ("web_search", "web_fetch"):
        with pytest.raises(ToolError, match=f"unknown function '{name}'"):
            run_commands(commands=[Command(name=name)])


def test_run_commands_works_through_the_mcp_client(tmp_path, monkeypatch):
    server = create_server(
        Settings(
            templates_dir=tmp_path / "templates",
            output_dir=tmp_path / "out",
            allow_input=False,
        )
    )
    runtime = server._pyfind_runtime
    monkeypatch.setattr(
        runtime,
        "list_windows",
        lambda query=None, visible_only=True: {
            "query": query,
            "visible_only": visible_only,
            "windows": [],
        },
    )
    monkeypatch.setattr(
        runtime,
        "list_templates",
        lambda: {"count": 0, "templates": []},
    )

    async def call() -> object:
        async with Client(server) as client:
            return await client.call_tool(
                "run_commands",
                {
                    "commands": [
                        {"name": "list_windows", "args": ["Note", False]},
                        {"name": "list_templates", "args": []},
                    ]
                },
            )

    result = asyncio.run(call())

    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["completed"] == 2
    assert payload["results"][0]["result"]["query"] == "Note"
    assert payload["results"][0]["result"]["visible_only"] is False