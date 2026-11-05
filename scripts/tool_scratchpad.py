"""Tkinter scratchpad for editing and calling PyFind MCP tool arguments.

Pick a tool, edit its arguments as real JSON (no shell quoting), format or
validate them, and call the tool.  Two modes are available:

* in-process (default): builds its own server and talks to it over the SDK's
  in-memory transport.  Breakpoints in ``src/custom_agent_mcp`` hit here.
* attached: connects over streamable HTTP to a shared server that OpenCode
  (or anything else) is also using, so both see the same runtime and input
  gate state.  Start that server with::

      python -m custom_agent_mcp --transport streamable-http --port 8765

    python scripts/tool_scratchpad.py
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import tkinter as tk
import traceback
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any

from mcp import Client
from mcp.types import CallToolResult, Tool

from custom_agent_mcp.server import create_server
from custom_agent_mcp.settings import Settings, load_settings

DEFAULT_SERVER_URL = "http://127.0.0.1:8765/mcp"


# --------------------------------------------------------------------------- #
# JSON schema helpers
# --------------------------------------------------------------------------- #
def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _pick_branch(schema: dict[str, Any]) -> dict[str, Any]:
    """Pick the first non-null branch of anyOf/oneOf."""
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if options:
            for option in options:
                if option.get("type") != "null":
                    return option
    return schema


def make_sample(schema: dict[str, Any], root: dict[str, Any] | None = None, include_all: bool = False) -> Any:
    """Build a starter value for a JSON schema node."""
    root = root if root is not None else schema
    schema = _pick_branch(schema or {})

    if "$ref" in schema:
        return make_sample(_resolve_ref(root, schema["$ref"]), root, include_all)

    if schema.get("default") is not None:
        return schema["default"]

    enum = schema.get("enum")
    if enum:
        return enum[0]

    stype = schema.get("type")
    if stype == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        sample: dict[str, Any] = {}
        for name, prop in properties.items():
            if include_all or name in required:
                sample[name] = make_sample(prop, root, include_all)
        return sample

    if stype == "array":
        return []

    if stype in ("integer", "number"):
        return schema.get("minimum", 0)

    if stype == "boolean":
        return False

    if stype == "string":
        return ""

    return None


def tool_template(tool: Tool, include_all: bool = False) -> dict[str, Any]:
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    sample = make_sample(schema, schema, include_all)
    return sample if isinstance(sample, dict) else {}


def format_content(result: CallToolResult) -> str:
    """Render a tool result without dumping base64 image payloads."""
    lines: list[str] = []
    if result.is_error:
        lines.append("ERROR")
        lines.append("")

    for item in result.content or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            text = getattr(item, "text", "")
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                pass
            lines.append(text)
        elif kind == "image":
            mime = getattr(item, "mime_type", None) or "image/?"
            data = getattr(item, "data", "") or ""
            lines.append(f"[image {mime}, {len(data)} base64 chars]")
        else:
            payload = item.model_dump(mode="json", by_alias=True, exclude_none=True)
            lines.append(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    if not lines:
        lines.append("(empty result)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Background asyncio loop that owns the server session
# --------------------------------------------------------------------------- #
class Bridge:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="mcp-loop", daemon=True)
        self.thread.start()
        self.client: Client | None = None
        self.server: Any = None
        self.attached = False

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _open(self, settings: Settings) -> list[Tool]:
        self.attached = False
        self.server = create_server(settings)
        self.client = Client(self.server)
        await self.client.__aenter__()
        return list((await self.client.list_tools()).tools)

    def open(self, settings: Settings) -> Any:
        return self.submit(self._open(settings))

    async def _attach(self, url: str) -> list[Tool]:
        self.attached = True
        self.client = Client(url)
        await self.client.__aenter__()
        return list((await self.client.list_tools()).tools)

    def attach(self, url: str) -> Any:
        return self.submit(self._attach(url))

    async def _close(self) -> None:
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None
        if not self.attached:
            runtime = getattr(self.server, "_pyfind_runtime", None)
            if runtime is not None:
                runtime.close()
        self.server = None

    def close(self) -> None:
        try:
            self.submit(self._close()).result(timeout=15)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Dark theme
# --------------------------------------------------------------------------- #
BG = "#1e1e1e"
SURFACE = "#252526"
EDITOR_BG = "#1e1e1e"
FG = "#d4d4d4"
MUTED = "#8b8b8b"
BORDER = "#3c3c3c"
ACCENT = "#0e639c"
ACCENT_HOVER = "#1177bb"
ACCENT_PRESSED = "#0a4d78"
BUTTON_BG = "#333333"
BUTTON_HOVER = "#3f3f46"
DISABLED_FG = "#6b6b6b"
SELECT_BG = "#264f78"
ERROR_FG = "#f48771"


def apply_dark_theme(root: tk.Tk) -> None:
    """Apply a flat VS Code-like dark palette.

    The built-in Windows theme (vista) ignores most color options, so switch to
    clam first; it is available everywhere and fully configurable.
    """
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(bg=BG)

    style.configure(
        ".",
        background=BG,
        foreground=FG,
        fieldbackground=SURFACE,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        troughcolor=BG,
        focuscolor=ACCENT,
        selectbackground=SELECT_BG,
        selectforeground="#ffffff",
        font=("Segoe UI", 9),
    )
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(8, 4))
    style.configure(
        "TButton",
        background=BUTTON_BG,
        foreground=FG,
        bordercolor=BORDER,
        lightcolor=BUTTON_BG,
        darkcolor=BUTTON_BG,
        padding=(10, 4),
    )
    style.map(
        "TButton",
        background=[
            ("disabled", SURFACE),
            ("pressed", ACCENT_PRESSED),
            ("active", BUTTON_HOVER),
        ],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#ffffff",
        bordercolor=ACCENT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    style.map(
        "Accent.TButton",
        background=[
            ("disabled", SURFACE),
            ("pressed", ACCENT_PRESSED),
            ("active", ACCENT_HOVER),
        ],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "TCheckbutton",
        background=BG,
        foreground=FG,
        indicatorbackground=SURFACE,
        indicatorforeground=FG,
        focuscolor=BG,
    )
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        indicatorbackground=[("selected", ACCENT), ("active", SURFACE)],
        indicatorforeground=[("selected", "#ffffff")],
    )
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=BUTTON_BG,
        foreground=FG,
        arrowcolor=FG,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        selectbackground=SELECT_BG,
        selectforeground="#ffffff",
        padding=(6, 3),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE), ("disabled", BG)],
        foreground=[("readonly", FG), ("disabled", DISABLED_FG)],
        background=[("active", BUTTON_HOVER), ("pressed", BUTTON_HOVER)],
        arrowcolor=[("active", "#ffffff")],
    )
    style.configure(
        "TScrollbar",
        background=BUTTON_BG,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=FG,
        gripcount=0,
    )
    style.map(
        "TScrollbar",
        background=[("active", "#4b4b52"), ("pressed", "#5a5a62")],
        arrowcolor=[("disabled", DISABLED_FG)],
    )
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", background=BORDER, sashthickness=6, gripcount=0)
    style.configure("TSeparator", background=BORDER)

    for option, value in (
        ("background", SURFACE),
        ("foreground", FG),
        ("selectBackground", SELECT_BG),
        ("selectForeground", "#ffffff"),
        ("font", "Segoe UI 9"),
    ):
        root.option_add(f"*TCombobox*Listbox.{option}", value)


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class ScratchpadApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        apply_dark_theme(self.root)
        self.root.title("PyFind MCP Scratchpad")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1100, screen_w - 80)
        height = min(760, screen_h - 120)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(820, 520)

        self.events: queue.Queue[tuple[Any, tuple[Any, ...]]] = queue.Queue()
        self.bridge: Bridge | None = None
        self.tools: dict[str, Tool] = {}
        self.busy = False
        self.settings = load_settings()

        self.tool_var = tk.StringVar()
        self.allow_input_var = tk.BooleanVar(value=self.settings.allow_input)
        self.allow_gate_var = tk.BooleanVar(value=self.settings.allow_input_gate)
        self.attach_var = tk.BooleanVar(value=bool(os.getenv("PYFIND_MCP_URL")))
        self.url_var = tk.StringVar(value=os.getenv("PYFIND_MCP_URL", DEFAULT_SERVER_URL))
        self.info_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Starting server...")

        self._build_ui()
        self._sync_attach()
        self._refresh_info()
        self.root.after(60, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._reload()

    # -- layout ------------------------------------------------------------ #
    def _build_ui(self) -> None:
        mono = ("Consolas", 10)

        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        # Row 1: tool choice and server settings.
        top = ttk.Frame(outer)
        top.pack(fill="x")

        ttk.Label(top, text="Tool").pack(side="left")
        self.tool_combo = ttk.Combobox(
            top, textvariable=self.tool_var, state="readonly", width=24
        )
        self.tool_combo.pack(side="left", padx=(6, 8))
        self.tool_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_template(False))

        ttk.Button(top, text="Template", command=lambda: self._load_template(False)).pack(side="left")
        ttk.Button(top, text="All fields", command=lambda: self._load_template(True)).pack(
            side="left", padx=(4, 0)
        )

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=12)

        self.input_check = ttk.Checkbutton(
            top,
            text="allow input",
            variable=self.allow_input_var,
            command=self._sync_gate_check,
        )
        self.input_check.pack(side="left")
        self.gate_check = ttk.Checkbutton(
            top, text="allow input gate", variable=self.allow_gate_var
        )
        self.gate_check.pack(side="left", padx=(8, 0))
        self.reload_button = ttk.Button(
            top, text="Reload server", command=self._reload
        )
        self.reload_button.pack(side="left", padx=(12, 0))

        # Row 2: attach to a shared server instead of hosting one in-process.
        attach_row = ttk.Frame(outer)
        attach_row.pack(fill="x", pady=(8, 0))
        self.attach_check = ttk.Checkbutton(
            attach_row,
            text="Attach to running server",
            variable=self.attach_var,
            command=self._sync_attach,
        )
        self.attach_check.pack(side="left")
        self.url_entry = ttk.Entry(attach_row, textvariable=self.url_var, width=38)
        self.url_entry.pack(side="left", padx=(8, 0))

        # Row 3: context info.
        ttk.Label(outer, textvariable=self.info_var, style="Muted.TLabel").pack(
            fill="x", pady=(6, 6)
        )

        # Main split: arguments | result.
        paned = ttk.PanedWindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)

        args_frame = ttk.Frame(paned, padding=(0, 0, 6, 0))
        result_frame = ttk.Frame(paned, padding=(6, 0, 0, 0))
        paned.add(args_frame, weight=1)
        paned.add(result_frame, weight=1)

        ttk.Label(args_frame, text="Arguments (JSON object)").pack(anchor="w")
        self.args_text = self._make_text(args_frame, mono)

        args_buttons = ttk.Frame(args_frame)
        args_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(args_buttons, text="Format", command=self._format_args).pack(side="left")
        ttk.Button(args_buttons, text="Copy JSON", command=self._copy_args).pack(
            side="left", padx=(4, 0)
        )
        self.call_button = ttk.Button(
            args_buttons,
            text="Call  (Ctrl+Enter)",
            command=self._call,
            style="Accent.TButton",
        )
        self.call_button.pack(side="right")

        ttk.Label(result_frame, text="Result").pack(anchor="w")
        self.result_text = self._make_text(result_frame, mono)
        self.result_text.tag_configure("error", foreground=ERROR_FG)
        self.result_text.configure(state="disabled")

        ttk.Label(outer, textvariable=self.status_var, anchor="w", style="Status.TLabel").pack(
            fill="x", pady=(8, 0)
        )

        self.root.bind("<Control-Return>", lambda _event: self._call())

    def _make_text(self, parent: ttk.Frame, font: tuple[str, int]) -> tk.Text:
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True, pady=(4, 0))
        text = tk.Text(
            holder,
            wrap="word",
            font=font,
            undo=True,
            height=20,
            background=EDITOR_BG,
            foreground=FG,
            insertbackground="#aeafad",
            selectbackground=SELECT_BG,
            selectforeground="#ffffff",
            inactiveselectbackground=SELECT_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            borderwidth=0,
            relief="flat",
            padx=8,
            pady=6,
        )
        scroll = ttk.Scrollbar(holder, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    # -- helpers ----------------------------------------------------------- #
    def _sync_gate_check(self) -> None:
        if not self.allow_input_var.get():
            self.allow_gate_var.set(False)

    def _sync_attach(self) -> None:
        attached = self.attach_var.get()
        state = "disabled" if attached else "normal"
        self.input_check.configure(state=state)
        self.gate_check.configure(state=state)
        self.url_entry.configure(state="normal" if attached else "disabled")
        self.reload_button.configure(text="Connect" if attached else "Reload server")

    def _refresh_info(self) -> None:
        if self.attach_var.get():
            self.info_var.set(
                f"shared server: {self.url_var.get().strip()}"
                "    (runtime and settings belong to that process)"
            )
        else:
            self.info_var.set(
                f"templates: {self.settings.templates_dir}    output: {self.settings.output_dir}"
            )

    def _status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.call_button.configure(state="disabled" if busy else "normal")

    def _post(self, callback: Any, *args: Any) -> None:
        """Queue a UI callback from any thread; drained on the Tk thread."""
        self.events.put((callback, args))

    def _drain_events(self) -> None:
        while True:
            try:
                callback, args = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                traceback.print_exc()
        self.root.after(60, self._drain_events)

    def _set_args(self, value: dict[str, Any]) -> None:
        self.args_text.delete("1.0", "end")
        self.args_text.insert("1.0", json.dumps(value, indent=2, ensure_ascii=False))

    def _load_template(self, include_all: bool) -> None:
        tool = self.tools.get(self.tool_var.get())
        if tool is None:
            return
        self._set_args(tool_template(tool, include_all))
        self._status(f"{tool.name}: template loaded")

    def _current_args(self) -> dict[str, Any] | None:
        raw = self.args_text.get("1.0", "end").strip() or "{}"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._status(f"Invalid JSON: {exc}")
            messagebox.showerror("Invalid JSON", f"{exc}\n\nLine {exc.lineno}, column {exc.colno}")
            return None
        if not isinstance(value, dict):
            self._status("Arguments must be a JSON object")
            messagebox.showerror("Invalid arguments", "Top-level JSON value must be an object.")
            return None
        return value

    def _format_args(self) -> None:
        value = self._current_args()
        if value is None:
            return
        self._set_args(value)
        self._status("JSON is valid")

    def _copy_args(self) -> None:
        value = self._current_args()
        if value is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(json.dumps(value, ensure_ascii=False))
        self._status("Compact JSON copied to clipboard")

    # -- lifecycle -------------------------------------------------------- #
    def _reload(self) -> None:
        if self.busy:
            return
        self._sync_gate_check()
        attach = self.attach_var.get()
        url = self.url_var.get().strip()
        if attach and not url:
            self._status("Enter the shared server URL first")
            return

        base = load_settings()
        self.settings = (
            base
            if attach
            else replace(
                base,
                allow_input=self.allow_input_var.get(),
                allow_input_gate=self.allow_gate_var.get() and self.allow_input_var.get(),
            )
        )
        self._refresh_info()

        if self.bridge is not None:
            self._status("Closing previous connection...")
            self.root.update_idletasks()
            self.bridge.close()
            self.bridge = None

        self.tools.clear()
        self.tool_combo["values"] = []
        self._status("Connecting..." if attach else "Starting server...")
        self.root.update_idletasks()

        self.bridge = Bridge()
        future = self.bridge.attach(url) if attach else self.bridge.open(self.settings)
        future.add_done_callback(lambda f: self._post(self._opened, f))

    def _opened(self, future: Any) -> None:
        try:
            tools = future.result()
        except Exception as exc:
            if self.attach_var.get():
                self._status(
                    f"Attach failed - is the shared server running at {self.url_var.get().strip()}?"
                )
            else:
                self._status("Server failed to start")
            self._show_error(exc)
            return

        self.tools = {tool.name: tool for tool in tools}
        names = list(self.tools)
        self.tool_combo["values"] = names
        if names:
            self.tool_var.set(names[0])
            self._load_template(False)

        if self.bridge is not None and self.bridge.attached:
            self._status(
                f"Attached - {len(names)} tools from {self.url_var.get().strip()}"
            )
            return

        flags = ", ".join(
            flag
            for flag, enabled in (
                ("input", self.settings.allow_input),
                ("gate", self.settings.allow_input_gate),
            )
            if enabled
        )
        self._status(f"Ready - {len(names)} tools" + (f" (enabled: {flags})" if flags else ""))

    # -- calling ---------------------------------------------------------- #
    def _call(self) -> None:
        if self.busy or self.bridge is None or self.bridge.client is None:
            return
        tool = self.tools.get(self.tool_var.get())
        if tool is None:
            self._status("Select a tool first")
            return
        arguments = self._current_args()
        if arguments is None:
            return

        self._set_busy(True)
        self._status(f"Calling {tool.name}...")
        started = time.perf_counter()
        future = self.bridge.submit(self.bridge.client.call_tool(tool.name, arguments))
        future.add_done_callback(
            lambda f: self._post(self._call_done, tool.name, started, f)
        )

    def _call_done(self, name: str, started: float, future: Any) -> None:
        elapsed = time.perf_counter() - started
        try:
            result = future.result()
        except Exception as exc:
            self._show_error(exc)
            self._status(f"{name} failed after {elapsed:.2f}s")
        else:
            self._set_result(format_content(result), result.is_error)
            state = "error" if result.is_error else "ok"
            self._status(f"{name}: {state} in {elapsed:.2f}s")
        finally:
            self._set_busy(False)

    def _set_result(self, text: str, is_error: bool = False) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        if is_error:
            self.result_text.tag_add("error", "1.0", "end")
        self.result_text.configure(state="disabled")

    def _show_error(self, exc: BaseException) -> None:
        self._set_result(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            is_error=True,
        )

    def _on_close(self) -> None:
        if self.bridge is not None:
            self._status("Shutting down...")
            self.root.update_idletasks()
            self.bridge.close()
            self.bridge = None
        self.root.destroy()


def main() -> None:
    try:
        load_settings()
    except ValueError as exc:
        messagebox.showerror("Invalid PYFIND_MCP_* environment", str(exc))
        raise SystemExit(1) from exc

    root = tk.Tk()
    ScratchpadApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
