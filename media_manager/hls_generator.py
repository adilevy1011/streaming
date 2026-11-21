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
import random
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CACHE_VERSION = "hls-v6"
EVENT_VERSION = 1
DEFAULT_UPLOAD_WORKERS = 1
DEFAULT_MAX_RETRIES = 5
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0


class StorageUploadError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def upload_workers() -> int:
    try:
        return max(1, int(os.environ.get("HLS_UPLOAD_WORKERS", str(DEFAULT_UPLOAD_WORKERS))))
    except ValueError:
        return DEFAULT_UPLOAD_WORKERS


def max_retries() -> int:
    try:
        return max(0, int(os.environ.get("HLS_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))))
    except ValueError:
        return DEFAULT_MAX_RETRIES


def is_retryable_upload_error(error: BaseException) -> bool:
    if isinstance(error, StorageUploadError):
        return error.status_code is None or error.status_code in {408, 429} or error.status_code >= 500
    return isinstance(error, (TimeoutError, URLError))


def retry_delay_seconds(error: BaseException, retry_number: int) -> float:
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        return min(RETRY_MAX_SECONDS, max(0.0, retry_after))
    exponential = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, retry_number - 1)))
    return min(RETRY_MAX_SECONDS, exponential + random.uniform(0, exponential * 0.25))


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


def validate_local_source(path: str | Path, expected_size: int | None = None, expected_mtime_ns: int | None = None) -> Path:
    source = Path(path)
    try:
        stat = source.stat()
    except OSError as exc:
        raise RuntimeError(f"Local HLS source is unavailable: {source}") from exc
    if not source.is_file():
        raise RuntimeError(f"Local HLS source is not a file: {source}")
    if expected_size is not None and stat.st_size != expected_size:
        raise RuntimeError(f"Local HLS source changed size after upload: {source}")
    if expected_mtime_ns is not None and stat.st_mtime_ns != expected_mtime_ns:
        raise RuntimeError(f"Local HLS source changed after upload: {source}")
    return source


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
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After")) if exc.headers.get("Retry-After") else None
        except (TypeError, ValueError):
            retry_after = None
        raise StorageUploadError(f"Storage upload failed ({exc.code}): {detail}", exc.code, retry_after) from exc
    except URLError as exc:
        raise StorageUploadError(f"Storage upload failed: {exc.reason}") from exc


def upload_with_retries(url: str, key: str, body: bytes, content_type: str, retries: int | None = None) -> int:
    max_attempts = max_retries() if retries is None else max(0, retries)
    retry_count = 0
    while True:
        try:
            upload(url, key, body, content_type)
            return retry_count
        except Exception as exc:
            if retry_count >= max_attempts or not is_retryable_upload_error(exc):
                raise RuntimeError(f"{exc} (retries={retry_count})") from exc
            retry_count += 1
            time.sleep(retry_delay_seconds(exc, retry_count))


def run(video: str, supabase_url: str, key: str, bucket: str, ffmpeg: str, local_source: str | None = None, job_id: str | None = None, source_size: int | None = None, source_mtime_ns: int | None = None) -> None:
    job_id = job_id or str(uuid.uuid4())
    validated_local_source = None
    if local_source:
        emit_event(job_id, "preparing_source", "started", source="local")
        validated_local_source = validate_local_source(local_source, source_size, source_mtime_ns)
        emit_event(job_id, "preparing_source", "completed", source="local")
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
        if validated_local_source:
            source = validated_local_source
        else:
            source_url = f"{base_url}/{encoded_path(video)}"
            emit_event(job_id, "preparing_source", "started")
            with request(source_url, key) as response, source.open("wb") as destination:
                shutil.copyfileobj(response, destination, length=1024 * 1024)
            emit_event(job_id, "preparing_source", "completed", source="remote")

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
        workers = upload_workers()
        retries = max_retries()
        emit_event(job_id, "uploading_segments", "started", total_segments=len(segments), total_bytes=total_bytes, completed_segments=0, completed_bytes=0, workers=workers, max_retries=retries)

        def upload_segment(index: int, segment: Path) -> tuple[int, str, int, int]:
            body = segment.read_bytes()
            object_path = prefix + "/" + segment.name
            try:
                retry_count = upload_with_retries(
                    f"{base_url}/{encoded_path(object_path)}", key, body, "video/mp2t", retries
                )
            except Exception as exc:
                raise RuntimeError(f"HLS segment upload failed: {object_path}: {exc}") from exc
            return index, segment.name, len(body), retry_count

        if workers == 1:
            results = (upload_segment(index, segment) for index, segment in enumerate(segments, 1))
            for index, name, size, retry_count in results:
                completed_bytes += size
                emit_event(job_id, "uploading_segments", "progress", segment=name, segment_index=index, total_segments=len(segments), completed_segments=(index if workers == 1 else 0), total_bytes=total_bytes, completed_bytes=completed_bytes, retry_count=retry_count)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hls-upload") as executor:
                futures = [executor.submit(upload_segment, index, segment) for index, segment in enumerate(segments, 1)]
                completed_segments = 0
                for future in as_completed(futures):
                    index, name, size, retry_count = future.result()
                    completed_segments += 1
                    completed_bytes += size
                    emit_event(job_id, "uploading_segments", "progress", segment=name, segment_index=index, total_segments=len(segments), completed_segments=completed_segments, total_bytes=total_bytes, completed_bytes=completed_bytes, retry_count=retry_count)
        emit_event(job_id, "finalizing_manifest", "started")
        upload_with_retries(manifest_url, key, manifest, "application/vnd.apple.mpegurl", retries)
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
    parser.add_argument("--source-size", type=int)
    parser.add_argument("--source-mtime-ns", type=int)
    parser.add_argument("--job-id")
    args = parser.parse_args()
    try:
        run(args.video, args.supabase_url, args.supabase_key, args.bucket, args.ffmpeg, args.source, args.job_id, args.source_size, args.source_mtime_ns)
    except Exception as exc:
        emit_event(args.job_id or "unknown", "failed", "failed", error=str(exc), actionable_error=str(exc))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
