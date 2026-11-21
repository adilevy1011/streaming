"""Pure catalog safety rules shared by the API and fixture tests."""

from __future__ import annotations

import re
from typing import Any

HLS_SEGMENT_RE = re.compile(r"(^|/)[^/]+-hls/segment-[0-9]+\.ts$", re.IGNORECASE)
HLS_MANIFEST_RE = re.compile(r"(^|/)[^/]+-hls/index\.m3u8$", re.IGNORECASE)


def is_hls_segment_path(path: str) -> bool:
    return bool(HLS_SEGMENT_RE.search(path or ""))


def is_hls_manifest_path(path: str) -> bool:
    return bool(HLS_MANIFEST_RE.search(path or ""))


def filter_catalog_videos(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove implementation objects and prefer a sibling original."""
    visible = [
        row for row in rows
        if row.get("path") and not is_hls_segment_path(row["path"])
        and (not is_hls_manifest_path(row["path"]) or row["path"].lower().endswith("-hls/index.m3u8"))
    ]
    paths = {row["path"] for row in visible}
    result = []
    for row in visible:
        path = row["path"]
        if is_hls_manifest_path(path):
            parts = path.rsplit("/", 2)
            stem = parts[-2][:-4]
            folder = parts[0] if len(parts) == 3 else ""
            if any(candidate.rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold() == stem.casefold()
                   and candidate.rsplit("/", 1)[0] == folder for candidate in paths if candidate != path):
                continue
            row = {**row, "name": stem, "folder_path": folder}
        result.append(row)
    return result


def filter_cached_media(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("path") and not is_hls_segment_path(row["path"])]


def validate_hls_playlist(text: str, segment_exists=None) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U" or "#EXT-X-ENDLIST" not in lines:
        return False
    segments = [line for line in lines if line.startswith("segment-") and line.endswith(".ts")]
    return bool(segments) and (segment_exists is None or all(segment_exists(name) for name in segments))
