# custom-agent-mcp

A local [Model Context Protocol](https://modelcontextprotocol.io/) server for Windows automation and web research. The PyFind capability set exposes screen capture, Windows OCR, and template matching from the local screen-automation package in `F:\all\GitHub\autopy2`. Optional input tools use loge2 (`F:\all\GitHub\loge2`) to block physical keyboard/mouse input with global hooks while injecting tagged keyboard and mouse events through the block. The SearXNG capability adds `web_search` and `web_fetch`: searches go through a SearXNG JSON API, and fetched HTML is converted to readable text for the agent.

The server keeps one `PyFinder` instance alive for its whole lifetime. The first screen tool call pays the PyFind process-pool startup cost; later calls reuse the warm pool and template cache.

## Requirements

- Windows 10/11
- Python 3.11+ (this machine uses Python 3.13)
- PyFind installed in the same interpreter, normally editable from `F:\all\GitHub\autopy2`
- loge2 installed in the same interpreter for input and blocking, normally editable from `F:\all\GitHub\loge2`
- The MCP Python SDK v2 (`mcp>=2.3,<3`)
- A reachable SearXNG instance with JSON output enabled for `web_search`; `web_fetch` only needs network access (the web tools add no Python dependency)

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
          "PYFIND_MCP_BEEP": "true",
          "PYFIND_MCP_IMAGE_FORMAT": "png",
          "PYFIND_MCP_WEBP_QUALITY": "90",
          "PYFIND_MCP_WEBP_LOSSLESS": "false",
          "PYFIND_MCP_LOG_LEVEL": "WARNING",
          "SEARXNG_MCP_BASE_URL": "http://127.0.0.1:8080",
          "SEARXNG_MCP_TIMEOUT": "15",
          "SEARXNG_MCP_USER_AGENT": "custom-agent-mcp/0.1 (SearXNG client)"
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

`codemode: false` exposes the tools directly as `pyfind_screenshot`, `pyfind_ocr`, `pyfind_find_image`, `pyfind_web_search`, and so on. Ordered input batching does not require Code Mode: call `run_commands` directly with a JSON list of commands. With Code Mode enabled, call it through `execute` as `tools.pyfind.run_commands(...)`.

After editing the config, check the connection:

```powershell
opencode mcp list
```

Or use `/mcps` inside OpenCode.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PYFIND_MCP_TEMPLATES_DIR` | `<cwd>/templates` | Directory containing top-level PNG/WebP templates |
| `PYFIND_MCP_OUTPUT_DIR` | `%TEMP%\opencode\pyfind-mcp` | Screenshot output directory |
| `PYFIND_MCP_ALLOW_INPUT` | `false` | Register the injection tools |
| `PYFIND_MCP_ALLOW_INPUT_GATE` | `false` | Register the task/blocking tools; requires `ALLOW_INPUT=true` |
| `PYFIND_MCP_INPUT_GATE_HOTKEY` | `ctrl+alt+shift+f12` | Physical emergency-release combo; `none` disables it |
| `PYFIND_MCP_INPUT_GATE_MAX_TTL` | `600` | Maximum optional auto-release TTL in seconds |
| `PYFIND_MCP_BEEP` | `true` | Play winsound cues: task start/end and a short blip per injection |
| `PYFIND_MCP_IMAGE_FORMAT` | `png` | Screenshot format: `png` or `webp` |
| `PYFIND_MCP_WEBP_QUALITY` | `90` | Lossy WebP quality, 1-100 |
| `PYFIND_MCP_WEBP_LOSSLESS` | `false` | Use lossless WebP instead of lossy |
| `PYFIND_MCP_MAX_WORKERS` | `2` | PyFind multiprocessing pool size |
| `PYFIND_MCP_THRESHOLD` | `0.8` | Default template-match threshold |
| `PYFIND_MCP_DEFAULT_TIMEOUT` | `10` | Default PyFind timeout in seconds |
| `PYFIND_MCP_MAX_TIMEOUT` | `120` | Maximum wait timeout accepted by a tool |
| `PYFIND_MCP_PREVIEW_MAX_WIDTH` | `1600` | Longest preview dimension returned as MCP image content |
| `PYFIND_MCP_MAX_OUTPUT_FILES` | `100` | Number of saved screenshots kept in the output directory |
| `PYFIND_MCP_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `SEARXNG_MCP_BASE_URL` | `http://127.0.0.1:8080` | SearXNG base URL; `/search` is appended (a trailing `/search` is stripped) |
| `SEARXNG_MCP_TIMEOUT` | `15` | HTTP timeout in seconds for `web_search` and `web_fetch` |
| `SEARXNG_MCP_USER_AGENT` | `custom-agent-mcp/0.1 (SearXNG client)` | User-Agent sent to SearXNG and fetched pages |

## Tools

Observation and web tools are always available. Injection tools require `PYFIND_MCP_ALLOW_INPUT=true`; blocking tools additionally require `PYFIND_MCP_ALLOW_INPUT_GATE=true`. `run_commands` is always registered, but it can only queue tools that are registered in the same server.

| Tool | Access | Description |
|---|---|---|
| `list_windows` | read-only | Window titles, handles, bounds, minimized/active state |
| `list_monitors` | read-only | Physical monitors and virtual desktop bounds |
| `screenshot` | read-only | Capture screen/monitor/window/region; returns a PNG/WebP preview image and full path |
| `ocr` | read-only | Windows OCR with absolute line and optional word boxes; prefer model vision when available |
| `find_text` | read-only | PyFind text matching with `in`, `=`, `startswith`, `endswith` |
| `find_image` | read-only | PyFind PNG/WebP template matching with threshold and wait timeout |
| `list_templates` | read-only | Available PNG/WebP templates and image dimensions |
| `reload_templates` | read-only | Clear and rebuild the template cache |
| `web_search` | read-only, network | Query the configured SearXNG instance; returns titles, URLs, snippets, engines, answers, suggestions, and corrections |
| `web_fetch` | read-only, network | Download an HTTP(S) page and return readable text with inline Markdown links; truncates to `max_chars` |
| `click` | input | Tagged click or move; optional modifiers and `window_title` focus check; restores the pointer afterwards |
| `move_mouse` | input | Move the pointer to absolute coordinates (leaves it there) |
| `move_mouse_by` | input | Move the pointer by an exact pixel offset (leaves it there) |
| `drag` | input | Press, move, and release a mouse button safely; restores the pointer afterwards |
| `type_text` | input | Type Unicode text one character at a time with pacing; `settle` delay before focus restore; pass `window_title` to focus it for the injection |
| `press_key` | input | Press a key or hotkey such as `ctrl+shift+s`; `settle` delay before focus restore; pass `window_title` to focus it for the injection |
| `scroll` | input | Scroll the wheel vertically or horizontally; restores the pointer if `x`/`y` moved it |
| `focus_window` | input | Force a window to the foreground and confirm it has focus |
| `cursor_position` | read-only | Current pointer position |
| `run_commands` | batch | Run a JSON list of `{"name", "args"}` commands in order; captures/restores focus around the queue so queued input keeps its selection |
| `begin_input_task` | gate | Start an injected-input task; each input call blocks only for its own injection |
| `end_input_task` | gate | End the task, release any remaining block, and play the descending end cue |
| `block_user_input` | gate | Manual override: hold physical input blocked across steps; persistent unless `ttl` is passed |
| `unblock_user_input` | gate | Release a manual block created by `block_user_input` |
| `input_block_status` | read-only | Task/blocked/running state, hotkey, remaining TTL |

### Input blocking

Agent tasks use `begin_input_task` / `end_input_task`. Physical input stays **available between calls**: nothing swallows input while the agent observes, waits, or thinks. Each input tool closes the gate only for the duration of its own injection and opens it again in a `finally`. The emergency hotkey is installed at task start and stays until `end_input_task`.

`block_user_input` is the explicit override for a continuous lockout; it is not part of the normal task flow. There is no task-wide blocking mode.

Because input is not held between calls, the foreground window can change while the agent observes or thinks, and a keystroke sent to the wrong window is silently lost. Pass `window_title` to `press_key` or `type_text` to make the call target-specific. Every injection then runs the same guarded sequence: the server blocks **keyboard and mouse**, captures the window that currently has focus, checks whether the named window (or one of its dialogs) already has focus, and if not calls `pyutmisc.windows.foreground.force_foreground` and waits briefly for Windows to report it as foreground. It sends the input, puts the previously focused window back in the foreground, and only then opens the gate again. If the target cannot be focused the call raises and **no key is sent**; if the previous window cannot be focused again afterwards the call also raises, so focus is never silently left on the wrong window. `click`, `drag`, and `scroll` accept `window_title` too: they focus the target for the injection, which also protects against another window covering the click target, and then restore the previous focus. `focus_window` is the exception: it is an explicit focus request, so it leaves the window in front.

#### Queueing input with run_commands

`run_commands` is the server-side ordered batch. It takes a JSON list and runs each entry in order on the server. Each entry is `{"name": <tool name>, "args": [positional arguments]}`, where the arguments follow the named tool's parameter order:

```json
{
  "window_title": "Untitled - Notepad",
  "commands": [
    {"name": "press_key", "args": ["ctrl+a"]},
    {"name": "sleep", "args": [0.1]},
    {"name": "type_text", "args": ["replacement"]}
  ]
}
```

The queue has a built-in `sleep` command, capped at 30 seconds. In a focused queue the input block stays closed during the pause, so keep it short; use `find_text` or `find_image` with `timeout` when waiting for UI state.

`run_commands` follows the same strict focus rules as a single input call, but around the whole queue: it blocks keyboard and mouse, captures the window that currently has focus, brings the target to the front, runs every command, restores the captured window, and only then releases input. Because the target keeps the foreground while commands run, every queued input call skips its own focus take/restore, so a selection, caret, or transient menu made by one entry is still live for the next. The human gets their previous window back after the queue. `settle` (default 0.2 s) delays the final restore so the target can process the last event while input is still blocked. Without a top-level `window_title`, the first focus command (`click`, `drag`, `type_text`, `press_key`, `scroll`, or `focus_window`) that supplies one is used. Commands that name a different window can still take focus for their own injection and restore the queue target afterwards; the final restore still returns the pre-queue window.

A focused queue holds input blocked for its whole duration, including any `sleep`, `find_text`, or `find_image` wait inside it. Keep waits short, or run long observation commands outside the queue.

Do not split these commands across separate tool calls. A standalone input call restores focus to the previously focused window after its injection, and between calls the foreground window can change; a later `type_text` may append instead of replace. The whole queue is validated before anything runs, so a typo in a later entry has no side effects. By default a failed command stops the queue and returns the results so far; `continue_on_error=true` keeps going. If a queue blocks input, put `unblock_user_input` last and use `continue_on_error=true` so a failure cannot strand the block. A focused queue already holds the gate for its whole duration; releasing it with a queued gate command opens the block early.

A screenshot or a visual decision ends the queue: inspect the result, then start the next queue. Inside a queue, use `find_text` or `find_image` with `timeout` to wait for UI state instead of sleeping. The queue accepts every registered tool except `screenshot`, `run_commands` itself, and the web tools (`web_search` and `web_fetch`), which are network calls and would hold the input gate closed while they run.

When `press_key` or `type_text` had to take focus, the restore is delayed by `settle` (default 0.2 s) while the gate is still closed. This gives the target time to process the last event before it is deactivated: without it, Chrome can lose a `ctrl+l` when the window had to be refocused and the next call types into the old URL. The delay is skipped when the target already had focus, and when the injection raised. Raise `settle` for slow or heavily loaded applications.

### Pointer restoration

`click`, `drag`, and `scroll` return the pointer to the position it had before the call, by default. The restore is a tagged move inside the same gate, so it still works while input is blocked and happens before focus is restored. If the pointer is already at the final position no extra move is sent; `scroll` without `x`/`y` never moves the pointer, so it has nothing to restore. Pass `restore_cursor=False` when the next step needs the pointer to stay where it is, for example a sequence of clicks at nearby coordinates. `move_mouse` and `move_mouse_by` are explicit pointer moves and always leave the pointer at the requested position; `click(button="move")` behaves the same way. The result reports `cursor_restored` in every case.

`begin_input_task` plays an ascending start cue, each injection plays a short high blip, and `end_input_task` plays a descending end cue. Cues are played asynchronously, so they never delay a tool or a hook. Set `PYFIND_MCP_BEEP=false` to silence them; a failed beep never prevents an injection or a release.

The task tools install the keyboard hook at task start (for the emergency hotkey) and the mouse hook on the first mouse injection; blocking itself is toggled per injection, and both keyboard and mouse are blocked for every injection because focus is taken and released inside it. The hooks stay installed for the task. A manual block and an injection lease layer over each other in either order: if a device is already blocked when an injection starts, the injection does not open it when it ends; if a manual block is armed during an injection, the manual block survives the lease ending.

`block_user_input` is a manual override for cases where the human must be locked out continuously, not part of the normal task flow. By default that block is **persistent**: it ends only when `unblock_user_input` is called, the physical emergency hotkey is pressed (`Ctrl+Alt+Shift+F12` by default), or the MCP process exits (Windows removes the hooks). Pass `ttl` to arm an automatic release timer; the default maximum is 600 seconds. Calling `block_user_input` again extends or re-arms the block.

While the gate is closed, the input-action tools inject with loge2's passthrough tag, so their clicks, moves, keys, and text still reach the desktop. Untagged synthetic input from other processes is blocked too. Injected events never trigger the emergency hotkey.

The emergency hotkey works even when only the mouse is blocked because the keyboard hook is installed in observe-only mode for the hotkey. Pressing it also plays the end cue as the task ends. If `PYFIND_MCP_INPUT_GATE_HOTKEY` is set to `none`, use `Ctrl+Alt+Del` and Task Manager to end the MCP process if a persistent block must be released.

### Coordinates

All rectangles are absolute virtual-screen pixels:

```json
{"x": 0, "y": 0, "width": 800, "height": 600}
```

Secondary monitors can have negative `x` or `y`. Use at most one of `window_title`, `monitor`, or `region` in a call. With none of them, the full virtual desktop is used.

`monitor` is the index returned by `list_monitors`, starting at `0`.

### Templates

Put PNG or WebP files in `templates/`:

```text
templates/
├─ save_button.png
└─ dialog_icon.webp
```

Call `find_image` with `templates: ["save_button"]`, `["save_button.png"]`, `["save_button.webp"]`, or an absolute path. A reference without a suffix tries `.png` first, then `.webp`. `reload_templates` is only needed after adding or replacing files. For a one-off template, use `screenshot` on the smallest region that contains the target and pass the returned full path; an entire-desktop screenshot is usually too large to match against a smaller region.

Mask templates, directories of templates, and multi-scale matching are not supported in v1.

### Screenshots

`screenshot` writes a full-resolution image to the output directory and returns a downscaled preview as MCP image content. The format is PNG by default; set `PYFIND_MCP_IMAGE_FORMAT=webp` for WebP (with `PYFIND_MCP_WEBP_QUALITY` or `PYFIND_MCP_WEBP_LOSSLESS`). The text block contains the full path, preview path, format, dimensions, capture region, and target. Use the full path as a template source when convenient; lossy WebP can slightly reduce template-match scores, so use lossless WebP or PNG for templates.

## Web search (SearXNG)

`web_search` and `web_fetch` are always registered and do not depend on PyFind or loge2. Point the server at a SearXNG instance with `SEARXNG_MCP_BASE_URL` (default `http://127.0.0.1:8080`). The instance must expose its JSON API; add `json` to `search.formats` in SearXNG's `settings.yml` and restart it:

```yaml
search:
  formats:
    - html
    - json
```

`web_search` accepts:

| Parameter | Default | Meaning |
|---|---|---|
| `query` | required | Search terms, at most 2000 characters |
| `max_results` | `10` | Results to return, 1-50 |
| `categories` | none | SearXNG categories such as `general`, `news`, `images` |
| `engines` | none | SearXNG engine names such as `duckduckgo`, `brave` |
| `language` | `auto` | SearXNG language code, for example `en-US` |
| `pageno` | `1` | Result page, 1-20 |
| `time_range` | none | `day`, `week`, `month`, or `year` |
| `safesearch` | `0` | `0` off, `1` moderate, `2` strict |
| `instance_url` | configured URL | Override the SearXNG base URL for this call |

The result contains `results` (title, URL, snippet, engine, score, category, published date), plus `answers`, `infoboxes`, `suggestions`, `corrections`, and `unresponsive_engines` when the instance provides them.

`web_fetch` downloads one HTTP(S) URL and returns readable text. HTML is converted to plain text with inline Markdown links; scripts, styles, and tags are removed. JSON is pretty-printed, and plain text or XML is returned as-is. PDFs and other binary content are rejected. `max_chars` defaults to 20000 and is capped at 200000; the result reports `char_count`, `truncated`, and `download_truncated`. It does not execute JavaScript, send cookies, or authenticate, so use it for static pages and APIs.

Web tools are deliberately excluded from `run_commands`: a focused queue holds the input gate closed for its whole duration, and a slow remote request would lock the human out. Call them as normal tools instead.

## Typical workflow

For web research, call `web_search` first, then `web_fetch` on the most relevant results. The web tools are independent of the desktop workflow below.

1. `list_windows` or `list_monitors` to find the target.
2. `screenshot` or `ocr` to inspect it. If the model has native vision, prefer
   the screenshot: it preserves layout and icons and usually reads text more
   reliably. `ocr` uses Windows OCR (not PaddleOCR), so its text and boxes can
   be imperfect; use it when the model cannot see images or when plain text is
   enough.
3. `find_text` / `find_image` to get absolute coordinates.
4. Call `begin_input_task` (ascending start cue). Input stays available between calls and is blocked only for each injection.
5. Use `click`, `move_mouse`, `drag`, `type_text`, `press_key`, or `scroll`. Each call blocks keyboard and mouse only for that injection and plays a short blip. Pass the inspected `window_title` to input calls so the server focuses it for the injection, then gives focus back to the window that had it before; either focus failure raises instead of being silent. Queue dependent commands that need no visual feedback in one `run_commands` call, for example `ctrl+a` followed by `type_text`, and pass `window_title` to the queue so it focuses once, keeps focus for every entry, and restores your window afterwards. `type_text` paces characters (default 0.03 s each), and both keyboard tools wait `settle` (default 0.2 s) before restoring focus so the target can process the last event. Clicks, drags, and targeted scrolls also put the pointer back where it was; pass `restore_cursor=False` only when it should stay at the target.
6. Call `end_input_task` when the test finishes, even if it failed (descending end cue), then take another screenshot to verify the result.

## Safety

- Input control is off by default. Set `PYFIND_MCP_ALLOW_INPUT=true` only when you intend to let the agent click and type.
- Blocking is off by default and needs a second opt-in, `PYFIND_MCP_ALLOW_INPUT_GATE=true`.
- Each injection blocks input only for its own duration. The manual `block_user_input` override can hold the human out continuously; use a bounded `ttl` and call `unblock_user_input` when done. If a run goes wrong, call `end_input_task`/`unblock_user_input` or press the emergency hotkey.
- The emergency hotkey (`Ctrl+Alt+Shift+F12` by default) releases the gate from physical input only. `Ctrl+Alt+Del` still opens the secure desktop, so Task Manager can end the MCP process if necessary.
- Global hooks are not a security boundary. Any process that knows loge2's `dwExtraInfo` tag can forge a passthrough event, and elevated windows may ignore injected input because of UIPI.
- Screen captures may contain passwords, tokens, or private content. Output stays in the local temp directory and is never uploaded by this server.
- `web_search` sends the query to the configured SearXNG instance, and `web_fetch` contacts the URL you pass. Do not put secrets in queries, and only fetch sources you trust.
- `web_fetch` follows redirects and can reach local or private network addresses. Treat it as a general HTTP client, not a sandbox.
- The agent sees the screen; do not run it against sensitive desktops unattended.

## Limitations

- Windows only.
- MSS captures only visible desktop content. Minimized or fully occluded windows cannot be read.
- `ocr` wraps Windows OCR, whose text and line/word boxes can be approximate; this server does not use PaddleOCR. Vision-capable models should read a `screenshot` instead.
- `find_text` uses PyFind's fixed `en-US` Windows OCR path. The `ocr` tool accepts other `lang` values. OCR matches report `confidence: 0.0`; use their rectangles, not the score.
- `find_image` matches at native scale only. Overlapping matches above the threshold can appear; the output is capped by `max_results`.
- WebP output reduces file size, not necessarily vision-token cost, which is usually driven by image dimensions. Lossy WebP can lower template-match scores; use PNG or `PYFIND_MCP_WEBP_LOSSLESS=true` for templates. Some MCP clients may not render WebP image content; keep the default PNG if screenshots arrive blank.
- `type_text` uses `KEYEVENTF_UNICODE`; a few legacy applications ignore that input path. Named keys such as `enter` and `tab` should use `press_key`.
- `type_text` sends one codepoint per `SendInput` call and pauses `interval` seconds between characters (default 0.03). Chromium targets drop characters from a single large batch; lower the interval for fast native controls, or raise it if characters are still lost. `press_key` and `type_text` also wait `settle` (default 0.2 s) before restoring focus when they had to take it, so the target can process the last event; raise it for slow applications.
- Focus handling calls `pyutmisc.windows.foreground.force_foreground`, so that package must be installed for `window_title` calls and `focus_window`. Windows can still refuse to hand over the foreground (for example during a UAC prompt); the call then fails without sending the event. Restoring the previous window after an injection is verified the same way, and a failed restore raises instead of leaving focus stolen.
- Focus is restored after every standalone injection. A selection made by one tool call is not owned by the server and can be lost before a later separate call; queue dependent commands such as `ctrl+a` followed by `type_text` in one `run_commands` call, with `window_title` on the queue. The queue applies the strict focus sequence around the whole list and restores the pre-queue window after the last command.
- The input gate is global to the interactive desktop session, not per-window. During an injection it blocks the human's input in every application, including OpenCode; between injections input passes through.
- The PyFind pool costs a few hundred MB and takes a moment on the first screen call.
- A stuck PyFind worker can hang a tool call; OpenCode's execution timeout returns an error, but reconnecting the server is the clean recovery.
- `web_search` needs SearXNG's JSON API. If `search.formats` does not include `json`, the instance returns HTTP 403 and the tool reports a hint.
- `web_fetch` reads static HTML, text, JSON, and XML only. It does not run JavaScript, keep cookies, log in, or extract PDFs and other binary files.
- HTML-to-text conversion is heuristic. Navigation, cookie banners, and other boilerplate can remain; use the returned title and description to judge the page.
- Public SearXNG instances may rate-limit or block non-browser clients. Set `SEARXNG_MCP_USER_AGENT` to a browser-like value only if the instance's rules allow it.

## Development

```powershell
pytest
pytest -m integration tests/test_live_pyfind.py -s
pytest -m integration tests/test_live_input_gate.py -s
```

`tests/test_live_pyfind.py` initializes the real PyFinder pool and captures the desktop. `tests/test_live_input_gate.py` installs the real keyboard/mouse hooks, briefly blocks physical input, verifies tagged injection passes, and exercises the emergency hotkey with harmless F16 chords. Run it only on an interactive desktop. `tests/test_searxng.py` uses a local HTTP fixture, so it does not need a running SearXNG instance.

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
- `web_search` returns HTTP 403: add `json` to `search.formats` in SearXNG's `settings.yml`, restart it, and check the limiter.
- `web_search` cannot reach the instance: confirm `SEARXNG_MCP_BASE_URL`, that SearXNG is running, and that the URL does not already end in `/search`.
- `web_fetch` reports an unsupported content type: only HTML, text, JSON, and XML pages are supported; PDFs and binary downloads are rejected.
- Protocol errors: set `PYFIND_MCP_LOG_LEVEL=DEBUG` and inspect OpenCode's MCP logs; PyFind diagnostics are redirected to stderr so stdout stays clean.