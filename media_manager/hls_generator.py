#!/usr/bin/env python3
"""Generate and upload a device-compatible HLS rendition for one video.

This is intentionally a trusted media_manager job. It may use the Supabase
service-role key, while the public backend only reads the resulting artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CACHE_VERSION = "hls-v6"
EVENT_VERSION = 1


def emit_event(job_id: str, phase: str, event: str, **fields: object) -> None:
    """Write the only machine-readable stream emitted by this worker."""
    payload = {
        "version": EVENT_VERSION,
        "job_id": job_id,
        "phase": phase,
        "event": event,
        "timestamp": time.time(),
        **fields,
    }
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def encoded_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in path.split("/"))


def cache_key(video: str) -> str:
    return hashlib.sha256(f"{CACHE_VERSION}:{video}".encode("utf-8")).hexdigest()


def hls_prefix(video: str) -> str:
    path = Path(video)
    if path.name == "index.m3u8" and path.parent.name.endswith("-hls"):
        return "/".join(path.parts[:-1])
    return "/".join((*path.parts[:-1], f"{path.stem}-hls"))


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


def run(video: str, supabase_url: str, key: str, bucket: str, ffmpeg: str, local_source: str | None = None, job_id: str | None = None) -> None:
    job_id = job_id or str(uuid.uuid4())
    prefix = hls_prefix(video)
    base_url = f"{supabase_url.rstrip('/')}/storage/v1/object/{quote(bucket, safe='')}"
    manifest_url = f"{base_url}/{encoded_path(prefix + '/index.m3u8')}"
    if request(manifest_url, key, allow_missing=True) is not None:
        emit_event(job_id, "complete", "complete", overall_percent=100)
        return

    with tempfile.TemporaryDirectory(prefix="adlv-hls-") as work:
        work_path = Path(work)
        source = work_path / "source"
        output = work_path
        if local_source:
            source = Path(local_source)
        else:
            source_url = f"{base_url}/{encoded_path(video)}"
            emit_event(job_id, "preparing_source", "started")
            with request(source_url, key) as response, source.open("wb") as destination:
                shutil.copyfileobj(response, destination, length=1024 * 1024)
            emit_event(job_id, "preparing_source", "completed", source="remote")

        if local_source:
            emit_event(job_id, "preparing_source", "completed", source="local")

        command = [
            ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:2", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
            "-c:v", "libx264", "-preset", os.environ.get("HLS_PRESET", "veryfast"),
            "-profile:v", "main", "-level:v", "4.0", "-crf", os.environ.get("HLS_CRF", "23"),
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(output / "segment-%05d.ts"), str(output / "index.m3u8"),
        ]
        emit_event(job_id, "generating_hls", "started")
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        progress_fields: dict[str, str] = {}
        assert process.stderr is not None
        for line in process.stderr:
            line = line.strip()
            if "=" in line and not line.startswith("["):
                name, value = line.split("=", 1)
                progress_fields[name] = value
                if name == "progress":
                    emit_event(job_id, "generating_hls", "progress", media_time_us=int(progress_fields.get("out_time_us", "0") or 0), speed=progress_fields.get("speed"))
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"FFmpeg exited with status {return_code}.")
        emit_event(job_id, "generating_hls", "completed")
        manifest = (output / "index.m3u8").read_bytes()
        segments = sorted(output.glob("segment-*.ts"))
        total_bytes = sum(segment.stat().st_size for segment in segments)
        completed_bytes = 0
        emit_event(job_id, "uploading_segments", "started", total_segments=len(segments), total_bytes=total_bytes, completed_segments=0, completed_bytes=0)
        for index, segment in enumerate(segments, 1):
            upload(f"{base_url}/{encoded_path(prefix + '/' + segment.name)}", key, segment.read_bytes(), "video/mp2t")
            completed_bytes += segment.stat().st_size
            emit_event(job_id, "uploading_segments", "progress", segment=segment.name, segment_index=index, total_segments=len(segments), completed_segments=index, total_bytes=total_bytes, completed_bytes=completed_bytes)
        emit_event(job_id, "finalizing_manifest", "started")
        upload(manifest_url, key, manifest, "application/vnd.apple.mpegurl")
        emit_event(job_id, "finalizing_manifest", "completed")
    emit_event(job_id, "complete", "complete", overall_percent=100)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supabase-url", required=True)
    parser.add_argument("--supabase-key", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--source", help="Use this local source instead of downloading the original.")
    parser.add_argument("--job-id")
    args = parser.parse_args()
    try:
        run(args.video, args.supabase_url, args.supabase_key, args.bucket, args.ffmpeg, args.source, args.job_id)
    except Exception as exc:
        emit_event(args.job_id or "unknown", "failed", "failed", error=str(exc), actionable_error=str(exc))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
