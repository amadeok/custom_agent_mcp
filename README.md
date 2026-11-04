# custom-agent-mcp

A local [Model Context Protocol](https://modelcontextprotocol.io/) server for Windows automation. The first capability set exposes PyFind (the local screen-automation package in `F:\all\GitHub\autopy2`) screen capture, Windows OCR, and template matching. Optional input tools use loge2 (`F:\all\GitHub\loge2`) to block physical keyboard/mouse input with global hooks while injecting tagged keyboard and mouse events through the block.

The server keeps one `PyFinder` instance alive for its whole lifetime. The first screen tool call pays the PyFind process-pool startup cost; later calls reuse the warm pool and template cache.

## Requirements

- Windows 10/11
- Python 3.11+ (this machine uses Python 3.13)
- PyFind installed in the same interpreter, normally editable from `F:\all\GitHub\autopy2`
- loge2 installed in the same interpreter for input and blocking, normally editable from `F:\all\GitHub\loge2`
- The MCP Python SDK v2 (`mcp>=2.3,<3`)

Verify PyFind and loge2 before installing:

```powershell
python -c "import PyFind.core; print('PyFind OK')"
python -c "import loge2.inject, loge2.local_mouse; print('loge2 OK')"
```

## Install

Install the siblings editable first if they are not already installed:

```powershell
python -m pip install -e F:\all\GitHub\autopy2
python -m pip install -e F:\all\GitHub\loge2
```

Then, from `F:\all\GitHub\custom_agent_mcp`:

```powershell
python -m pip install -e .
```

For tests:

```powershell
python -m pip install -e ".[dev]"
pytest
```

The server entry point is:

```powershell
python -m custom_agent_mcp
```

It speaks MCP over stdio. It does not print a banner.

## OpenCode configuration

OpenCode V2 stores MCP servers under `mcp.servers`. A working global entry looks like this:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "pyfind": {
        "type": "local",
        "command": [
          "C:\\Users\\amade\\AppData\\Local\\Python\\pythoncore-3.13-64\\python.exe",
          "-m",
          "custom_agent_mcp"
        ],
        "cwd": "F:\\all\\GitHub\\custom_agent_mcp",
        "environment": {
          "PYFIND_MCP_TEMPLATES_DIR": "F:\\all\\GitHub\\custom_agent_mcp\\templates",
          "PYFIND_MCP_OUTPUT_DIR": "C:\\Users\\amade\\AppData\\Local\\Temp\\opencode\\pyfind-mcp",
          "PYFIND_MCP_ALLOW_INPUT": "false",
          "PYFIND_MCP_ALLOW_INPUT_GATE": "false",
          "PYFIND_MCP_INPUT_GATE_HOTKEY": "ctrl+alt+shift+f12",
          "PYFIND_MCP_INPUT_GATE_MAX_TTL": "600",
          "PYFIND_MCP_LOG_LEVEL": "WARNING"
        },
        "codemode": false,
        "timeout": {
          "startup": 45000,
          "catalog": 30000,
          "execution": 300000
        }
      }
    }
  }
}
```

Set both `PYFIND_MCP_ALLOW_INPUT` and `PYFIND_MCP_ALLOW_INPUT_GATE` to `"true"` to expose the injection and blocking tools, then restart OpenCode or reconnect the MCP server. The gate requires input tools because its purpose is to let this server inject while physical input is blocked.

`codemode: false` exposes the tools directly as `pyfind_screenshot`, `pyfind_ocr`, `pyfind_find_image`, and so on. With Code Mode enabled, call them through `execute` as `tools.pyfind.screenshot(...)`.

After editing the config, check the connection:

```powershell
opencode mcp list
```

Or use `/mcps` inside OpenCode.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PYFIND_MCP_TEMPLATES_DIR` | `<cwd>/templates` | Directory containing top-level PNG templates |
| `PYFIND_MCP_OUTPUT_DIR` | `%TEMP%\opencode\pyfind-mcp` | Screenshot output directory |
| `PYFIND_MCP_ALLOW_INPUT` | `false` | Register the injection tools |
| `PYFIND_MCP_ALLOW_INPUT_GATE` | `false` | Register the blocking tools; requires `ALLOW_INPUT=true` |
| `PYFIND_MCP_INPUT_GATE_HOTKEY` | `ctrl+alt+shift+f12` | Physical emergency-release combo; `none` disables it |
| `PYFIND_MCP_INPUT_GATE_MAX_TTL` | `600` | Maximum optional auto-release TTL in seconds |
| `PYFIND_MCP_MAX_WORKERS` | `2` | PyFind multiprocessing pool size |
| `PYFIND_MCP_THRESHOLD` | `0.8` | Default template-match threshold |
| `PYFIND_MCP_DEFAULT_TIMEOUT` | `10` | Default PyFind timeout in seconds |
| `PYFIND_MCP_MAX_TIMEOUT` | `120` | Maximum wait timeout accepted by a tool |
| `PYFIND_MCP_PREVIEW_MAX_WIDTH` | `1600` | Longest preview dimension returned as MCP image content |
| `PYFIND_MCP_MAX_OUTPUT_FILES` | `100` | Number of saved screenshots kept in the output directory |
| `PYFIND_MCP_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

## Tools

Observation tools are always available. Injection tools require `PYFIND_MCP_ALLOW_INPUT=true`; blocking tools additionally require `PYFIND_MCP_ALLOW_INPUT_GATE=true`.

| Tool | Access | Description |
|---|---|---|
| `list_windows` | read-only | Window titles, handles, bounds, minimized/active state |
| `list_monitors` | read-only | Physical monitors and virtual desktop bounds |
| `screenshot` | read-only | Capture screen/monitor/window/region; returns a preview image and full path |
| `ocr` | read-only | Windows OCR with absolute line and optional word boxes; prefer model vision when available |
| `find_text` | read-only | PyFind text matching with `in`, `=`, `startswith`, `endswith` |
| `find_image` | read-only | PyFind PNG template matching with threshold and wait timeout |
| `list_templates` | read-only | Available PNG templates and image dimensions |
| `reload_templates` | read-only | Clear and rebuild the template cache |
| `click` | input | Tagged click or move at absolute coordinates; optional held modifiers |
| `move_mouse` | input | Move the pointer to absolute coordinates |
| `move_mouse_by` | input | Move the pointer by an exact pixel offset |
| `drag` | input | Press, move, and release a mouse button safely |
| `type_text` | input | Type Unicode text via `loge2.inject` |
| `press_key` | input | Press a key or hotkey such as `ctrl+shift+s` |
| `scroll` | input | Scroll the mouse wheel vertically or horizontally |
| `cursor_position` | read-only | Current pointer position |
| `block_user_input` | gate | Block physical keyboard/mouse input; persistent unless `ttl` is passed |
| `unblock_user_input` | gate | Release the physical input block |
| `input_block_status` | read-only | Blocked/running state, hotkey, remaining TTL |

### Input blocking

`block_user_input` installs `WH_KEYBOARD_LL` and `WH_MOUSE_LL` hooks in the MCP process and swallows every physical event for the selected devices. By default the block is **persistent**: it ends only when:

1. `unblock_user_input` is called,
2. the physical emergency hotkey is pressed (`Ctrl+Alt+Shift+F12` by default), or
3. the MCP process exits (Windows removes the hooks).

Pass `ttl` to `block_user_input` to arm an automatic release timer; the default maximum is 600 seconds. Calling `block_user_input` again extends or re-arms the block.

While the gate is active, the input-action tools inject with loge2's passthrough tag, so their clicks, moves, keys, and text still reach the desktop. Untagged synthetic input from other processes is blocked too. Injected events never trigger the emergency hotkey.

The emergency hotkey works even when only the mouse is blocked because the keyboard hook is installed in observe-only mode for the hotkey. If `PYFIND_MCP_INPUT_GATE_HOTKEY` is set to `none`, use `Ctrl+Alt+Del` and Task Manager to end the MCP process if a persistent gate must be released.

### Coordinates

All rectangles are absolute virtual-screen pixels:

```json
{"x": 0, "y": 0, "width": 800, "height": 600}
```

Secondary monitors can have negative `x` or `y`. Use at most one of `window_title`, `monitor`, or `region` in a call. With none of them, the full virtual desktop is used.

`monitor` is the index returned by `list_monitors`, starting at `0`.

### Templates

Put PNG files in `templates/`:

```text
templates/
├─ save_button.png
└─ dialog_icon.png
```

Call `find_image` with `templates: ["save_button"]`, `["save_button.png"]`, or an absolute PNG path. `reload_templates` is only needed after adding or replacing files. For a one-off template, use `screenshot` on the smallest region that contains the target and pass the returned full path; an entire-desktop screenshot is usually too large to match against a smaller region.

Mask templates, directories of templates, and multi-scale matching are not supported in v1.

### Screenshots

`screenshot` writes a full-resolution PNG to the output directory and returns a downscaled preview as MCP image content. The text block contains the full path, preview path, dimensions, capture region, and target. Use the full path as a template source when convenient.

## Typical workflow

1. `list_windows` or `list_monitors` to find the target.
2. `screenshot` or `ocr` to inspect it. If the model has native vision, prefer
   the screenshot: it preserves layout and icons and usually reads text more
   reliably. `ocr` uses Windows OCR (not PaddleOCR), so its text and boxes can
   be imperfect; use it when the model cannot see images or when plain text is
   enough.
3. `find_text` / `find_image` to get absolute coordinates.
4. If blocking is needed, call `block_user_input` before the interaction.
5. Use `click`, `move_mouse`, `drag`, `type_text`, `press_key`, or `scroll`.
6. `unblock_user_input` when the test finishes. Take another screenshot to verify the result.

## Safety

- Input control is off by default. Set `PYFIND_MCP_ALLOW_INPUT=true` only when you intend to let the agent click and type.
- Blocking is off by default and needs a second opt-in, `PYFIND_MCP_ALLOW_INPUT_GATE=true`.
- A persistent block can lock the human out of the keyboard and mouse. Use `ttl` for unattended runs, and always call `unblock_user_input` after a test.
- The emergency hotkey (`Ctrl+Alt+Shift+F12` by default) releases the gate from physical input only. `Ctrl+Alt+Del` still opens the secure desktop, so Task Manager can end the MCP process if necessary.
- Global hooks are not a security boundary. Any process that knows loge2's `dwExtraInfo` tag can forge a passthrough event, and elevated windows may ignore injected input because of UIPI.
- Screen captures may contain passwords, tokens, or private content. Output stays in the local temp directory and is never uploaded by this server.
- The agent sees the screen; do not run it against sensitive desktops unattended.

## Limitations

- Windows only.
- MSS captures only visible desktop content. Minimized or fully occluded windows cannot be read.
- `ocr` wraps Windows OCR, whose text and line/word boxes can be approximate; this server does not use PaddleOCR. Vision-capable models should read a `screenshot` instead.
- `find_text` uses PyFind's fixed `en-US` Windows OCR path. The `ocr` tool accepts other `lang` values. OCR matches report `confidence: 0.0`; use their rectangles, not the score.
- `find_image` matches at native scale only. Overlapping matches above the threshold can appear; the output is capped by `max_results`.
- `type_text` uses `KEYEVENTF_UNICODE`; a few legacy applications ignore that input path. Named keys such as `enter` and `tab` should use `press_key`.
- The input gate is global to the interactive desktop session, not per-window. It blocks the human's input in every application, including OpenCode.
- The PyFind pool costs a few hundred MB and takes a moment on the first screen call.
- A stuck PyFind worker can hang a tool call; OpenCode's execution timeout returns an error, but reconnecting the server is the clean recovery.

## Development

```powershell
pytest
pytest -m integration tests/test_live_pyfind.py -s
pytest -m integration tests/test_live_input_gate.py -s
```

`tests/test_live_pyfind.py` initializes the real PyFinder pool and captures the desktop. `tests/test_live_input_gate.py` installs the real keyboard/mouse hooks, briefly blocks physical input, verifies tagged injection passes, and exercises the emergency hotkey with harmless F16 chords. Run it only on an interactive desktop.

To exercise the stdio protocol without OpenCode:

```powershell
python -m pytest -q tests/test_stdio_smoke.py
```

## Troubleshooting

- `PyFind is not installed`: install the editable package from `F:\all\GitHub\autopy2`.
- `loge2 is not installed`: install the editable package from `F:\all\GitHub\loge2` in the same interpreter that runs the MCP server.
- `block_user_input` fails to install a hook: make sure the server runs in an interactive desktop session, not a service or session-0 process.
- `opencode mcp list` shows `failed`: check the absolute Python path, `cwd`, and that `python -m custom_agent_mcp` starts without an immediate traceback.
- No OCR text: confirm the Windows OCR language pack is installed and try a smaller region.
- `find_image` misses: use the actual on-screen pixels as the template, raise the region size, or lower `threshold`.
- Protocol errors: set `PYFIND_MCP_LOG_LEVEL=DEBUG` and inspect OpenCode's MCP logs; PyFind diagnostics are redirected to stderr so stdout stays clean.