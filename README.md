# custom-agent-mcp

A local [Model Context Protocol](https://modelcontextprotocol.io/) server for Windows automation. The first capability set exposes PyFind (the local screen-automation package in `F:\all\GitHub\autopy2`) screen capture, Windows OCR, template matching, and optional mouse/keyboard input.

The server keeps one `PyFinder` instance alive for its whole lifetime. The first screen tool call pays the PyFind process-pool startup cost; later calls reuse the warm pool and template cache.

## Requirements

- Windows 10/11
- Python 3.11+ (this machine uses Python 3.13)
- PyFind installed in the same interpreter, normally editable from `F:\all\GitHub\autopy2`
- The MCP Python SDK v2 (`mcp>=2.3,<3`)

Verify PyFind before installing:

```powershell
python -c "import PyFind.core; print('PyFind OK')"
```

## Install

From `F:\all\GitHub\custom_agent_mcp`:

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
| `PYFIND_MCP_ALLOW_INPUT` | `false` | Register `click`, `type_text`, `press_key`, `scroll` |
| `PYFIND_MCP_MAX_WORKERS` | `2` | PyFind multiprocessing pool size |
| `PYFIND_MCP_THRESHOLD` | `0.8` | Default template-match threshold |
| `PYFIND_MCP_DEFAULT_TIMEOUT` | `10` | Default PyFind timeout in seconds |
| `PYFIND_MCP_MAX_TIMEOUT` | `120` | Maximum wait timeout accepted by a tool |
| `PYFIND_MCP_PREVIEW_MAX_WIDTH` | `1600` | Longest preview dimension returned as MCP image content |
| `PYFIND_MCP_MAX_OUTPUT_FILES` | `100` | Number of saved screenshots kept in the output directory |
| `PYFIND_MCP_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |

## Tools

Observation tools are always available. Input tools are only registered when `PYFIND_MCP_ALLOW_INPUT=true`.

| Tool | Access | Description |
|---|---|---|
| `list_windows` | read-only | Window titles, handles, bounds, minimized/active state |
| `list_monitors` | read-only | Physical monitors and virtual desktop bounds |
| `screenshot` | read-only | Capture screen/monitor/window/region; returns a preview image and full path |
| `ocr` | read-only | Windows OCR with absolute line and optional word boxes |
| `find_text` | read-only | PyFind text matching with `in`, `=`, `startswith`, `endswith` |
| `find_image` | read-only | PyFind PNG template matching with threshold and wait timeout |
| `list_templates` | read-only | Available PNG templates and image dimensions |
| `reload_templates` | read-only | Clear and rebuild the template cache |
| `click` | input | Click or move at absolute coordinates |
| `type_text` | input | Type text through PyAutoGUI |
| `press_key` | input | Press a key or a hotkey such as `ctrl+shift+s` |
| `scroll` | input | Scroll the mouse wheel |

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
2. `screenshot` or `ocr` to inspect it.
3. `find_text` / `find_image` to get absolute coordinates.
4. If input is enabled, `click`, `type_text`, `press_key`, or `scroll`.
5. Take another screenshot to verify the result.

## Safety

- Input control is off by default. Set `PYFIND_MCP_ALLOW_INPUT=true` only when you intend to let the agent click and type.
- Screen captures may contain passwords, tokens, or private content. Output stays in the local temp directory and is never uploaded by this server.
- PyAutoGUI's top-left failsafe remains enabled. A click near `(0, 0)` can raise a failsafe error by design.
- The agent sees the screen; do not run it against sensitive desktops unattended.

## Limitations

- Windows only.
- MSS captures only visible desktop content. Minimized or fully occluded windows cannot be read.
- `find_text` uses PyFind's fixed `en-US` Windows OCR path. The `ocr` tool accepts other `lang` values. OCR matches report `confidence: 0.0`; use their rectangles, not the score.
- `find_image` matches at native scale only. Overlapping matches above the threshold can appear; the output is capped by `max_results`.
- PyAutoGUI text entry is ASCII-oriented. Unicode input may need a clipboard-based tool later.
- The PyFind pool costs a few hundred MB and takes a moment on the first screen call.
- A stuck PyFind worker can hang a tool call; OpenCode's execution timeout returns an error, but reconnecting the server is the clean recovery.

## Development

```powershell
pytest
pytest -m integration tests/test_live_pyfind.py -s
```

The integration test initializes the real PyFinder pool and captures the desktop.

To exercise the stdio protocol without OpenCode:

```powershell
python -m pytest -q tests/test_stdio_smoke.py
```

## Troubleshooting

- `PyFind is not installed`: install the editable package from `F:\all\GitHub\autopy2`.
- `opencode mcp list` shows `failed`: check the absolute Python path, `cwd`, and that `python -m custom_agent_mcp` starts without an immediate traceback.
- No OCR text: confirm the Windows OCR language pack is installed and try a smaller region.
- `find_image` misses: use the actual on-screen pixels as the template, raise the region size, or lower `threshold`.
- Protocol errors: set `PYFIND_MCP_LOG_LEVEL=DEBUG` and inspect OpenCode's MCP logs; PyFind diagnostics are redirected to stderr so stdout stays clean.