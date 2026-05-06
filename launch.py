"""MacSweep launcher.

Starts the FastAPI engine in a daemon thread on a free localhost port, then
opens a pywebview native window pointing at it. Closing the window exits the
process. No browser, no terminal, no server to manage.
"""
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import uvicorn
import webview

from backend.main import app


def find_free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def serve(port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def wait_until_ready(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def bring_to_front() -> None:
    """Force the app to the front — pywebview windows launched via Terminal
    sometimes open behind. Cocoa-only; safe no-op on other platforms."""
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyRegular
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        pass


def kill_existing() -> None:
    """Kill any prior MacSweep process so launching the .app twice doesn't leave
    a stale instance serving an old version of the frontend."""
    import subprocess, os
    me = os.getpid()
    try:
        out = subprocess.check_output(["pgrep", "-f", "launch.py"], text=True)
        for line in out.strip().splitlines():
            try:
                pid = int(line.strip())
                if pid != me:
                    os.kill(pid, 9)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    except subprocess.CalledProcessError:
        pass


def main() -> None:
    kill_existing()
    port = find_free_port()
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    if not wait_until_ready(port):
        sys.stderr.write("MacSweep: backend failed to start\n")
        sys.exit(1)

    # Per-launch cache buster on the URL itself. WKWebView keys its in-memory
    # page cache on the full URL, so a constant `127.0.0.1:8765/` would
    # serve the previous launch's cached HTML even with no-store headers.
    # Adding a unique launch ID to the URL guarantees a fresh HTML fetch.
    launch_id = int(time.time() * 1000)
    webview.create_window(
        "MacSweep",
        f"http://127.0.0.1:{port}/?launch={launch_id}",
        width=1320,
        height=880,
        min_size=(960, 640),
        background_color="#131318",
    )
    # private_mode=True → ephemeral session (no persistent NSURLCache, cookies,
    # or local storage from prior runs). Critical: without this, WKWebView
    # was serving the previous session's cached JS/CSS even after we bumped
    # mtimes and added Cache-Control: no-store. Now every launch is clean.
    webview.start(bring_to_front, private_mode=True)


if __name__ == "__main__":
    main()
