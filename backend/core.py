"""Application configuration, dependencies, models, and shared services."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import Client, create_client

try:
    import redis.asyncio as redis
except ImportError:
    redis = None


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
ROOM_TTL_SECONDS = int(os.environ.get("WATCH_ROOM_TTL_SECONDS", "86400"))
API_RATE_LIMIT = int(os.environ.get("API_RATE_LIMIT", "120"))
API_RATE_WINDOW_SECONDS = int(os.environ.get("API_RATE_WINDOW_SECONDS", "60"))
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.environ.get("LOGIN_RATE_WINDOW_SECONDS", "60"))
MAX_API_BODY_BYTES = int(os.environ.get("MAX_API_BODY_BYTES", str(2 * 1024 * 1024)))


def parse_allowed_emails(value: str) -> set[str]:
    emails = {email.strip().casefold() for email in value.split(",") if email.strip()}
    if "*" in emails and emails != {"*"}:
        raise RuntimeError("ALLOWED_EMAILS must be either '*' or a comma-separated list of emails, not both.")
    return emails


ALLOWED_EMAILS = parse_allowed_emails(os.environ["ALLOWED_EMAILS"])
ALLOW_ALL_EMAILS = ALLOWED_EMAILS == {"*"}
CORS_ORIGINS = [origin.strip() for origin in os.environ["CORS_ORIGINS"].split(",") if origin.strip()]
if API_RATE_LIMIT <= 0 or API_RATE_WINDOW_SECONDS <= 0:
    raise RuntimeError("API_RATE_LIMIT and API_RATE_WINDOW_SECONDS must be greater than zero.")
if LOGIN_RATE_LIMIT <= 0 or LOGIN_RATE_WINDOW_SECONDS <= 0:
    raise RuntimeError("LOGIN_RATE_LIMIT and LOGIN_RATE_WINDOW_SECONDS must be greater than zero.")
if MAX_API_BODY_BYTES <= 0 or ROOM_TTL_SECONDS <= 0:
    raise RuntimeError("MAX_API_BODY_BYTES and WATCH_ROOM_TTL_SECONDS must be greater than zero.")
if REDIS_URL and redis is None:
    raise RuntimeError("REDIS_URL is configured, but the redis package is not installed.")

redis_client = redis.from_url(REDIS_URL, decode_responses=True) if redis and REDIS_URL else None
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
app = FastAPI(title="adlv Media API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
)


class RateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    async def check(self, key: str, limit: int, window_seconds: int) -> int | None:
        now = int(time.time())
        window = now // window_seconds
        redis_key = f"rate-limit:{key}:{window}"
        if redis_client:
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, window_seconds + 1)
            return window_seconds - (now % window_seconds) if count > limit else None
        async with self._lock:
            previous_window, count = self._windows.get(key, (window, 0))
            if previous_window != window:
                count = 0
            count += 1
            self._windows[key] = (window, count)
            if count > limit:
                return window_seconds - (now % window_seconds)
        return None


rate_limiter = RateLimiter()


def request_client_key(request: Request) -> str:
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")).strip()


def validate_text(value: str, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise HTTPException(status_code=400, detail=f"Invalid {field}.")
    return value


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_email(value: str) -> str:
    value = validate_text(value, "email", 320).strip().casefold()
    if not EMAIL_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid email.")
    return value


def validate_media_path(value: str) -> str:
    value = validate_text(value, "media path", 1024)
    normalized = str(PurePosixPath("/" + value)).lstrip("/")
    if not normalized or normalized.startswith("..") or "\\" in value or any(part in {".", ".."} for part in value.split("/")):
        raise HTTPException(status_code=400, detail="Invalid media path.")
    return normalized


def validate_uuid(value: str, field: str = "identifier") -> str:
    value = validate_text(value, field, 36)
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.") from exc


def rate_limit_bucket(path: str) -> tuple[str, int, int]:
    return ("login", LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS) if path == "/api/auth/login" else ("api", API_RATE_LIMIT, API_RATE_WINDOW_SECONDS)


@app.middleware("http")
async def rate_limit_api_requests(request: Request, call_next: Any) -> Response:
    if request.url.path.startswith("/api/"):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_API_BODY_BYTES:
                    return Response(status_code=413, content="Request body is too large.")
            except ValueError:
                return Response(status_code=400, content="Invalid Content-Length header.")
        if len(request.url.query) > 4096:
            return Response(status_code=400, content="Request query is too long.")
        bucket, limit, window_seconds = rate_limit_bucket(request.url.path)
        retry_after = await rate_limiter.check(f"{bucket}:{request_client_key(request)}", limit, window_seconds)
        if retry_after is not None:
            return Response(content=json.dumps({"detail": "Too many requests; please try again later."}), status_code=429, media_type="application/json", headers={"Retry-After": str(retry_after)})
    return await call_next(request)


@app.middleware("http")
async def prevent_stale_api_responses(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/api/") and not (request.url.path.startswith("/api/media/file/") and response.headers.get("Cache-Control")):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class ProgressRequest(BaseModel):
    media_path: str = Field(min_length=1, max_length=1024)
    position_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    completed: bool = False
    updated_at: str | None = None


class WatchRoomRequest(BaseModel):
    media_path: str = Field(min_length=1, max_length=1024)


class WatchCommand(BaseModel):
    action: Literal["play", "pause", "seek", "rate"]
    position: float | None = Field(default=None, ge=0)
    playback_rate: float | None = Field(default=None, gt=0, le=4)
    command_id: str | None = Field(default=None, max_length=128)


class ProfileRequest(BaseModel):
    subtitles_enabled: bool


class AdminVideoAccessRequest(BaseModel):
    user_access: list[str] = Field(max_length=500)


class AdminNewVideosAccessRequest(BaseModel):
    new_videos_access: bool


class FolderOrderingRequest(BaseModel):
    item_paths: list[str] = Field(max_length=5000)


def reject_unallowed(email: str) -> None:
    normalized_email = validate_email(email)
    if not ALLOW_ALL_EMAILS and normalized_email not in ALLOWED_EMAILS:
        raise HTTPException(status_code=403, detail="This account is not allowed.")


def session_response(session: Any) -> dict[str, Any]:
    auth_session = getattr(session, "session", None) or session
    user = getattr(session, "user", None) or getattr(auth_session, "user", None)
    if not auth_session or not user:
        raise HTTPException(status_code=401, detail="Supabase did not return a valid session.")
    reject_unallowed(user.email or "")
    return {"access_token": auth_session.access_token, "refresh_token": auth_session.refresh_token, "expires_in": auth_session.expires_in, "user": {"id": user.id, "email": user.email}}


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return validate_text(authorization[7:].strip(), "access token", 4096)


def current_token(authorization: str | None = Header(default=None), token: str | None = Query(default=None)) -> str:
    return validate_text(token or bearer_token(authorization), "access token", 4096)


def current_user(token: str = Depends(current_token)) -> Any:
    try:
        user = supabase.auth.get_user(token).user
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    reject_unallowed(user.email or "")
    return user


def supabase_headers(token: str) -> dict[str, str]:
    return {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"}


def supabase_request(method: str, path: str, token: str, **kwargs: Any) -> Any:
    headers = supabase_headers(token)
    headers.update(kwargs.pop("headers", {}))
    response = httpx.request(method, f"{SUPABASE_URL}{path}", headers=headers, timeout=30, **kwargs)
    if response.status_code >= 400:
        detail = f"Supabase request failed ({response.status_code})"
        if response.text.strip():
            detail += f": {response.text.strip().replace(chr(10), ' ')[:500]}"
        raise HTTPException(status_code=502, detail=detail)
    return response.json() if response.content else None


def catalog_rows(token: str, table: str, select: str, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = {"select": select, "order": "path.asc", "limit": 1000, "offset": offset}
        params.update(filters or {})
        page = supabase_request("GET", f"/rest/v1/{table}", token, params=params) or []
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += 1000


def catalog_folder_artworks(video_path: str, image_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    parts = video_path.split("/")
    by_stem = {(row.get("folder_path", ""), row.get("name", "").rsplit(".", 1)[0].casefold()): row for row in image_rows}
    artworks: dict[str, dict[str, str]] = {}
    for index in range(1, len(parts) - 1):
        folder_path = "/".join(parts[:index])
        parent_path = "/".join(parts[:index - 1])
        image = by_stem.get((parent_path, parts[index - 1].casefold()))
        if image:
            artworks[folder_path] = {"path": image["path"], "updatedAt": image.get("updated_at") or ""}
    return artworks


def catalog_videos(token: str, path: str = "") -> list[dict[str, Any]]:
    rows = catalog_rows(token, "videos", "path,name,folder_path,created_at,updated_at,subtitle_path,preview_image_path,preview_image_updated_at")
    image_rows = catalog_rows(token, "media_objects", "path,name,folder_path,updated_at", {"kind": "eq.image"})
    images = {(image.get("folder_path", ""), image.get("name", "").rsplit(".", 1)[0].casefold()): image for image in image_rows}
    prefix = f"{path.rstrip('/')}/" if path else ""
    videos = []
    for row in rows:
        video_path = row.get("path", "")
        if prefix and not video_path.startswith(prefix):
            continue
        image = images.get((row.get("folder_path", ""), row.get("name", "").rsplit(".", 1)[0].casefold()))
        preview_path = image.get("path") if image else row.get("preview_image_path")
        video = {"name": row.get("name", ""), "path": video_path, "uploadedAt": row.get("created_at") or row.get("updated_at") or ""}
        if preview_path:
            video["previewImagePath"] = preview_path
            video["previewImageUpdatedAt"] = (image.get("updated_at") if image else row.get("preview_image_updated_at")) or ""
        artworks = catalog_folder_artworks(video_path, image_rows)
        if artworks:
            video["folderArtworks"] = artworks
        videos.append(video)
    return videos


def stream_videos(token: str, path: str = ""):
    for video in catalog_videos(token, path):
        yield json.dumps(video, separators=(",", ":")) + "\n"


def list_files(token: str, path: str = "") -> list[dict[str, Any]]:
    prefix = f"{path.rstrip('/')}/" if path else ""
    return [{"name": row.get("name", ""), "path": row.get("path", "")} for row in catalog_rows(token, "videos", "path,name") if not prefix or row.get("path", "").startswith(prefix)]


def matching_subtitle(token: str, video_path: str) -> str:
    safe_path = validate_media_path(video_path)
    rows = supabase_request("GET", "/rest/v1/videos", token, params={"select": "subtitle_path", "path": f"eq.{safe_path}", "limit": "1"}) or []
    return (rows[0].get("subtitle_path") or "") if rows else ""


def extract_embedded_subtitle(video_path: str) -> str:
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="Embedded subtitle extraction is unavailable.")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path, "-map", "0:s:0", "-c:s", "webvtt", "-f", "webvtt", "pipe:1"], capture_output=True, timeout=120, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise HTTPException(status_code=404, detail="No embedded subtitles found.")
    return result.stdout.decode("utf-8-sig", errors="replace")


def ensure_video_access(token: str, video_path: str) -> None:
    rows = supabase_request("GET", "/rest/v1/videos", token, params={"select": "path", "path": f"eq.{video_path}", "limit": "1"}) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Video not found.")


def ensure_media_asset_access(token: str, asset_path: str) -> None:
    visible_videos = supabase_request("GET", "/rest/v1/videos", token, params={"select": "path,folder_path,preview_image_path,subtitle_path"}) or []
    if any(asset_path in {row.get("path"), row.get("preview_image_path"), row.get("subtitle_path")} for row in visible_videos):
        return
    visible_paths = {row.get("path") for row in visible_videos}
    preview_rows = supabase_request("GET", "/rest/v1/video_previews", token, params={"select": "media_path,sheets"}) or []
    if any(row.get("media_path") in visible_paths and asset_path in (row.get("sheets") or []) for row in preview_rows):
        return
    object_rows = supabase_request("GET", "/rest/v1/media_objects", token, params={"select": "folder_path,name", "path": f"eq.{asset_path}", "kind": "eq.image", "limit": "1"}) or []
    if object_rows:
        folder_path = object_rows[0].get("folder_path", "")
        stem = object_rows[0].get("name", "").rsplit(".", 1)[0]
        artwork_folder = f"{folder_path}/{stem}" if folder_path else stem
        prefix = f"{artwork_folder}/" if artwork_folder else ""
        if any(row.get("folder_path") == folder_path or (prefix and row.get("path", "").startswith(prefix)) for row in visible_videos):
            return
    raise HTTPException(status_code=404, detail="Media not found.")
