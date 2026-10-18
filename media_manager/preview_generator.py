"""Standalone preview generator used by the native media manager.

It deliberately has no project imports and reads all connection settings from
command-line arguments supplied by the dashboard.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

VIDEO_EXTENSIONS = {"mp4", "m4v", "webm", "mov", "mkv", "avi", "ogv", "mpeg", "mpg", "ts"}
INTERVAL_SECONDS, COLUMNS, ROWS = 5, 10, 10
THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT = 160, 90


class Client:
    def __init__(self, url: str, key: str, bucket: str): self.url, self.key, self.bucket = url.rstrip("/"), key, bucket
    def request(self, path: str, method: str = "GET", data: bytes | None = None, content_type: str = "application/json", extra: dict[str, str] | None = None):
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": content_type}; headers.update(extra or {})
        try: return urlopen(Request(self.url + path, data=data, method=method, headers=headers), timeout=180)
        except (HTTPError, URLError) as exc: raise RuntimeError(f"{method} {path}: {exc}") from exc
    def json(self, path: str, method: str = "GET", body: object | None = None):
        data = None if body is None else json.dumps(body).encode();
        with self.request(path, method, data) as response: return json.load(response)
    def list_storage(self, prefix: str = ""):
        return self.json(f"/storage/v1/object/list/{quote(self.bucket, safe='')}", "POST", {"prefix": prefix, "limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"}}) or []
    def upload(self, local: Path, remote: str):
        self.request(f"/storage/v1/object/{quote(self.bucket, safe='')}/{quote(remote, safe='/')}", "POST", local.read_bytes(), mimetypes.guess_type(local.name)[0] or "image/jpeg", {"x-upsert": "true"}).close()


def log(message: str): print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)
def encoded(path: str) -> str: return "/".join(quote(part, safe="") for part in path.split("/"))
def preview_prefix(path: str) -> str: return "__previews/" + base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def video_paths(client: Client, prefix: str = "") -> list[str]:
    paths = []
    for item in client.list_storage(prefix):
        name = item.get("name", ""); path = f"{prefix}/{name}" if prefix else name
        if not name: continue
        if item.get("id") is None: paths.extend(video_paths(client, path))
        elif Path(name).suffix.lower().lstrip(".") in VIDEO_EXTENSIONS: paths.append(path)
    return paths


def download(client: Client, remote: str, local: Path):
    with client.request(f"/storage/v1/object/authenticated/{quote(client.bucket, safe='')}/{encoded(remote)}") as source, local.open("wb") as target: shutil.copyfileobj(source, target)


def generate(client: Client, media_path: str, force: bool):
    prefix = preview_prefix(media_path); existing = {f"{prefix}/{x.get('name')}" for x in client.list_storage(prefix) if x.get("id") is not None}
    with tempfile.TemporaryDirectory(prefix="adlv-media-preview-") as temp:
        work = Path(temp); source, sprites = work / "source", work / "sprites"; sprites.mkdir(); log(f"Downloading {media_path}"); download(client, media_path, source)
        duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)], text=True).strip())
        graph = f"fps=1/{INTERVAL_SECONDS},scale={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:force_original_aspect_ratio=decrease,pad={THUMBNAIL_WIDTH}:{THUMBNAIL_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,tile={COLUMNS}x{ROWS}:padding=0:margin=0"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", graph, "-q:v", "6", "-f", "image2", str(sprites / "sprite-%03d.jpg")], check=True)
        remote_sheets = []
        for sheet in sorted(sprites.glob("sprite-*.jpg")):
            remote = f"{prefix}/{sheet.name}"; remote_sheets.append(remote)
            if force or remote not in existing: log(f"Uploading {sheet.name}"); client.upload(sheet, remote)
        body = {"media_path": media_path, "sprite_prefix": prefix, "sheets": remote_sheets, "duration_seconds": duration, "interval_seconds": INTERVAL_SECONDS, "columns": COLUMNS, "rows": ROWS, "thumbnail_width": THUMBNAIL_WIDTH, "thumbnail_height": THUMBNAIL_HEIGHT}
        client.request("/rest/v1/video_previews?on_conflict=media_path", "POST", json.dumps(body).encode(), extra={"Prefer": "resolution=merge-duplicates,return=minimal"}).close()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--supabase-url", required=True); parser.add_argument("--supabase-key", required=True); parser.add_argument("--bucket", required=True); parser.add_argument("--video", required=True); parser.add_argument("--force", action="store_true"); args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"): print("ERROR: ffmpeg and ffprobe are required on PATH", file=sys.stderr); return 2
    client = Client(args.supabase_url, args.supabase_key, args.bucket)
    if args.video not in video_paths(client): print(f"ERROR: video not found: {args.video}", file=sys.stderr); return 2
    try: generate(client, args.video, args.force); print("Preview generation finished", flush=True); return 0
    except Exception as exc: print(f"ERROR: {exc}", file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(main())
