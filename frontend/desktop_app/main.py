"""ADLV Media Streamer desktop shell.

A desktop app that uses the same frontend as the web app.

Usage:
    python main.py --url http://127.0.0.1:8000

"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_URL = "http://127.0.0.1:8000/"
URL_ENV_KEYS = ("ADLV_BACKEND_URL", "BACKEND_URL", "API_URL", "PREVIEW_API_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ICON = PROJECT_ROOT / "frontend" / "favicon.ico"


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("The backend URL cannot be empty.")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Backend URL must be an http:// or https:// URL.")
    return value.rstrip("/") + "/"


def cache_busted_start_url(url: str) -> str:
    """Force a fresh document bundle while retaining the persistent session."""
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}desktop_boot={int(time.time())}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ADLV Media Streamer desktop app")
    parser.add_argument(
        "--url",
        default=None,
        help="URL of the running ADLV backend (overrides environment settings)",
    )
    parser.add_argument(
        "--browser-fallback",
        action="store_true",
        help="Open the frontend in the system browser instead of a desktop window.",
    )
    return parser.parse_args()


def read_backend_env_url() -> str | None:
    """Read only URL settings from backend/.env; never expose other values."""
    roots = [Path(__file__).resolve().parents[2], Path.cwd()]
    executable = Path(sys.executable).resolve()
    roots.extend(executable.parents[:5])
    env_files: list[Path] = []
    for root in roots:
        env_files.extend((root / "backend" / ".env", root / "backend.env"))

    for env_file in dict.fromkeys(env_files):
        if not env_file.is_file():
            continue
        values: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in URL_ENV_KEYS:
                values[key] = value.strip().strip('"').strip("'")
        found = next((values[key] for key in URL_ENV_KEYS if values.get(key)), None)
        if found:
            return found
    return None


def resolve_backend_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    for key in URL_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return read_backend_env_url() or DEFAULT_URL


def browser_storage_path() -> Path:
    """Return a private, persistent profile directory for the desktop shell."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "ADLV Media Streamer" / "webview-profile"
    return Path.home() / ".adlv-media-streamer" / "webview-profile"


class DesktopApi:
    """Small bridge used by the frontend for native desktop window controls."""

    def __init__(self) -> None:
        self._window = None

    def toggle_fullscreen(self) -> bool:
        print("[ADLV desktop] toggle_fullscreen bridge called", flush=True)
        if self._window is None:
            print("[ADLV desktop] fullscreen skipped: window is not ready", flush=True)
            return False
        try:
            self._window.toggle_fullscreen()
            print("[ADLV desktop] native fullscreen toggle requested", flush=True)
            return True
        except Exception as exc:
            print(f"[ADLV desktop] fullscreen error: {exc!r}", flush=True)
            return False

    def desktop_log(self, message: str) -> None:
        print(f"[ADLV desktop] JS: {message}", flush=True)


def run() -> int:
    args = parse_args()
    try:
        url = normalize_url(resolve_backend_url(args.url))
    except ValueError as exc:
        print(f"ADLV desktop app: {exc}", file=sys.stderr)
        return 2

    if args.browser_fallback:
        webbrowser.open(url)
        return 0

    print(f"[ADLV desktop] backend URL: {url}", flush=True)
    print(f"[ADLV desktop] storage path: {browser_storage_path()}", flush=True)
    print(f"[ADLV desktop] icon path: {APP_ICON}", flush=True)

    try:
        import webview
    except ImportError:
        print(
            "PyWebView is not installed. Install frontend/desktop_app/requirements.txt "
            "or rerun with --browser-fallback.",
            file=sys.stderr,
        )
        return 1

    storage_path = browser_storage_path()
    storage_path.mkdir(parents=True, exist_ok=True)
    desktop_api = DesktopApi()

    window = webview.create_window(
        "Adlv Media Stream",
        cache_busted_start_url(url),
        js_api=desktop_api,
        width=1280,
        height=820,
        min_size=(900, 620),
        resizable=True,
        text_select=True,
        confirm_close=True,
    )
    desktop_api._window = window
    print("[ADLV desktop] WebView window created; JS bridge attached", flush=True)
    # EdgeChromium/WebView2 is preferred on Windows by PyWebView and supports
    # the media element, range requests, localStorage, and WebSockets used by
    # the existing frontend.
    webview.start(
        gui="edgechromium",
        debug=True,
        private_mode=False,
        storage_path=str(storage_path),
        icon=str(APP_ICON),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
