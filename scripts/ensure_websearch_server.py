"""Ensure exactly one shared Dynamic Search Kit HTTP server is running.

OpenCode's ``type: local`` MCP entries spawn one child process per project
directory. Pointing every directory at one ``type: remote`` server keeps that
at one process, but nothing in the config starts it. This script fills that
gap: it is safe to run from a startup hook, a scheduled task, or by hand, and
it is safe to run many times at once.

Concurrency is handled with :mod:`filelock` around the whole
check-start-wait sequence, so two callers racing at boot cannot both spawn a
server. Readiness is confirmed before the lock is released, so a caller that
returns success can connect immediately.

Usage::

    python scripts/ensure_websearch_server.py            # start if needed
    python scripts/ensure_websearch_server.py --status   # JSON, never starts
    python scripts/ensure_websearch_server.py --restart  # stop, then start
    python scripts/ensure_websearch_server.py --stop     # stop by pid file

Environment values for the server come from ``--env-file`` (default:
``scripts/websearch.env`` next to this file), then the inherited environment.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from filelock import FileLock, Timeout as FileLockTimeout
except ImportError:  # pragma: no cover - filelock is a hard requirement
    sys.stderr.write(
        "filelock is required: python -m pip install filelock\n"
    )
    raise SystemExit(2)

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_ENV_FILE = SCRIPT_PATH.parent / "websearch.env"
DEFAULT_STATE_DIR = Path(
    os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "state"
) / "websearch-mcp"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STARTUP_TIMEOUT = 120.0
DEFAULT_LOCK_TIMEOUT = 120.0
MCP_PATH = "/mcp"

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE file, ignoring blanks and ``#`` comments."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_mcp(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    """Return (ready, detail) for the Streamable HTTP MCP endpoint.

    Any HTTP answer below 500 proves the listener is our server rather than a
    stray process; a refused connection means it is not up yet.
    """
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ensure-script", "version": "1"},
            },
        }
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("POST", MCP_PATH, body=body, headers=headers)
        response = connection.getresponse()
        detail = f"HTTP {response.status}"
        response.read(2048)
        return response.status < 500, detail
    except (OSError, http.client.HTTPException) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        connection.close()


def read_pid(pid_file: Path) -> int | None:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate(pid: int, *, timeout: float = 10.0) -> bool:
    if not pid_alive(pid):
        return True
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def resolve_python() -> str:
    """Prefer the interpreter running this script unless PYTHON_BIN overrides."""
    override = (os.getenv("WEBSEARCH_MCP_PYTHON") or "").strip()
    if override:
        return override
    current = sys.executable
    if current and Path(current).is_file():
        return current
    found = shutil.which("python")
    if not found:
        raise SystemExit("no python interpreter found for the shared server")
    return found


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def status(host: str, port: int, pid_file: Path) -> dict[str, Any]:
    ready, detail = probe_mcp(host, port)
    pid = read_pid(pid_file)
    return {
        "url": f"http://{host}:{port}{MCP_PATH}",
        "listening": port_open(host, port),
        "ready": ready,
        "probe": detail,
        "pid": pid,
        "pid_alive": bool(pid and pid_alive(pid)),
        "pid_file": str(pid_file),
    }


def start_server(
    *,
    host: str,
    port: int,
    env_file: Path,
    pid_file: Path,
    log_file: Path,
    startup_timeout: float,
    cwd: Path,
) -> dict[str, Any]:
    """Start the server if it is not already healthy. Caller holds the lock."""
    ready, detail = probe_mcp(host, port)
    if ready:
        pid = read_pid(pid_file)
        return {"action": "none", "reason": "already running", "pid": pid, "probe": detail}

    if port_open(host, port):
        raise SystemExit(
            f"port {port} is in use but did not answer as an MCP endpoint "
            f"({detail}); refusing to start a second server"
        )

    env = dict(os.environ)
    env.update(read_env_file(env_file))
    env.setdefault("PYTHONUNBUFFERED", "1")
    source_root = str(cwd / "src")
    env["PYTHONPATH"] = (
        source_root + os.pathsep + env["PYTHONPATH"]
        if env.get("PYTHONPATH")
        else source_root
    )

    log_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        resolve_python(),
        "-m",
        "custom_agent_mcp.web_server",
        "--transport",
        "streamable-http",
        "--host",
        host,
        "--port",
        str(port),
    ]

    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {' '.join(command)}\n")
        log.flush()
        flags = 0
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
            **popen_kwargs,
        )

    pid_file.write_text(str(process.pid), encoding="utf-8")

    deadline = time.monotonic() + startup_timeout
    last_detail = "not started"
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SystemExit(
                f"shared server exited during startup with code {code}; "
                f"see {log_file}"
            )
        ready, last_detail = probe_mcp(host, port)
        if ready:
            return {
                "action": "started",
                "pid": process.pid,
                "url": f"http://{host}:{port}{MCP_PATH}",
                "log": str(log_file),
                "probe": last_detail,
            }
        time.sleep(0.25)

    terminate(process.pid)
    raise SystemExit(
        f"shared server did not become ready within {startup_timeout:.0f}s "
        f"({last_detail}); see {log_file}"
    )


def stop_server(host: str, port: int, pid_file: Path, *, timeout: float = 10.0) -> dict[str, Any]:
    pid = read_pid(pid_file)
    stopped = False
    if pid and terminate(pid, timeout=timeout):
        stopped = True
    try:
        pid_file.unlink()
    except OSError:
        pass

    # A stale pid file, or a server started outside this script, still holds
    # the port. Fall back to matching the command line.
    if port_open(host, port):
        for other in _python_servers_on(port):
            terminate(other, timeout=timeout)
            stopped = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and port_open(host, port):
            time.sleep(0.1)
    return {"action": "stopped" if stopped else "none", "pid": pid, "port_free": not port_open(host, port)}


def _python_servers_on(port: int) -> list[int]:
    """PIDs of ``custom_agent_mcp.web_server`` processes holding ``port``."""
    if os.name == "nt":
        netstat = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        pids = {
            int(part.split()[-1])
            for part in netstat.stdout.splitlines()
            if part.split()[-2:-1] == ["LISTENING"] and f":{port}" in part
        }
    else:
        pids = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fd_dir = entry / "fd"
                sockets = {
                    os.readlink(fd) for fd in fd_dir.iterdir()
                }
            except OSError:
                continue
            del sockets  # port ownership needs /proc/net/tcp; handled by caller
    found: list[int] = []
    for pid in pids:
        command = _command_line(pid)
        if "custom_agent_mcp.web_server" in command:
            found.append(pid)
    return found


def _command_line(pid: int) -> str:
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.stdout or ""
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().decode(
            "utf-8", "replace"
        )
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--start", action="store_true", help="start if needed (default)")
    action.add_argument("--stop", action="store_true", help="stop the shared server")
    action.add_argument("--restart", action="store_true", help="stop, then start")
    action.add_argument("--status", action="store_true", help="print state as JSON")
    parser.add_argument("--host", default=os.getenv("WEBSEARCH_MCP_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("WEBSEARCH_MCP_PORT", DEFAULT_PORT))
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument(
        "--pid-file", type=Path, default=None, help="default: <state-dir>/server.pid"
    )
    parser.add_argument(
        "--log-file", type=Path, default=None, help="default: <state-dir>/server.log"
    )
    parser.add_argument(
        "--lock-file", type=Path, default=None, help="default: <state-dir>/server.lock"
    )
    parser.add_argument("--startup-timeout", type=float, default=DEFAULT_STARTUP_TIMEOUT)
    parser.add_argument("--lock-timeout", type=float, default=DEFAULT_LOCK_TIMEOUT)
    parser.add_argument("--cwd", type=Path, default=REPO_ROOT)
    parser.add_argument("--quiet", action="store_true", help="only print on error")
    args = parser.parse_args(argv)

    pid_file = args.pid_file or args.state_dir / "server.pid"
    log_file = args.log_file or args.state_dir / "server.log"
    lock_file = args.lock_file or args.state_dir / "server.lock"

    if args.status:
        print(json.dumps(status(args.host, args.port, pid_file), indent=2))
        return 0

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(str(lock_file), timeout=args.lock_timeout)
        lock.acquire()
    except FileLockTimeout:
        sys.stderr.write(
            f"another caller held {lock_file} for more than "
            f"{args.lock_timeout:.0f}s\n"
        )
        return 3

    try:
        if args.stop or args.restart:
            result = stop_server(args.host, args.port, pid_file)
            if not args.quiet:
                print(json.dumps(result))
            if args.stop:
                return 0
        result = start_server(
            host=args.host,
            port=args.port,
            env_file=args.env_file,
            pid_file=pid_file,
            log_file=log_file,
            startup_timeout=args.startup_timeout,
            cwd=args.cwd,
        )
        if not args.quiet:
            print(json.dumps(result, indent=2))
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())