#!/usr/bin/env python3
"""Benchmark local HLS encoding candidates without uploading or publishing media."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path


def parse_progress(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def benchmark_one(source: Path, ffmpeg: str, preset: str, segment_duration: int, work_root: Path) -> dict[str, object]:
    output = work_root / f"{preset}-{segment_duration}-{uuid.uuid4().hex}"
    output.mkdir(parents=True, exist_ok=False)
    try:
        manifest = output / "index.m3u8"
        command = [
            ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
            "-c:v", "libx264", "-preset", preset, "-profile:v", "main", "-level:v", "4.0", "-crf", "23",
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-f", "hls", "-hls_time", str(segment_duration),
            "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(output / "segment-%05d.ts"), str(manifest),
        ]
        started = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        elapsed = time.perf_counter() - started
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"FFmpeg exited with status {result.returncode}.")
        segments = sorted(output.glob("segment-*.ts"))
        total_bytes = sum(path.stat().st_size for path in segments)
        progress = parse_progress(result.stdout)
        duration_seconds = None
        if progress.get("out_time_us"):
            duration_seconds = int(progress["out_time_us"]) / 1_000_000
        return {
            "preset": preset,
            "segment_duration_seconds": segment_duration,
            "elapsed_seconds": round(elapsed, 3),
            "media_duration_seconds": duration_seconds,
            "realtime_speed": round(duration_seconds / elapsed, 3) if duration_seconds and elapsed else None,
            "output_bytes": total_bytes,
            "segment_count": len(segments),
            "average_segment_bytes": round(total_bytes / len(segments)) if segments else 0,
        }
    finally:
        shutil.rmtree(output, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--preset", action="append", dest="presets", default=None)
    parser.add_argument("--segment-duration", action="append", type=int, dest="durations", default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path, default=Path.cwd() / ".phase5-benchmark")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"Source does not exist or is not a file: {args.source}")
    presets = args.presets or ["veryfast"]
    durations = args.durations or [6]
    if any(duration <= 0 for duration in durations):
        parser.error("Segment durations must be positive.")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    results = [benchmark_one(args.source, args.ffmpeg, preset, duration, args.work_dir) for preset in presets for duration in durations]
    report = {"source": str(args.source), "results": results}
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
