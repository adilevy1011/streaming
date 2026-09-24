#!/usr/bin/env python3
"""Detect end credits from Supabase preview sprite sheets using local vision/OCR."""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import cv2
import easyocr
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
VIDEO_EXTENSIONS = {"mp4", "m4v", "webm", "mov", "mkv", "avi", "ogv", "mpeg", "mpg", "ts"}
CREDIT_KEYWORDS = {
    "directed", "director", "produced", "producer", "written", "writer",
    "cast", "starring", "executive", "music", "edited", "cinematography",
    "screenplay", "stunts", "costume", "animation", "soundtrack", "credits",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv(ROOT / "backend" / ".env")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.environ.get("MEDIA_BUCKET", "media")
SCORE_THRESHOLD = float(os.environ.get("CREDITS_SCORE_THRESHOLD", "0.32"))
MODEL_THRESHOLD = float(os.environ.get("CREDITS_MODEL_THRESHOLD", "0.55"))
MODEL_PATH = os.environ.get("CREDITS_MODEL_PATH", "")
DEBUG_SCORES = os.environ.get("CREDITS_DEBUG", "").lower() in {"1", "true", "yes"}
RED = '\033[31m'
GREEN = '\033[32m'
YELLOW = '\033[33m'
RESET = '\033[0m'

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    raise SystemExit("Missing required env vars: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

HEADERS = {
    "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    "apikey": SERVICE_ROLE_KEY,
    "Content-Type": "application/json",
}
ocr_reader: easyocr.Reader | None = None
credit_model = None
credit_model_transform = None


def api_request(path: str, method: str = "GET", body: bytes | None = None, extra_headers: dict[str, str] | None = None):
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    request = Request(f"{SUPABASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        return urlopen(request, timeout=60)
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} HTTP {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {path}: {error.reason}") from error


def fetch_all_rows(table: str, select: str = "*") -> list[dict]:
    result: list[dict] = []
    offset = 0
    while True:
        with api_request(f"/rest/v1/{table}?select={select}&limit=1000&offset={offset}") as response:
            rows = json.load(response) or []
        result.extend(rows)
        if len(rows) < 1000:
            return result
        offset += 1000


def fetch_preview_manifests() -> list[dict]:
    return fetch_all_rows("video_previews")


def fetch_credit_paths() -> set[str]:
    return {row["media_path"] for row in fetch_all_rows("video_credits", "media_path") if row.get("media_path")}


def list_storage(prefix: str = "") -> list[dict]:
    result: list[dict] = []
    offset = 0
    while True:
        body = json.dumps({
            "prefix": prefix,
            "limit": 1000,
            "offset": offset,
            "sortBy": {"column": "name", "order": "asc"},
        }).encode("utf-8")
        with api_request(
            f"/storage/v1/object/list/{quote(BUCKET, safe='')}", "POST", body
        ) as response:
            items = json.load(response) or []
        result.extend(items)
        if len(items) < 1000:
            return result
        offset += 1000


def list_video_paths(prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for item in list_storage(prefix):
        name = item.get("name", "")
        if not name:
            continue
        path = f"{prefix}/{name}" if prefix else name
        if item.get("id") is None:
            paths.update(list_video_paths(path))
        elif Path(name).suffix.lower().lstrip(".") in VIDEO_EXTENSIONS:
            paths.add(path)
    return paths


def delete_credit_record(media_path: str) -> None:
    with api_request(
        f"/rest/v1/video_credits?media_path=eq.{quote(media_path, safe='')}",
        method="DELETE",
    ):
        pass


def cleanup_stale_credit_records(video_paths: set[str]) -> int:
    stale_paths = sorted(fetch_credit_paths() - video_paths)
    for media_path in stale_paths:
        delete_credit_record(media_path)
        print(f"  [{YELLOW}-{RESET}] Deleted stale credit record: {media_path}")
    return len(stale_paths)


def download_sprite_image(remote_path: str) -> np.ndarray:
    encoded_path = "/".join(quote(part, safe="") for part in remote_path.split("/"))
    path = f"/storage/v1/object/authenticated/{quote(BUCKET, safe='')}/{encoded_path}"
    with api_request(path) as response:
        image_array = np.frombuffer(response.read(), dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode sprite sheet: {remote_path}")
    return image


def get_ocr_reader() -> easyocr.Reader:
    global ocr_reader
    if ocr_reader is None:
        print("[*] Initializing local OCR engine...", flush=True)
        ocr_reader = easyocr.Reader(["en"], gpu=False)
    return ocr_reader


def get_credit_model():
    """Load an optional fine-tuned MobileNet checkpoint once per process."""
    global credit_model, credit_model_transform
    if credit_model is not None or not MODEL_PATH:
        return credit_model, credit_model_transform
    model_path = Path(MODEL_PATH)
    if not model_path.is_absolute():
        model_path = ROOT / model_path
    if not model_path.exists():
        print(f"[!] CREDITS_MODEL_PATH does not exist; using heuristic detector only: {model_path}")
        return None, None
    try:
        import torch
        from torchvision import models, transforms

        model = models.mobilenet_v3_small(weights=None)
        model.classifier[3] = torch.nn.Linear(model.classifier[3].in_features, 1)
        checkpoint = torch.load(model_path, map_location="cpu")
        state_dict = checkpoint.get("state_dict", checkpoint)
        model.load_state_dict(state_dict)
        model.eval()
        credit_model = model
        credit_model_transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        print(f"[*] Loaded trained MobileNet credit verifier: {model_path}")
    except Exception as error:
        print(f"[!] Could not load CREDITS_MODEL_PATH; using heuristic detector only: {error}")
        credit_model = None
        credit_model_transform = None
    return credit_model, credit_model_transform


def model_credit_score(tile: np.ndarray) -> float | None:
    model, transform = get_credit_model()
    if model is None or transform is None:
        return None
    import torch

    rgb_tile = cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)
    tensor = transform(rgb_tile).unsqueeze(0)
    with torch.inference_mode():
        return float(torch.sigmoid(model(tensor)).item())


def tile_from_sheet(image: np.ndarray, tile_index: int, cols: int, rows: int) -> np.ndarray:
    height, width = image.shape[:2]
    row, column = divmod(tile_index, cols)
    left = (column * width) // cols
    right = ((column + 1) * width) // cols
    top = (row * height) // rows
    bottom = ((row + 1) * height) // rows
    return image[top:bottom, left:right]


def score_credit_tile(tile: np.ndarray) -> tuple[float, dict]:
    """Score overlay text, OCR, layout, edges, brightness variation, and dark pixels."""
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    dark_ratio = float(np.mean(gray < 55))
    brightness_std = float(np.std(gray))
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float(np.mean(edges > 0))

    kernel_width = max(5, tile.shape[1] // 8)
    top_hat = cv2.morphologyEx(
        gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 5))
    )
    bright_strokes = (top_hat > 35).astype(np.uint8)
    lower_start = tile.shape[0] // 3
    lower_strokes = bright_strokes[lower_start:]
    row_coverage = np.mean(lower_strokes, axis=1)
    text_rows = int(np.count_nonzero(row_coverage > 0.015))
    bright_text_ratio = float(np.mean(lower_strokes))
    bright_line_signal = min(1.0, text_rows / 8.0)
    bright_area_signal = min(1.0, bright_text_ratio / 0.08)
    bright_text_signal = 0.65 * bright_line_signal + 0.35 * bright_area_signal

    reader = get_ocr_reader()
    ocr_tile = cv2.resize(tile, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    ocr_results = reader.readtext(ocr_tile, detail=1, paragraph=False)
    accepted = [item for item in ocr_results if len(item) >= 3 and float(item[2]) >= 0.25]
    combined_text = " ".join(str(item[1]).lower() for item in accepted)
    matched_keywords = {word for word in CREDIT_KEYWORDS if word in combined_text}

    text_boxes = []
    for item in accepted:
        box = np.asarray(item[0], dtype=np.float32)
        if box.shape == (4, 2):
            text_boxes.append(box)
    row_centers = {
        round(float(np.mean(box[:, 1])) / max(1, ocr_tile.shape[0]) * 10)
        for box in text_boxes
    }

    keyword_signal = min(1.0, len(matched_keywords) / 2.0)
    text_count_signal = min(1.0, len(text_boxes) / 4.0)
    layout_signal = min(1.0, len(row_centers) / 3.0)
    edge_signal = min(1.0, max(0.0, (edge_density - 0.015) / 0.12))
    dark_signal = min(1.0, dark_ratio / 0.80)
    brightness_signal = min(1.0, max(0.0, brightness_std / 70.0))
    score = (
        0.25 * keyword_signal
        + 0.15 * text_count_signal
        + 0.10 * layout_signal
        + 0.10 * edge_signal
        + 0.04 * dark_signal
        + 0.06 * brightness_signal
        + 0.30 * bright_text_signal
    )
    details = {
        "keywords": sorted(matched_keywords),
        "text_boxes": len(text_boxes),
        "dark_ratio": round(dark_ratio, 3),
        "edge_density": round(edge_density, 3),
        "text_rows": text_rows,
        "bright_text_ratio": round(bright_text_ratio, 3),
    }
    model_score = model_credit_score(tile)
    if model_score is not None:
        details["model_score"] = round(model_score, 3)
    return score, details


def detect_credits_in_timeline(
    sheets: list[str], duration: float, interval: float, cols: int, rows: int
) -> tuple[float, float, str] | None:
    """Binary-search the first sustained credit evidence in the final quarter."""
    tiles_per_sheet = cols * rows
    valid_tiles = min(len(sheets) * tiles_per_sheet, max(1, math.ceil(duration / interval)))
    search_start = min(valid_tiles - 1, max(0, math.floor(valid_tiles * 0.75)))
    model_enabled = get_credit_model()[0] is not None
    sheet_cache: dict[int, np.ndarray] = {}
    score_cache: dict[int, tuple[float, dict]] = {}

    def score_at(global_tile: int) -> tuple[float, dict]:
        global_tile = max(0, min(valid_tiles - 1, global_tile))
        if global_tile not in score_cache:
            sheet_index, tile_index = divmod(global_tile, tiles_per_sheet)
            if sheet_index not in sheet_cache:
                sheet_cache[sheet_index] = download_sprite_image(sheets[sheet_index])
            tile = tile_from_sheet(sheet_cache[sheet_index], tile_index, cols, rows)
            score_cache[global_tile] = score_credit_tile(tile)
        return score_cache[global_tile]

    def heuristic_started_at(global_tile: int, threshold: float) -> bool:
        window = [
            score_at(index)[0] >= threshold
            for index in range(global_tile, min(valid_tiles, global_tile + 3))
        ]
        return sum(window) >= max(1, len(window) // 2 + 1)

    def model_started_at(global_tile: int) -> bool:
        window = [
            score_at(index)[1].get("model_score", 0.0) >= MODEL_THRESHOLD
            for index in range(global_tile, min(valid_tiles, global_tile + 3))
        ]
        return sum(window) >= max(1, len(window) // 2 + 1)

    def credit_started_at(global_tile: int, threshold: float) -> bool:
        heuristic_match = heuristic_started_at(global_tile, threshold)
        return heuristic_match and (not model_enabled or model_started_at(global_tile))

    tail_start = max(search_start, valid_tiles - 3)
    tail_probe_start = max(search_start, valid_tiles - 8)
    tail_evidence = [score_at(index) for index in range(tail_probe_start, valid_tiles)]
    tail_scores = [score for score, _ in tail_evidence]
    tail_peak = max(tail_scores, default=0.0)
    effective_threshold = min(SCORE_THRESHOLD, max(0.24, tail_peak * 0.80))
    if DEBUG_SCORES:
        print(
            f"  [debug] tile range {search_start}-{valid_tiles - 1}; "
            f"tail scores {[round(score, 3) for score in tail_scores]}; "
            f"effective threshold {effective_threshold:.3f}; "
            f"tail evidence {[details for _, details in tail_evidence]}"
        )
    joint_tail_match = any(
        credit_started_at(index, effective_threshold)
        for index in range(tail_start, valid_tiles)
    )

    if not joint_tail_match and model_enabled:
        model_tail_match = any(
            model_started_at(index) for index in range(tail_start, valid_tiles)
        )
        if model_tail_match:
            low, high = search_start, valid_tiles - 1
            while low < high:
                midpoint = (low + high) // 2
                if model_started_at(midpoint):
                    high = midpoint
                else:
                    low = midpoint + 1
            model_candidates = [
                index for index in range(max(search_start, low - 3), min(valid_tiles, low + 4))
                if score_at(index)[1].get("model_score", 0.0) >= MODEL_THRESHOLD
            ]
            if model_candidates:
                best_tile = min(model_candidates)
                confidence = score_at(best_tile)[1].get("model_score", MODEL_THRESHOLD)
                return best_tile * interval, confidence, "local-mobilenet-binary-search-fallback"
        return None

    low, high = search_start, valid_tiles - 1
    while low < high:
        midpoint = (low + high) // 2
        if credit_started_at(midpoint, effective_threshold):
            high = midpoint
        else:
            low = midpoint + 1

    candidates = [
        index
        for index in range(max(search_start, low - 3), min(valid_tiles, low + 4))
        if score_at(index)[0] >= effective_threshold
        and (not model_enabled or score_at(index)[1].get("model_score", 0.0) >= MODEL_THRESHOLD)
    ]
    if not candidates:
        return None
    best_tile = min(candidates)
    heuristic_confidence, details = score_at(best_tile)
    model_confidence = details.get("model_score")
    confidence = heuristic_confidence if model_confidence is None else (heuristic_confidence + model_confidence) / 2
    return best_tile * interval, confidence, (
        "local-opencv-easyocr-mobilenet-binary-search"
        if model_enabled else "local-opencv-easyocr-binary-search"
    )


def upsert_credit_record(record: dict) -> None:
    """Insert or update a row and verify PostgREST returned the persisted row."""
    body = json.dumps(record).encode("utf-8")
    with api_request(
        "/rest/v1/video_credits?on_conflict=media_path",
        method="POST",
        body=body,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    ) as response:
        persisted = json.load(response)
    rows = persisted if isinstance(persisted, list) else [persisted]
    if not rows or rows[0].get("media_path") != record["media_path"]:
        raise RuntimeError(
            f"Supabase did not return the expected video_credits row for {record['media_path']}"
        )


def process_video(preview: dict) -> None:
    media_path = preview.get("media_path")
    sheets = preview.get("sheets") or []
    try:
        duration = float(preview.get("duration_seconds", 0))
        interval = float(preview.get("interval_seconds", 5))
        cols = int(preview.get("columns", 10))
        rows = int(preview.get("rows", 10))
    except (TypeError, ValueError) as error:
        print(f"[{RED}-{RESET}] Skipping {media_path}: invalid preview metadata ({error}).")
        return

    if not media_path or not isinstance(sheets, list) or not sheets:
        print(f"[{RED}-{RESET}] Skipping {media_path}: no sprite sheets found.")
        return
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(interval) or interval <= 0:
        print(f"[{RED}-{RESET}] Skipping {media_path}: invalid duration or interval.")
        return
    if cols <= 0 or rows <= 0:
        print(f"[{RED}-{RESET}] Skipping {media_path}: invalid sprite dimensions.")
        return

    print(f"[*] Analyzing '{media_path}'")
    try:
        result = detect_credits_in_timeline(sheets, duration, interval, cols, rows)
        if result is None:
            print(f"  [{RED}-{RESET}] No credits detected for '{media_path}'.")
            return
        offset, confidence, detected_via = result
        credits_start = round(min(offset, duration), 2)
        upsert_credit_record({
            "media_path": media_path,
            "credits_start_seconds": credits_start,
            "credits_end_seconds": round(duration, 2),
            "confidence_score": round(confidence, 3),
            "detected_via": detected_via,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  [{GREEN}+{RESET}] SUCCESS: credits detected at {credits_start}s (Conf: {confidence:.2f})")
    except Exception as error:
        print(f"  [{RED}!{RESET}] Error scanning '{media_path}': {error}", file=sys.stderr)


def main() -> int:
    print("[*] Loading preview manifests and reconciling existing credit rows...")
    model, _ = get_credit_model()
    if model is None:
        print("[*] MobileNet verifier: disabled; using OpenCV/EasyOCR only.")
    else:
        print(f"[*] MobileNet verifier: enabled (threshold={MODEL_THRESHOLD:.2f}).")
    try:
        manifests = fetch_preview_manifests()
        video_paths = list_video_paths()
        cleanup_stale_credit_records(video_paths)
        existing_credit_paths = fetch_credit_paths() & video_paths
    except Exception as error:
        print(f"[{RED}!{RESET}] Failed to reconcile Supabase state: {error}", file=sys.stderr)
        return 1

    print(
        f"[*] {len(manifests)} preview(s), {len(existing_credit_paths)} existing credit row(s), "
        "rechecking all preview(s) with both configured detectors."
    )
    for preview in manifests:
        process_video(preview)
    print("[*] Credit detection run finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
