#!/usr/bin/env python3
"""Generate and upload a device-compatible HLS rendition for one video.

This is intentionally a trusted media_manager job. It may use the Supabase
service-role key, while the public backend only reads the resulting artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CACHE_VERSION = "hls-v5"
HLS_PREFIX = "__previews/hls"


def encoded_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/"))


def cache_key(video: str) -> str:
    return hashlib.sha256(f"{CACHE_VERSION}:{video}".encode("utf-8")).hexdigest()


def request(url: str, key: str, method: str = "GET", body: bytes | None = None, content_type: str | None = None, allow_missing: bool = False):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        return urlopen(Request(url, data=body, headers=headers, method=method), timeout=120)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if allow_missing and (exc.code == 404 or '"code":"NoSuchKey"' in detail or '"error":"not_found"' in detail):
            return None
        raise RuntimeError(f"Storage request failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Storage request failed: {exc.reason}") from exc


def upload(url: str, key: str, body: bytes, content_type: str) -> None:
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": content_type, "x-upsert": "true"}
    try:
        with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=120) as response:
            if response.status >= 400:
                raise RuntimeError(f"Storage upload failed ({response.status})")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Storage upload failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Storage upload failed: {exc.reason}") from exc


def run(video: str, supabase_url: str, key: str, bucket: str, ffmpeg: str) -> None:
    digest = cache_key(video)
    prefix = f"{HLS_PREFIX}/{digest}"
    base_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{quote(bucket, safe='')}"
    manifest_url = f"{base_url}/{encoded_path(prefix + '/index.m3u8')}"
    if request(manifest_url, key, allow_missing=True) is not None:
        print("HLS already exists; skipping.", flush=True)
        return

    source_url = f"{base_url}/{encoded_path(video)}"
    with tempfile.TemporaryDirectory(prefix="adlv-hls-") as work:
        work_path = Path(work)
        source = work_path / "source"
        output = work_path
        print("Downloading source to the media manager workstation...", flush=True)
        with request(source_url, key) as response, source.open("wb") as destination:
            shutil.copyfileobj(response, destination, length=1024 * 1024)

        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
            "-c:v", "libx264", "-preset", os.environ.get("HLS_PRESET", "veryfast"),
            "-profile:v", "main", "-level:v", "4.0", "-crf", os.environ.get("HLS_CRF", "23"),
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(output / "segment-%05d.ts"), str(output / "index.m3u8"),
        ]
        print("Transcoding HLS locally...", flush=True)
        subprocess.run(command, check=True)
        manifest = (output / "index.m3u8").read_bytes()
        for segment in sorted(output.glob("segment-*.ts")):
            print(f"Uploading {segment.name}...", flush=True)
            upload(f"{base_url}/{encoded_path(prefix + '/' + segment.name)}", key, segment.read_bytes(), "video/mp2t")
        upload(manifest_url, key, manifest, "application/vnd.apple.mpegurl")
    print("HLS generation complete.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supabase-url", required=True)
    parser.add_argument("--supabase-key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    run(args.video, args.supabase_url, args.supabase_key, args.bucket, args.ffmpeg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
