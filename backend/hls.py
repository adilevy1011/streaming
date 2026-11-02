"""Progressive on-demand HLS transcoding and cache helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

HLS_CACHE_DIR = Path(os.environ.get("HLS_CACHE_DIR", str(Path(tempfile.gettempdir()) / "adlv-media-hls")))
CACHE_VERSION = "hls-v4"
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
HLS_START_TIMEOUT = int(os.environ.get("HLS_START_TIMEOUT", "120"))

_locks: dict[str, threading.Lock] = {}
_jobs: dict[str, "_Job"] = {}
_state_guard = threading.Lock()


@dataclass
class _Job:
    ready: threading.Event
    done: threading.Event
    error: str | None = None


def cache_key(media_path: str) -> str:
    return hashlib.sha256(f"{CACHE_VERSION}:{media_path}".encode("utf-8")).hexdigest()


def cache_dir(media_path: str) -> Path:
    return HLS_CACHE_DIR / cache_key(media_path)


def _lock_for(media_path: str) -> threading.Lock:
    with _state_guard:
        return _locks.setdefault(media_path, threading.Lock())


def manifest_path(media_path: str) -> Path:
    return cache_dir(media_path) / "index.m3u8"


def ffmpeg_binary() -> str | None:
    return shutil.which(FFMPEG_PATH) or (FFMPEG_PATH if Path(FFMPEG_PATH).is_file() else None)


def is_complete(media_path: str) -> bool:
    manifest = manifest_path(media_path)
    if not manifest.is_file():
        return False
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    segments = [line for line in lines if line and not line.startswith("#")]
    return "#EXT-X-ENDLIST" in lines and bool(segments) and all(
        (manifest.parent / line.split("?", 1)[0]).is_file() for line in segments
    )


def _has_ready_segment(media_path: str) -> bool:
    manifest = manifest_path(media_path)
    if not manifest.is_file():
        return False
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(
        line and not line.startswith("#") and (manifest.parent / line.split("?", 1)[0]).is_file()
        for line in lines
    )


def _build(media_path: str, media_url: str, headers: dict[str, str], ffmpeg: str, job: _Job) -> None:
    target = cache_dir(media_path)
    work_dir = HLS_CACHE_DIR / f".{cache_key(media_path)}-source"
    source = work_dir / "source"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True, timeout=None) as client:
            logger.info("Downloading source for HLS: %s", media_path)
            with client.stream("GET", media_url, headers=headers) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"source download returned HTTP {response.status_code}")
                with source.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        output.write(chunk)

        target.mkdir(parents=True, exist_ok=True)
        logger.info("Starting progressive FFmpeg HLS transcode for %s", media_path)
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
            "-c:v", "libx264", "-preset", os.environ.get("HLS_PRESET", "veryfast"),
            "-profile:v", "main", "-level:v", "4.0", "-crf", os.environ.get("HLS_CRF", "23"),
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(target / "segment-%05d.ts"), str(target / "index.m3u8"),
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while True:
            if _has_ready_segment(media_path):
                job.ready.set()
            try:
                _, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            detail = (stderr or "unknown FFmpeg error").strip()[-4000:]
            logger.error("FFmpeg HLS transcode failed for %s: %s", media_path, detail)
            raise RuntimeError("FFmpeg failed")
        job.ready.set()
        logger.info("HLS generation completed for %s", media_path)
    except Exception as exc:
        job.error = str(exc)
        logger.exception("HLS generation failed for %s", media_path)
        try:
            if target.exists():
                shutil.rmtree(target)
        except OSError:
            logger.exception("Unable to clean failed HLS cache for %s", media_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        job.done.set()
        with _state_guard:
            if _jobs.get(media_path) is job:
                del _jobs[media_path]


def generate(media_path: str, media_url: str, headers: dict[str, str]) -> Path:
    """Start or join a progressive VOD transcode and return its playlist."""
    ffmpeg = ffmpeg_binary()
    if ffmpeg is None:
        raise HTTPException(status_code=503, detail="HLS playback requires ffmpeg on the server.")
    if is_complete(media_path) or _has_ready_segment(media_path):
        return manifest_path(media_path)

    with _lock_for(media_path):
        with _state_guard:
            job = _jobs.get(media_path)
            if job is None:
                try:
                    HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    target = cache_dir(media_path)
                    if target.exists():
                        shutil.rmtree(target)
                except OSError as exc:
                    logger.exception("HLS cache directory is not writable: %s", HLS_CACHE_DIR)
                    raise HTTPException(status_code=503, detail="The HLS cache directory is not writable.") from exc
                job = _Job(threading.Event(), threading.Event())
                _jobs[media_path] = job
                threading.Thread(target=_build, args=(media_path, media_url, headers, ffmpeg, job), daemon=True).start()

    deadline = time.monotonic() + HLS_START_TIMEOUT
    while time.monotonic() < deadline:
        if _has_ready_segment(media_path):
            return manifest_path(media_path)
        if job.done.is_set():
            raise HTTPException(status_code=500, detail="The media could not be converted for streaming.")
        time.sleep(0.25)
    raise HTTPException(status_code=504, detail="The media conversion is taking too long to start.")
