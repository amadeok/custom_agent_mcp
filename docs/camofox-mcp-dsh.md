# Camofox MCP in DeepSeek Harness — how it was made to work

Field notes from wiring the `camofox` MCP server (Node) into [DSH](https://github.com/deepseek-ai) on this Windows machine, including the multi-hour crash that turned out to be caused by a directory path.

This is the **Node** stack (`camofox-mcp` + `camofox-browser`). It is unrelated to this repository's own Python `camoufox_open` / `camoufox_act` integration described in the [README](../README.md#camoufox-persistent-sessions). Both can coexist; they are separate browsers with separate profiles.

---

## TL;DR

```powershell
# 1. Camoufox engine MUST live outside the workspace (see "The crash" below)
#    Installed at: C:\Users\amade\camofox\engine

# 2. Start the browser daemon (sets CAMOUFOX_INSTALL_DIR itself)
.mcp\camofox-browser\start-camofox-browser.cmd start

# 3. Confirm it is up
curl http://127.0.0.1:9377/health
```

If `/health` reports `browserConnected: true`, the MCP tools work. Nothing else needs to be running.

---

## Architecture: three layers, not one

The single most useful thing to understand. The JSON snippet that starts this whole process only configures **one** of three moving parts.

| Layer | Package | Runs as | Responsibility |
|---|---|---|---|
| MCP front-end | `camofox-mcp` 1.17.0 | stdio, spawned by DSH | Exposes 35 tools, speaks JSON-RPC over stdin/stdout |
| Browser daemon | `camofox-browser` 2.5.0 | detached background process on port 9377 | Owns the Playwright contexts, tab registry, downloads, network capture |
| Engine | Camoufox 158.0.1-beta.41 (`2644bd14`) | child process of the daemon | The actual patched Firefox binary |

The MCP server holds **no** browser state. It is a thin HTTP client. Consequences worth internalising:

- Restarting the daemon drops every tab, but not the DSH session.
- The daemon is **not** started by DSH. If it is down, every tool call fails with a connection error.
- The daemon does **not** survive a reboot. It needs starting manually.
- `tabId` values are meaningless to a different daemon instance.

---

## The MCP entry

Appended to `C:\Users\amade\.dsh\profiles\web\cordis.patch.yml`, which is a top-level YAML **array** of loader patches. MCP servers are added as `insert` entries:

```yaml
- id: mcp-camofox
  name: "@deepseek-ai/dsh-mcp-client"
  config:
    serverName: camofox
    transport: stdio
    command: C:\Program Files\nodejs\node.exe
    args:
      - C:\Users\amade\Documents\deepseek-harness\default-workspace\.mcp\camofox\node_modules\camofox-mcp\dist\index.js
    cwd: C:\Users\amade\Documents\deepseek-harness\default-workspace\.mcp\camofox
    toolCallTimeoutMs: 300000
    failOnStartupError: false
    env:
      CAMOFOX_URL: http://localhost:9377
```

Decisions worth copying:

- **Absolute `dist/index.js`, not `npx -y camofox-mcp@latest`.** The `@latest` form re-resolves on every start, so a registry publish can silently change behaviour under you mid-session. Pinning to a local install makes the setup reproducible and offline-capable.
- **`failOnStartupError: false`.** A missing or crashed browser daemon must not take down the other MCP servers in the profile. Matches the `mcp-web-tools` entry.
- **`toolCallTimeoutMs: 300000`.** `camofox_browser` screenshot and `batch_download` legitimately run for minutes.
- **`cwd` set to the package directory** so relative resolution inside the server behaves predictably.

Tools are namespaced as `mcp__camofox__<toolName>`, e.g. `mcp__camofox__create_tab`, `mcp__camofox__snapshot`.

### Installation that avoids the npm permission wall

`npm install camofox-browser` failed with an EPERM spawn error while running postinstall scripts. Workaround:

```powershell
npm install camofox-browser@2.5.0 --ignore-scripts
node node_modules\camofox-browser\bin\install-browser.js
```

`--ignore-scripts` skips the postinstall that tries to fetch the browser; `install-browser.js` does the same job on demand, where the failure is visible.

---

## The crash: `0xC0000005` in `ucrtbase.dll`

### Symptom

Every launch of `camoufox.exe` died instantly:

| Code | Meaning |
|---|---|
| `3221225477` (`0xC0000005`) | Access violation |
| `3221226505` (`0xC0000409`) | Stack buffer overrun / fast-fail |

Faulting module `ucrtbase.dll`, WER bucket `BEX64`, offset `0x11858`. Browser stdout got as far as `*** You are running in headless mode.` and then the process vanished. No Playwright error, no log line — the child process just disappeared, which is why this presented as an MCP or Playwright bug rather than a binary problem.

### What was ruled out

| Hypothesis | Test | Result |
|---|---|---|
| Corrupted download | SHA-256 every file against a known-good install | **Byte-identical** — `camoufox.exe`, `xul.dll`, `omni.ja`, `camoufox.cfg`, `dependentlibs.list` all matched |
| Bad build | Same build elsewhere | Launched fine |
| `GeoLite2-City.mmdb` | Renamed it away, retried | Crash persisted |
| Incomplete install | `version.json` was a 41-byte stub | Replaced with the full 271-byte manifest, crash persisted |
| Windows sandbox ACLs | Copied the workspace's exact `Deny DeleteSubdirectoriesAndFiles` ACE onto a working directory | **Still launched fine** |

The ACL test matters: the DSH workspace does carry a non-inherited `Everyone: Deny DeleteSubdirectoriesAndFiles` rule (that is what the file sandbox installs), and it is an obvious suspect. It is a red herring.

### The actual variable: path length and depth

The same bytes, copied to different directories:

| Path | Result |
|---|---|
| `C:\cf-short` | ALIVE |
| `C:\Users\amade\cf-short2` | ALIVE |
| `C:\Users\amade\Documents\cf-t1` | ALIVE |
| `C:\Users\amade\Documents\deepseek-harness\cf-t2` | ALIVE |
| `C:\Users\amade\Documents\deepseek-harness\default-workspace\cf-t3` | **EXIT `0xC0000005`** |
| `...\default-workspace\.mcp\camoufox` | **EXIT `0xC0000005`** |
| `C:\Users\amade\AppData\Local\camoufox\...\158.0.1-beta.41-2644bd14` | ALIVE |

Not monotonic in length — `C:\Users\amade\AppData\Local\camoufox\camoufox\Cache\browsers\official\158.0.1-beta.41-2644bd14` is 108 characters and works. So it is not a hard `MAX_PATH` limit. Whatever the trigger is, it keys on the workspace directory specifically; the depth and name `default-workspace` are the distinguishing features.

Confirmed deterministic, not flaky — three alternating launches of workspace copy vs. sibling directory:

```
ws#0  EXIT 3221225477      sib#0 ALIVE
ws#1  EXIT 3221225477      sib#1 ALIVE
ws#2  EXIT 3221225477      sib#2 ALIVE
```

### Fix

Move the engine out of the workspace and point the daemon at it:

```powershell
$eng = 'C:\Users\amade\camofox\engine'
robocopy 'C:\Users\amade\AppData\Local\camoufox\camoufox\Cache\browsers\official\158.0.1-beta.41-2644bd14' $eng /E

# Write version.json BOM-free: Node's JSON.parse rejects a leading BOM,
# and a parse failure here degrades into "version could not be determined".
[IO.File]::WriteAllText("$eng\version.json", '{"version":"158.0.1","release":"beta.41"}',
    (New-Object System.Text.UTF8Encoding($false)))
```

`robocopy` exiting with `1` means "files were copied" and is the success case; only `>= 8` indicates failure.

`CAMOUFOX_INSTALL_DIR` is the **only** lever. There is no CLI flag. It is read once at module load in `camoufox-js/dist/pkgman.js`:

```js
export const INSTALL_DIR = process.env.CAMOUFOX_INSTALL_DIR
    ? path.resolve(process.env.CAMOUFOX_INSTALL_DIR)
    : userCacheDir("camoufox");
```

`camofox-browser` forwards it to its daemon explicitly — it is one of ~45 names allow-listed in `dist/src/utils/config.js` (`serverEnv`). Any env var not on that list is **silently dropped** when the daemon is spawned. If you add a config knob and it appears to do nothing, check that list first.

Because the daemon is spawned `detached` with a filtered environment, a per-shell `$env:CAMOUFOX_INSTALL_DIR` only helps the shell that starts it. Two fixes applied:

1. Set it at user level so any manual start works:
   ```powershell
   [Environment]::SetEnvironmentVariable('CAMOUFOX_INSTALL_DIR', 'C:\Users\amade\camofox\engine', 'User')
   ```
2. Ship a wrapper that sets it inline — see below.

---

## Second failure: stale profile sidecars

With the engine moved, `create_tab` then failed with:

```
Cannot verify profile compatibility for user "dsh-smoke": installed Camoufox
version could not be determined. Run npm run browser:install ...
```

Cause: `getInstalledCamoufoxVersion()` in `dist/src/services/context-pool.js` calls `camoufox-js`'s `installedVerStr()`, which does:

```js
Version.fromPath()  // reads $INSTALL_DIR/version.json
```

Any throw is swallowed and returns the string `'unknown'`, which then hard-fails the compatibility check. A `version.json` missing, malformed, or with the wrong shape produces this. Hence the explicit write in the fix above — do not assume `robocopy` carried a valid one.

Profiles under `C:\Users\amade\.camofox\profiles\` also cache `compatibility.json` recording the engine version. Profiles created against the broken install block the fixed one:

```
Profile for user "X" was created with Camoufox <old>, but the current version is <new>.
Delete the profile directory to reset.
```

Deleting them requires the daemon to be **stopped** first — SQLite files stay locked otherwise:

```powershell
.mcp\camofox-browser\start-camofox-browser.cmd stop
Get-Process camoufox -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item 'C:\Users\amade\.camofox\profiles\*' -Recurse -Force
```

---

## The start/stop wrapper

`.mcp\camofox-browser\start-camofox-browser.cmd`, because the fix must be baked into the launch or it is lost on the next clean shell.

```powershell
.mcp\camofox-browser\start-camofox-browser.cmd start    # background start + health
.mcp\camofox-browser\start-camofox-browser.cmd status
.mcp\camofox-browser\start-camofox-browser.cmd stop
.mcp\camofox-browser\start-camofox-browser.cmd restart
```

Two Windows-specific traps hit while writing it:

- **Use `.cmd`, not `.ps1`.** The PowerShell version is rejected outright: *"is not digitally signed. You cannot run this script on the current system."* Do not rely on changing execution policy; the `.cmd` wrapper is immune.
- **`timeout /t` fails under redirection.** `timeout /t 3 /nobreak` aborts with *"Input redirection is not supported"* when stdout is captured, which is exactly what happens when an agent or CI runs it. Use `powershell -NoProfile -Command "Start-Sleep -Seconds 3"` instead.

The wrapper also fails fast if `camoufox.exe` is missing from the engine directory, so a bad `CAMOUFOX_INSTALL_DIR` surfaces as a clear message rather than a downstream MCP timeout.

---

## Verification

Health endpoint:

```json
{"ok":true,"running":true,"engine":"camoufox","version":"2.5.0",
 "browserConnected":true,"poolSize":1,"activeUserIds":["dsh-verify"],
 "profileDirsTotal":1}
```

`browserConnected: false` with `running: true` is normal while idle — the browser only launches on the first tab.

End-to-end, through the real MCP tools:

1. `create_tab` → `https://example.com`
2. `snapshot` → accessibility tree with the page paragraphs and a link ref
3. `screenshot` → 1280×800 PNG render
4. `camofox_evaluate_js` → fingerprint check

```json
{"title":"Example Domain",
 "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0",
 "tz":"America/New_York","lang":"en-US","webdriver":false,"plugins":5,"cores":32}
```

The `tz` value is the meaningful one: the host is `Europe/Rome`, and the tab requested the `us-east` preset, so the timezone spoofing is provably active. `webdriver: false` confirms the automation flag is suppressed.

---

## Debugging technique that paid off

When the MCP tool schema rejected arguments, the fast path was to **bypass the MCP client and drive the server over raw stdio JSON-RPC**:

```powershell
# newline-delimited JSON-RPC on stdin, one message per line
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_tab","arguments":{"url":"https://example.com","userId":"dsh-smoke"}}}
```

Piping that into `node .../camofox-mcp/dist/index.js` separates *schema validation* failures from *backend* failures in one step. Two things to get right:

- Write the input file **BOM-free UTF-8**. A UTF-8 BOM breaks the first `initialize` line and the server hangs waiting for valid JSON.
- Use `cmd.exe /c "... < in.txt > out.txt"` rather than PowerShell redirection, which mangles quoting of paths containing spaces.

This is how the version-detection error was isolated to the daemon, after the MCP layer had already been cleared.

---

## Known rough edges

- **`create_tab`'s `networkCapture` parameter rejects every value.** `start: false` demands `scope: "session"` and an active recording; `start: true` returns `Network capture not found`; correctly-formatted 43-character base64url keys are rejected as `Invalid network capture key`. Omit the parameter entirely — `create_tab` then works. Start capture afterwards with `network_capture_start` on the existing tab. This looks like a bug in camofox-mcp 1.17.0's schema validation, not a configuration problem.
- **`proxy` and `proxyProfile` cannot be null or empty.** `proxyProfile: ""` → *expected string to have >=1 characters*; `proxy: null` → *expected object*; any unknown name → *Unknown proxy profile*. Omit both.
- **`list_tabs` with no arguments returns `[]`** even when tabs exist. Tab ownership is per-`userId`, so pass `userId`.
- **`snapshot` and `screenshot` require an explicit `tabId`.** Unlike some tools they do not accept `userId` as a substitute.
- **`screenshot` returns a normalized copy**, not the source file. Copy to a writable `.png` path before editing.

---

## Checklist for a fresh machine

1. `npm install camofox-mcp@latest` somewhere permanent; note the absolute `dist/index.js`.
2. `npm install camofox-browser@2.5.0 --ignore-scripts`, then `node bin\install-browser.js`.
3. Fetch Camoufox and **place the build outside any sandboxed/workspace directory**.
4. Write a valid `version.json` (`{"version":"…","release":"…"}`) into the engine directory.
5. Set `CAMOUFOX_INSTALL_DIR` at user level **and** inline in the start script.
6. Start the daemon; wait for `browserConnected` or a successful first `create_tab`.
7. Add the `mcp-camofox` entry with `failOnStartupError: false`.
8. Verify with `create_tab` → `snapshot` → `screenshot` → `camofox_evaluate_js` (check `tz` and `webdriver`).

If step 6 crashes with `0xC0000005`, go back to step 3 — the engine path is wrong, and nothing about the MCP config or the browser binary will explain it.
