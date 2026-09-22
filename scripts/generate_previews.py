#!/usr/bin/env python3
"""Generate and resume low-resolution sprite sheets for the Supabase media bucket.

Requirements: Python 3.10+, ffmpeg, and ffprobe.
Configuration is read from backend/.env (environment variables win).

Run:
    python scripts/generate_previews.py
    python scripts/generate_previews.py --force

The script writes the video_previews row only after every sheet exists. If it is
interrupted, a later run reuses any already-uploaded partial sheets and resumes.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import math
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
VIDEO_EXTENSIONS = {"mp4", "m4v", "webm", "mov", "mkv", "avi", "ogv", "mpeg", "mpg", "ts"}
INTERVAL_SECONDS = 5
COLUMNS = 10
ROWS = 10
THUMBNAIL_WIDTH = 160
THUMBNAIL_HEIGHT = 90


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without requiring python-dotenv."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


load_dotenv(ROOT / "backend" / ".env")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.environ.get("MEDIA_BUCKET", "")
PREVIEW_API_URL = os.environ.get("PREVIEW_API_URL", "http://127.0.0.1:8000").rstrip("/")

if not SUPABASE_URL or not BUCKET:
    raise SystemExit("Missing SUPABASE_URL or MEDIA_BUCKET in backend/.env")

AUTH_HEADERS: dict[str, str] = {}


def login_with_backend() -> str:
    """Authenticate through the same backend route used by the web app."""
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    body = json.dumps({"email": email, "password": password}).encode()
    request = Request(
        f"{PREVIEW_API_URL}/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            session = json.load(response)
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Login failed (HTTP {error.code}): {details}") from error
    except URLError as error:
        raise RuntimeError(f"Login failed: {error.reason}") from error

    access_token = session.get("access_token")
    if not access_token:
        raise RuntimeError("Login failed: the backend did not return an access token.")
    return access_token


def configure_auth() -> None:
    global AUTH_HEADERS
    if SERVICE_ROLE_KEY:
        AUTH_HEADERS = {
            "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
            "apikey": SERVICE_ROLE_KEY,
        }
        return
    if not SUPABASE_ANON_KEY:
        raise RuntimeError(
            "Missing SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY in backend/.env"
        )
    log("SUPABASE_SERVICE_ROLE_KEY is not set; sign in to use the anon key.")
    access_token = login_with_backend()
    AUTH_HEADERS = {
        "Authorization": f"Bearer {access_token}",
        "apikey": SUPABASE_ANON_KEY,
    }


def api_request(path: str, method: str = "GET", body: bytes | None = None,
                content_type: str | None = None, extra_headers: dict[str, str] | None = None):
    headers = dict(AUTH_HEADERS)
    if content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    request = Request(f"{SUPABASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        return urlopen(request, timeout=120)
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {path}: {error.reason}") from error


def encoded_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/"))


def list_storage(prefix: str = "") -> list[dict]:
    result: list[dict] = []
    offset = 0
    while True:
        body = json.dumps({
            "prefix": prefix,
            "limit": 1000,
            "offset": offset,
            "sortBy": {"column": "name", "order": "asc"},
        }).encode()
        with api_request(
            f"/storage/v1/object/list/{quote(BUCKET, safe='')}",
            "POST", body, "application/json",
        ) as response:
            items = json.load(response)
        result.extend(items or [])
        if len(items or []) < 1000:
            return result
        offset += 1000


def list_video_paths(prefix: str = "") -> list[str]:
    if not prefix:
        log("Discovering videos in storage")
    paths: list[str] = []
    for item in list_storage(prefix):
        name = item.get("name", "")
        path = f"{prefix}/{name}" if prefix else name
        if item.get("id") is None:
            paths.extend(list_video_paths(path))
        elif Path(name).suffix.lower().lstrip(".") in VIDEO_EXTENSIONS:
            paths.append(path)
    return paths


def preview_rows() -> dict[str, dict]:
    log("Loading preview manifests")
    with api_request("/rest/v1/video_previews?select=*") as response:
        rows = json.load(response)
    log(f"Loaded {len(rows)} preview manifest(s)")
    return {row["media_path"]: row for row in rows}


def preview_prefix(media_path: str) -> str:
    safe_id = base64.urlsafe_b64encode(media_path.encode()).decode().rstrip("=")
    return f"__previews/{safe_id}"


def delete_storage_objects(paths: list[str]) -> None:
    """Delete storage objects using the storage object's delete endpoint."""
    for path in paths:
        log(f"Deleting stale preview sheet: {path}")
        api_request(
            f"/storage/v1/object/{quote(BUCKET, safe='')}/{encoded_path(path)}",
            "DELETE",
        ).close()


def delete_manifest(media_path: str) -> None:
    log(f"Deleting stale preview manifest: {media_path}")
    api_request(
        f"/rest/v1/video_previews?media_path=eq.{quote(media_path, safe='')}",
        "DELETE",
    ).close()


def cleanup_stale_previews(rows: dict[str, dict], video_paths: set[str]) -> None:
    """Remove manifests and sprite sheets whose source videos no longer exist."""
    stale_paths = sorted(set(rows) - video_paths)
    if not stale_paths:
        log("No stale previews to delete")
        return

    log(f"Deleting previews for {len(stale_paths)} missing video(s)")
    for media_path in stale_paths:
        row = rows[media_path]
        expected_prefix = preview_prefix(media_path)
        prefix = row.get("sprite_prefix") or expected_prefix
        if prefix != expected_prefix:
            log(
                f"Skipping storage cleanup for {media_path}: "
                f"unexpected sprite prefix {prefix!r}"
            )
        else:
            objects = [
                f"{prefix}/{item['name']}"
                for item in list_storage(prefix)
                if item.get("id") is not None
            ]
            if objects:
                delete_storage_objects(objects)
        delete_manifest(media_path)
        rows.pop(media_path, None)


def download_video(media_path: str, destination: Path) -> None:
    log(f"Downloading source video: {media_path}")
    path = f"/storage/v1/object/authenticated/{quote(BUCKET, safe='')}/{encoded_path(media_path)}"
    with api_request(path) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def run(command: list[str], capture_output: bool = False) -> str:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )
    if completed.returncode != 0:
        details = (completed.stderr or "").strip()
        raise RuntimeError(f"{' '.join(command[:1])} failed: {details}")
    return (completed.stdout or "").strip()


def upload_file(local_path: Path, remote_path: str) -> None:
    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    log(f"Uploading preview sheet: {local_path.name}")
    api_request(
        f"/storage/v1/object/{quote(BUCKET, safe='')}/{encoded_path(remote_path)}",
        "POST",
        local_path.read_bytes(),
        content_type,
        {"x-upsert": "true"},
    ).close()


def upsert_manifest(manifest: dict) -> None:
    log(f"Saving preview manifest: {manifest['media_path']}")
    api_request(
        "/rest/v1/video_previews",
        "POST",
        json.dumps(manifest).encode(),
        "application/json",
        {"Prefer": "resolution=merge-duplicates,return=minimal"},
    ).close()


def remote_sheet_names(prefix: str) -> set[str]:
    names = {f"{prefix}/{item['name']}" for item in list_storage(prefix) if item.get("id") is not None}
    return names


def manifest_is_complete(row: dict) -> bool:
    sheets = row.get("sheets") or []
    prefix = row.get("sprite_prefix", "")
    log(f"Checking existing preview: {row.get('media_path', '(unknown)')}")
    return bool(sheets) and set(sheets).issubset(remote_sheet_names(prefix))


def generate_video(media_path: str, previous: dict | None, force: bool) -> None:
    sprite_prefix = preview_prefix(media_path)
    existing_remote = set() if force else remote_sheet_names(sprite_prefix)
    with tempfile.TemporaryDirectory(prefix="adlv-preview-") as temp:
        work = Path(temp)
        source = work / "source"
        sprite_dir = work / "sprites"
        sprite_dir.mkdir()
        log(f"Creating preview: {media_path}")
        download_video(media_path, source)
        log(f"Reading video duration: {media_path}")
        duration = float(run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(source),
        ], capture_output=True))
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("Could not determine video duration")

        filter_graph = (
            f"fps=1/{INTERVAL_SECONDS},scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:"
            f"force_original_aspect_ratio=decrease,pad={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:"
            f"(ow-iw)/2:(oh-ih)/2,setsar=1,tile={COLUMNS}x{ROWS}:padding=0:margin=0"
        )
        log(f"Generating sprite sheets: {media_path}")
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-vf", filter_graph, "-q:v", "6", "-f", "image2",
            str(sprite_dir / "sprite-%03d.jpg"),
        ])

        local_sheets = sorted(sprite_dir.glob("sprite-*.jpg"))
        if not local_sheets:
            raise RuntimeError("ffmpeg did not create any sprite sheets")
        remote_sheets: list[str] = []
        log(f"Uploading sprite sheets: {media_path}")
        for local_sheet in local_sheets:
            remote_path = f"{sprite_prefix}/{local_sheet.name}"
            if force or remote_path not in existing_remote:
                upload_file(local_sheet, remote_path)
            remote_sheets.append(remote_path)

        upsert_manifest({
            "media_path": media_path,
            "sprite_prefix": sprite_prefix,
            "sheets": remote_sheets,
            "duration_seconds": duration,
            "interval_seconds": INTERVAL_SECONDS,
            "columns": COLUMNS,
            "rows": ROWS,
            "thumbnail_width": THUMBNAIL_WIDTH,
            "thumbnail_height": THUMBNAIL_HEIGHT,
        })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Regenerate and overwrite every sheet")
    args = parser.parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ERROR: ffmpeg and ffprobe must be installed and available on PATH", file=sys.stderr)
        return 2
    try:
        configure_auth()
    except (RuntimeError, EOFError, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    rows = preview_rows()
    videos = list_video_paths()
    log(f"Found {len(videos)} video(s)")
    cleanup_stale_previews(rows, set(videos))
    complete = {path for path, row in rows.items() if manifest_is_complete(row)}
    for media_path in videos:
        if not args.force and media_path in complete:
            log(f"Skipping complete video: {media_path}")
            continue
        try:
            generate_video(media_path, rows.get(media_path), args.force)
        except Exception as error:  # Continue processing the remaining library.
            log(f"ERROR: {media_path}: {error}")
    log("Preview generation finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
