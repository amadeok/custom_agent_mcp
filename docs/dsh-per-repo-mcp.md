# DSH per-repository MCP servers and web search

How to give DeepSeek Harness (DSH) a different MCP toolset for one repository,
and where web search fits in each layer.

Tested against `@deepseek-ai/dsh` 0.2.2-rc.1 with the `web` profile on Windows.

## TL;DR

DSH has no automatic per-repository config discovery. A profile is composed
from global layers, so repository scoping is a convention: keep the profile
generic, put repo-specific MCP entries in a patch file inside the repository,
and pass it with `--patch` when you launch from that repository.

```powershell
cd F:\all\GitHub\custom_agent_mcp
dsh web --patch .dsh\cordis.patch.yml
```

## What DSH actually composes

Each profile is an ordered stack:

1. Bundle patches, in `dsh.profile.bundles` order
   (`@deepseek-ai/dsh-base`, then `@deepseek-ai/dsh-web-app`, ...).
2. The profile layer: `$DSH_HOME/profiles/<name>/cordis.patch.yml`.
3. The home layer: `$DSH_HOME/cordis.patch.yml` (applies to every profile).
4. Each `--patch <file>` overlay, in argv order.

`$DSH_HOME` defaults to `~/.dsh`. On this machine that is
`C:\Users\amade\.dsh`.

Two facts make repo-scoped overlays work:

- The **invoking directory is the default workspace root**.
- Relative `--patch` paths resolve against the **invoking directory**.

There is no scan for `.dsh/` in the current repository, so the overlay only
applies when you pass it explicitly. That is the feature: the same file is both
the documentation and the switch.

## Pattern A: repo-local patch overlay

Create `.dsh/cordis.patch.yml` in the repository. It is a top-level YAML array
of patch entries, exactly like the profile patch:

```yaml
# Applied only when dsh is started with --patch from this repository.
# Top-level YAML array of loader patches.

# Enable the PyFind entry if your profile defines it but leaves it off.
- id: mcp-pyfind
  disabled: false

# If the profile already inserts the web tools globally, turn that copy off so
# this repository's copy is the only one. This warns and skips when the entry
# does not exist.
- id: mcp-web-tools
  disabled: true

# Add the Dynamic Search Kit only in this repository.
- insert:
    - id: mcp-web-tools-repo
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: mcp_web_tools
        transport: stdio
        command: C:\Users\amade\AppData\Local\Python\pythoncore-3.13-64\python.exe
        args: [ "-m", "custom_agent_mcp.dynamic_search_kit" ]
        cwd: F:\all\GitHub\custom_agent_mcp
        toolCallTimeoutMs: 300000
        failOnStartupError: false
        env:
          DYNAMIC_SEARCH_TOOL_MODE: combined
          DYNAMIC_SEARCH_PROVIDERS: searxng,tavily,tinyfish,camoufox
          DYNAMIC_SEARCH_STRATEGY: first
          SEARXNG_MCP_BASE_URL: http://127.0.0.1:8888
          SEARXNG_MCP_TIMEOUT: "15"
          SEARXNG_MCP_START_CMD: C:\Users\amade\Apps\searxng-windows\start-searxng.cmd
          TINYFISH_MCP_FALLBACK: "true"
          CAMOUFOX_MCP_MODE: remote
          CAMOUFOX_MCP_REMOTE_URL: ws://192.168.1.194:9222/dynamic-search
          PYFIND_MCP_LOG_LEVEL: WARNING
```

A launcher in the repository root makes the overlay the default for that
checkout:

```bat
@echo off
cd /d "%~dp0"
npx @deepseek-ai/dsh@0.2.2-rc.1 web --patch .dsh\cordis.patch.yml %*
```

Pin the CLI version in the launcher. The unpinned `npx @deepseek-ai/dsh` form
re-resolves on every start, so a registry publish can change behavior under you.

### Patch semantics that matter

- `- id: <entry>` overrides **top-level fields** of an existing entry. A
  `config:` override **replaces the whole config object**, it is not a deep
  merge. Repeat every field the entry needs.
- `- insert: [ ... ]` appends entries. Without an `id` it appends at the root;
  with an `id` it appends to that group.
- `disabled: false` re-enables an entry without rewriting its config. This is
  how a repo overlay turns on a server the global profile ships off.
- `name:` acts as a guard. If it does not match the existing entry's module,
  the patch is skipped with a warning.
- Inserted entries need a unique `id`. `serverName` controls the tool prefix
  (`mcp__<serverName>__<tool>`), so keep it unique among enabled servers too.

### Avoid double-loading

If the global profile already inserts the same server, do not insert a second
copy in the repo overlay. Either:

- leave the global entry in place and only toggle `disabled`, or
- remove the global entry and let each repository insert its own.

Two enabled entries with the same `serverName` produce duplicate or unstable
tool names and cost context twice.

## Pattern B: a dedicated profile

Use this when the repository needs a different model, preset, or plugin set,
not just different MCP servers.

```powershell
dsh --profile custom-agent-mcp --from-default-profile web
```

That creates `$DSH_HOME/profiles/custom-agent-mcp/` from the shipped `web`
template and boots it. Edit its `cordis.patch.yml` and put the MCP entries
there. Start it later with:

```powershell
dsh custom-agent-mcp
```

The tradeoff is manual selection. A profile is not bound to a directory, so
nothing switches it automatically when you change repositories.

## Web search: know which one you are enabling

There are two independent paths. Pick one primary path per session.

### Built-in DSH search

`web_search` and `web_fetch` come from `@deepseek-ai/dsh-tool-web`. The Web
app disables the host row and each agent preset adds it back, so the
`standard`, `cordis`, and `ptc` presets have it and `minimal` does not.

The provider rows in `dsh-base` are:

| Row | Default |
| --- | --- |
| `web` | `searchProvider: deepseek-official`, `fetchProvider: http` |
| `web-search-deepseek` | `apiKeyEnv: DEEPSEEK_API_KEY` |
| `web-deepseek-api` | `apiKeyEnv: DEEPSEEK_API_KEY` |
| `web-fetch-http` | none |
| `tool-web` | `fetch: true`, `searchTimeoutMs: 60000` |

Configure it in the GUI under **Settings -> Plugins -> Web search** (the key is
written through the credentials store, not the settings document), or override
the `web` row in a patch:

```yaml
- id: web
  config:
    searchProvider: deepseek-official
    fetchProvider: http
```

Both fields are required because `config` is replaced wholesale. Omitting
`fetchProvider` drops it.

This path uses a DeepSeek model request for search, so it follows the
`DEEPSEEK_API_KEY` credential and DeepSeek billing.

### MCP search from this repository

`custom_agent_mcp.dynamic_search_kit` exposes its own tools, optionally
`dynamic_search`, `searxng_search`, `tavily_search`, `tinyfish_search`,
`camoufox_search`, `fetch_url`, `page_fetch`, and the `camoufox_*` session
tools. It needs no DeepSeek key; SearXNG, Tavily, TinyFish, and Camoufox are
configured independently. Every exposed tool adds its description to the model
context, which is why the overlay above keeps the tool mode on `combined`.

The old `custom_agent_mcp.web_server` module is a compatibility alias and
still works, but new entries should use `dynamic_search_kit`.

### Which to use where

- Built-in search: zero setup, DeepSeek-account billing, provider switch in the
  GUI, results shaped by the DeepSeek Messages API.
- MCP search: provider chain you control (SearXNG first is free), persistent
  Camoufox sessions, static-first fetching, no DeepSeek key. Costs context for
  its tool catalog.

Running both in one session is valid, but the model then sees two overlapping
search tools. Prefer one, or deny the other per agent preset.

## Verify and reload

Print the composed tree without booting:

```powershell
dsh web --dump-config --patch .dsh\cordis.patch.yml
```

Inspect the schema for a field you are unsure about:

```powershell
dsh --profile web --dump-config-schema
```

Then boot with the overlay and check the tool list in the session.

`dsh-hmr` watches the profile manifest and the profile and home patch files.
It does **not** watch `--patch` overlays, so restart the web process after
editing the repo file. The GUI itself reconnects automatically.

## Gotchas

- `cwd` defaults to the workspace (the invoking directory), but set it
  explicitly. A server started from a subdirectory sees a different workspace.
- Use the absolute Python executable in `command`; `py` and `python` depend on
  PATH and the active virtual environment.
- Quote paths that contain spaces. Windows backslashes are literal in YAML
  plain scalars.
- Set `failOnStartupError: false` for optional servers. A dead Camoufox or
  SearXNG daemon should not take down the whole profile.
- A missing `id` target only warns and skips. Check startup logs after renaming
  or removing a global entry that a repo overlay still references.
- `transport: stdio` is required for local servers; `transport` is not inferred
  from the presence of `command`.