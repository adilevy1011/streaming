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
CHECKPOINT_VERSION = 1
STORAGE_LIST_PAGE_SIZE = 1000


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


def checkpoint_path(video: str, source_size: int, source_mtime_ns: int) -> Path:
    state_root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME") or tempfile.gettempdir()
    identity = f"{CACHE_VERSION}:{video}:{source_size}:{source_mtime_ns}".encode("utf-8")
    return Path(state_root) / "ADLV Media Manager" / "hls-checkpoints" / (hashlib.sha256(identity).hexdigest() + ".json")


def checkpoint_identity(video: str, source_size: int, source_mtime_ns: int) -> dict[str, object]:
    return {"video": video, "format": CACHE_VERSION, "source_size": source_size, "source_mtime_ns": source_mtime_ns}


def load_checkpoint(path: Path, identity: dict[str, object]) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"version": CHECKPOINT_VERSION, "identity": identity, "segments": {}, "completed": {}, "phase": "preparing_source"}
    if payload.get("version") != CHECKPOINT_VERSION or payload.get("identity") != identity:
        return {"version": CHECKPOINT_VERSION, "identity": identity, "segments": {}, "completed": {}, "phase": "preparing_source"}
    return payload


def write_checkpoint(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["version"] = CHECKPOINT_VERSION
    payload["updated_at"] = time.time()
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def remote_segment_sizes(supabase_url: str, key: str, bucket: str, prefix: str) -> dict[str, int]:
    """List every object under a prefix; Storage listing is paginated."""
    url = f"{supabase_url.rstrip('/')}/storage/v1/object/list/{quote(bucket, safe='')}"
    result: dict[str, int] = {}
    offset = 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": STORAGE_LIST_PAGE_SIZE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}}).encode("utf-8")
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=120) as response:
                page = json.loads(response.read().decode("utf-8") or "[]")
        except (HTTPError, URLError, ValueError) as exc:
            raise RuntimeError(f"Storage listing failed for {prefix}: {exc}") from exc
        if not isinstance(page, list):
            raise RuntimeError(f"Storage listing returned an invalid page for {prefix}.")
        for entry in page:
            name = str(entry.get("name") or "")
            if name.startswith(prefix + "/"):
                name = name[len(prefix) + 1:]
            metadata = entry.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            size = entry.get("size") or metadata.get("size")
            if name.startswith("segment-") and name.endswith(".ts") and size is not None:
                try:
                    result[name] = int(size)
                except (TypeError, ValueError):
                    continue
        if len(page) < STORAGE_LIST_PAGE_SIZE:
            return result
        offset += STORAGE_LIST_PAGE_SIZE


def verified_segments(expected: dict[str, int], remote: dict[str, int]) -> dict[str, int]:
    return {name: size for name, size in expected.items() if remote.get(name) == size}


def manifest_segment_names(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("segment-") and line.strip().endswith(".ts")]


def manifest_metadata_matches(text: str, source_size: int, source_mtime_ns: int) -> bool:
    required = {
        "#ADLV-HLS-FORMAT": CACHE_VERSION,
        "#ADLV-SOURCE-SIZE": str(source_size),
        "#ADLV-SOURCE-MTIME-NS": str(source_mtime_ns),
    }
    found = {}
    for line in text.splitlines():
        if "=" in line and line.startswith("#ADLV-"):
            name, value = line.split("=", 1)
            found[name] = value
    return all(found.get(name) == value for name, value in required.items())


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

        source_stat = source.stat()
        existing_manifest = request(manifest_url, key, allow_missing=True)
        if existing_manifest is not None:
            with existing_manifest:
                existing_text = existing_manifest.read().decode("utf-8", errors="replace")
            remote_sizes = remote_segment_sizes(supabase_url, key, bucket, prefix)
            referenced = manifest_segment_names(existing_text)
            complete = (
                existing_text.startswith("#EXTM3U")
                and "#EXT-X-ENDLIST" in existing_text
                and bool(referenced)
                and all(name in remote_sizes and remote_sizes[name] > 0 for name in referenced)
                and manifest_metadata_matches(existing_text, source_stat.st_size, source_stat.st_mtime_ns)
            )
            if complete:
                emit_event(job_id, "complete", "complete", overall_percent=100, resumed_manifest=True)
                return
        identity = checkpoint_identity(video, source_stat.st_size, source_stat.st_mtime_ns)
        checkpoint = checkpoint_path(video, source_stat.st_size, source_stat.st_mtime_ns)
        checkpoint_data = load_checkpoint(checkpoint, identity)

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
        manifest_text = (output / "index.m3u8").read_text(encoding="utf-8")
        manifest_text = manifest_text.replace(
            "#EXTM3U\n",
            f"#EXTM3U\n#ADLV-HLS-FORMAT={CACHE_VERSION}\n#ADLV-SOURCE-SIZE={source_stat.st_size}\n#ADLV-SOURCE-MTIME-NS={source_stat.st_mtime_ns}\n",
            1,
        )
        manifest = manifest_text.encode("utf-8")
        segments = sorted(output.glob("segment-*.ts"))
        total_bytes = sum(segment.stat().st_size for segment in segments)
        expected_sizes = {segment.name: segment.stat().st_size for segment in segments}
        checkpoint_data["segments"] = expected_sizes
        checkpoint_data["phase"] = "uploading_segments"
        remote_sizes = remote_segment_sizes(supabase_url, key, bucket, prefix)
        verified = verified_segments(expected_sizes, remote_sizes)
        checkpoint_data["completed"] = verified
        write_checkpoint(checkpoint, checkpoint_data)
        completed_bytes = sum(verified.values())
        completed_segments = len(verified)
        workers = upload_workers()
        retries = max_retries()
        emit_event(job_id, "uploading_segments", "started", total_segments=len(segments), total_bytes=total_bytes, completed_segments=completed_segments, completed_bytes=completed_bytes, verified_segments=completed_segments, missing_segments=len(segments) - completed_segments, workers=workers, max_retries=retries)

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
            for index, segment in enumerate(segments, 1):
                if segment.name in verified:
                    emit_event(job_id, "uploading_segments", "progress", segment=segment.name, segment_index=index, total_segments=len(segments), completed_segments=completed_segments, total_bytes=total_bytes, completed_bytes=completed_bytes, retry_count=0, resumed=True)
                    continue
                _, name, size, retry_count = upload_segment(index, segment)
                completed_bytes += size
                completed_segments += 1
                verified[name] = size
                checkpoint_data["completed"] = verified
                write_checkpoint(checkpoint, checkpoint_data)
                emit_event(job_id, "uploading_segments", "progress", segment=name, segment_index=index, total_segments=len(segments), completed_segments=completed_segments, total_bytes=total_bytes, completed_bytes=completed_bytes, retry_count=retry_count)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hls-upload") as executor:
                futures = [executor.submit(upload_segment, index, segment) for index, segment in enumerate(segments, 1) if segment.name not in verified]
                for future in as_completed(futures):
                    index, name, size, retry_count = future.result()
                    completed_segments += 1
                    completed_bytes += size
                    verified[name] = size
                    checkpoint_data["completed"] = verified
                    write_checkpoint(checkpoint, checkpoint_data)
                    emit_event(job_id, "uploading_segments", "progress", segment=name, segment_index=index, total_segments=len(segments), completed_segments=completed_segments, total_bytes=total_bytes, completed_bytes=completed_bytes, retry_count=retry_count)
        emit_event(job_id, "finalizing_manifest", "started")
        checkpoint_data["phase"] = "finalizing_manifest"
        write_checkpoint(checkpoint, checkpoint_data)
        upload_with_retries(manifest_url, key, manifest, "application/vnd.apple.mpegurl", retries)
        emit_event(job_id, "finalizing_manifest", "completed")
        try:
            checkpoint.unlink()
        except FileNotFoundError:
            pass
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
