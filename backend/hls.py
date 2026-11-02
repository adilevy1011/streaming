"""On-demand HLS transcoding and cache helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

HLS_CACHE_DIR = Path(os.environ.get("HLS_CACHE_DIR", str(Path(tempfile.gettempdir()) / "adlv-media-hls")))
# Bump when the output compatibility settings change so old caches are not
# reused with a different encoder contract.
CACHE_VERSION = "hls-v3"
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
HLS_TRANSCODE_TIMEOUT = int(os.environ.get("HLS_TRANSCODE_TIMEOUT", "3600"))
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def cache_key(media_path: str) -> str:
    return hashlib.sha256(f"{CACHE_VERSION}:{media_path}".encode("utf-8")).hexdigest()


def cache_dir(media_path: str) -> Path:
    return HLS_CACHE_DIR / cache_key(media_path)


def _lock_for(media_path: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(media_path, threading.Lock())


def manifest_path(media_path: str) -> Path:
    return cache_dir(media_path) / "index.m3u8"


def ffmpeg_binary() -> str | None:
    """Resolve FFmpeg using PATH or an explicit service-friendly path."""
    return shutil.which(FFMPEG_PATH) or (FFMPEG_PATH if Path(FFMPEG_PATH).is_file() else None)


def _cache_is_complete(media_path: str) -> bool:
    """Only reuse a VOD cache when every segment referenced by its playlist exists."""
    manifest = manifest_path(media_path)
    if not manifest.is_file():
        return False
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    segments = [line for line in lines if line and not line.startswith("#")]
    return bool(segments) and all(
        (manifest.parent / line.split("?", 1)[0]).is_file()
        for line in segments
    )


def generate(media_path: str, media_url: str, headers: dict[str, str]) -> Path:
    """Download a source object and create a broadly compatible HLS VOD cache."""
    ffmpeg = ffmpeg_binary()
    logger.info("Starting HLS generation for %s; ffmpeg=%s; cache=%s", media_path, ffmpeg or "not found", HLS_CACHE_DIR)
    if ffmpeg is None:
        raise HTTPException(status_code=503, detail="HLS playback requires ffmpeg on the server.")

    target = cache_dir(media_path)
    existing = manifest_path(media_path)
    if _cache_is_complete(media_path):
        return existing

    with _lock_for(media_path):
        if _cache_is_complete(media_path):
            return existing
        try:
            HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.exception("HLS cache directory is not writable: %s", HLS_CACHE_DIR)
            raise HTTPException(status_code=503, detail="The HLS cache directory is not writable.") from exc
        with tempfile.TemporaryDirectory(prefix="adlv-hls-") as work_dir:
            work = Path(work_dir)
            source = work / "source"
            partial = work / "output"
            partial.mkdir()
            try:
                with httpx.Client(follow_redirects=True, timeout=None) as client:
                    logger.info("Downloading source for HLS: %s", media_path)
                    with client.stream("GET", media_url, headers=headers) as response:
                        if response.status_code >= 400:
                            raise HTTPException(status_code=404, detail="Media not found.")
                        with source.open("wb") as output:
                            for chunk in response.iter_bytes(1024 * 1024):
                                output.write(chunk)
                logger.info("Starting FFmpeg HLS transcode for %s", media_path)
                command = [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a:0?",
                    # Keep the single rendition within limits supported by
                    # older phones and inexpensive browsers. This also makes
                    # very large sources practical on mobile connections.
                    "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
                    "-c:v", "libx264", "-preset", os.environ.get("HLS_PRESET", "veryfast"),
                    # Main/yuv420p is widely hardware-decodable, including on
                    # older iOS devices. Level 4.0 accommodates 1080p sources.
                    "-profile:v", "main", "-level:v", "4.0",
                    "-crf", os.environ.get("HLS_CRF", "23"), "-pix_fmt", "yuv420p",
                    "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
                    "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                    "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "vod",
                    "-hls_flags", "independent_segments",
                    "-hls_segment_filename", str(partial / "segment-%05d.ts"),
                    str(partial / "index.m3u8"),
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=HLS_TRANSCODE_TIMEOUT)
                if result.returncode != 0:
                    logger.error("FFmpeg HLS transcode failed for %s: %s", media_path, (result.stderr or "unknown FFmpeg error").strip()[-4000:])
                    raise HTTPException(status_code=500, detail="The media could not be converted for streaming.")
                if not (partial / "index.m3u8").is_file():
                    raise HTTPException(status_code=500, detail="FFmpeg did not create an HLS manifest.")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(partial, target)
                logger.info("HLS generation completed for %s", media_path)
            except subprocess.TimeoutExpired as exc:
                logger.error("FFmpeg HLS transcode timed out for %s", media_path)
                raise HTTPException(status_code=504, detail="The media conversion timed out.") from exc
            except OSError as exc:
                logger.exception("Unable to run FFmpeg for %s", media_path)
                raise HTTPException(status_code=503, detail="HLS playback is unavailable on the server.") from exc
            except httpx.HTTPError as exc:
                logger.exception("Unable to download media for HLS transcode: %s", media_path)
                raise HTTPException(status_code=502, detail="The source media could not be downloaded.") from exc
    return existing

