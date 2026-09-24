import os
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from supabase import Client, create_client


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
ALLOWED_EMAILS = {
    email.strip().lower()
    for email in os.environ["ALLOWED_EMAILS"].split(",")
    if email.strip()
}
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
CORS_ORIGINS = [origin.strip() for origin in os.environ["CORS_ORIGINS"].split(",") if origin.strip()]
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
app = FastAPI(title="adlv Media API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Range"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
)


@app.middleware("http")
async def prevent_stale_api_responses(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        if not (request.url.path.startswith("/api/media/file/") and response.headers.get("Cache-Control")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
    return response


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ProgressRequest(BaseModel):
    media_path: str = Field(min_length=1)
    position_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    completed: bool = False
    updated_at: str | None = None


class ProfileRequest(BaseModel):
    subtitles_enabled: bool


def reject_unallowed(email: str) -> None:
    if email.strip().lower() not in ALLOWED_EMAILS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed.")


def session_response(session: Any) -> dict[str, Any]:
    auth_session = getattr(session, "session", None) or session
    user = getattr(session, "user", None) or getattr(auth_session, "user", None)
    if not auth_session or not user:
        raise HTTPException(status_code=401, detail="Supabase did not return a valid session.")
    reject_unallowed(user.email or "")
    return {
        "access_token": auth_session.access_token,
        "refresh_token": auth_session.refresh_token,
        "expires_in": auth_session.expires_in,
        "user": {"id": user.id, "email": user.email},
    }


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return authorization[7:].strip()


def current_token(authorization: str | None = Header(default=None), token: str | None = Query(default=None)) -> str:
    token = token or bearer_token(authorization)
    return token


def current_user(token: str = Depends(current_token)) -> Any:
    try:
        user_response = supabase.auth.get_user(token)
        user = user_response.user
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    reject_unallowed(user.email or "")
    return user


def supabase_headers(token: str) -> dict[str, str]:
    return {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"}


def supabase_request(method: str, path: str, token: str, **kwargs: Any) -> Any:
    request_headers = supabase_headers(token)
    request_headers.update(kwargs.pop("headers", {}))
    response = httpx.request(method, f"{SUPABASE_URL}{path}", headers=request_headers, timeout=30, **kwargs)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Supabase request failed ({response.status_code}).")
    return response.json() if response.content else None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    try:
        session = supabase.auth.sign_in_with_password({"email": payload.email, "password": payload.password})
        return session_response(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid email or password.") from exc


@app.post("/api/auth/refresh")
def refresh(payload: RefreshRequest) -> dict[str, Any]:
    try:
        session = supabase.auth.refresh_session(payload.refresh_token)
        return session_response(session)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Unable to refresh session.") from exc


@app.get("/api/auth/session")
def session(user: Any = Depends(current_user)) -> dict[str, Any]:
    return {"user": {"id": user.id, "email": user.email}}


@app.get("/api/profile")
def profile(user: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, Any]:
    params = {
        "select": "user_id,subtitles_enabled",
        "user_id": f"eq.{user.id}",
        "limit": "1",
    }
    data = supabase_request("GET", "/rest/v1/profiles", token, params=params) or []
    if data:
        return data[0]

    row = {"user_id": user.id, "subtitles_enabled": False}
    created = supabase_request(
        "POST",
        "/rest/v1/profiles",
        token,
        headers={"Prefer": "return=representation"},
        json=row,
    ) or []
    return created[0] if created else row


@app.patch("/api/profile")
def update_profile(payload: ProfileRequest, user: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, Any]:
    row = {"user_id": user.id, "subtitles_enabled": payload.subtitles_enabled}
    data = supabase_request(
        "POST",
        "/rest/v1/profiles",
        token,
        params={"on_conflict": "user_id"},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json=row,
    ) or []
    return data[0] if data else row


def catalog_rows(token: str, table: str, select: str, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000
    while True:
        params = {
            "select": select,
            "order": "path.asc",
            "limit": page_size,
            "offset": offset,
        }
        params.update(filters or {})
        page = supabase_request(
            "GET",
            f"/rest/v1/{table}",
            token,
            params=params,
        ) or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def catalog_folder_artworks(video_path: str, image_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    parts = video_path.split("/")
    artworks: dict[str, dict[str, str]] = {}
    by_stem = {
        (row.get("folder_path", ""), row.get("name", "").rsplit(".", 1)[0].casefold()): row
        for row in image_rows
    }
    for index in range(1, len(parts) - 1):
        folder_path = "/".join(parts[:index])
        parent_path = "/".join(parts[:index - 1])
        folder_name = parts[index - 1]
        image = by_stem.get((parent_path, folder_name.casefold()))
        if image:
            artworks[folder_path] = {
                "path": image["path"],
                "updatedAt": image.get("updated_at") or "",
            }
    return artworks


def catalog_videos(token: str, path: str = "") -> list[dict[str, Any]]:
    rows = catalog_rows(
        token,
        "videos",
        "path,name,folder_path,created_at,updated_at,subtitle_path,preview_image_path,preview_image_updated_at",
    )
    image_rows = catalog_rows(
        token,
        "media_objects",
        "path,name,folder_path,updated_at",
        {"kind": "eq.image"},
    )
    prefix = f"{path.rstrip('/')}/" if path else ""
    videos = []
    for row in rows:
        video_path = row.get("path", "")
        if prefix and not video_path.startswith(prefix):
            continue
        video = {
            "name": row.get("name", ""),
            "path": video_path,
            "uploadedAt": row.get("created_at") or row.get("updated_at") or "",
        }
        if row.get("preview_image_path"):
            video["previewImagePath"] = row["preview_image_path"]
            video["previewImageUpdatedAt"] = row.get("preview_image_updated_at") or ""
        folder_artworks = catalog_folder_artworks(video_path, image_rows)
        if folder_artworks:
            video["folderArtworks"] = folder_artworks
        videos.append(video)
    return videos


def stream_videos(token: str, path: str = ""):
    for video in catalog_videos(token, path):
        yield json.dumps(video, separators=(",", ":")) + "\n"


def list_files(token: str, path: str = "") -> list[dict[str, Any]]:
    prefix = f"{path.rstrip('/')}/" if path else ""
    return [
        {"name": row.get("name", ""), "path": row.get("path", "")}
        for row in catalog_rows(token, "videos", "path,name")
        if not prefix or row.get("path", "").startswith(prefix)
    ]


def matching_subtitle(token: str, video_path: str) -> str:
    safe_path = str(PurePosixPath("/" + video_path)).lstrip("/")
    if not safe_path or safe_path.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid media path.")

    rows = supabase_request(
        "GET",
        "/rest/v1/videos",
        token,
        params={"select": "subtitle_path", "path": f"eq.{safe_path}", "limit": "1"},
    ) or []
    return rows[0].get("subtitle_path", "") if rows else ""


@app.get("/api/media/stream")
def media_stream(path: str = Query(default=""), _: Any = Depends(current_user), token: str = Depends(current_token)) -> StreamingResponse:
    safe_path = str(PurePosixPath("/" + path)).lstrip("/")
    if path and (not safe_path or safe_path.startswith("..")):
        raise HTTPException(status_code=400, detail="Invalid media path.")
    return StreamingResponse(stream_videos(token, safe_path), media_type="application/x-ndjson")


@app.get("/api/media/files")
def files(_: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    return list_files(token)


@app.get("/api/media/subtitle")
def subtitle(video_path: str = Query(..., min_length=1), _: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, str]:
    return {"path": matching_subtitle(token, video_path)}


@app.get("/api/media/credits")
def credits(video_path: str = Query(..., min_length=1), _: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, Any]:
    data = supabase_request(
        "GET",
        "/rest/v1/video_credits",
        token,
        params={
            "select": "media_path,credits_start_seconds,credits_end_seconds",
            "media_path": f"eq.{video_path}",
            "limit": "1",
        },
    ) or []
    if not data:
        return {"media_path": video_path, "credits_start_seconds": None, "credits_end_seconds": None}
    return data[0]


@app.get("/api/previews")
def previews(_: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    select = "media_path,sheets,duration_seconds,interval_seconds,columns,rows,thumbnail_width,thumbnail_height"
    return supabase_request("GET", f"/rest/v1/video_previews?select={select}", token) or []


@app.get("/api/progress")
def progress(user: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    params = {"select": "media_path,position_seconds,duration_seconds,completed,updated_at", "user_id": f"eq.{user.id}", "order": "updated_at.desc"}
    return supabase_request("GET", "/rest/v1/video_progress", token, params=params) or []


@app.post("/api/progress")
def save_progress(payload: ProgressRequest, user: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, Any]:
    row = payload.model_dump(exclude_none=True)
    row["user_id"] = user.id
    data = supabase_request(
        "POST",
        "/rest/v1/video_progress",
        token,
        params={"on_conflict": "user_id,media_path"},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json=row,
    )
    return (data or [row])[0]


async def upstream_stream(url: str, headers: dict[str, str]) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", url, headers=headers) as upstream:
            if upstream.status_code >= 400:
                return
            async for chunk in upstream.aiter_bytes(1024 * 1024):
                yield chunk


@app.get("/api/media/file/{media_path:path}")
async def media_file(media_path: str, request: Request, _: Any = Depends(current_user), token: str = Depends(current_token)) -> Response:
    safe_path = str(PurePosixPath("/" + media_path)).lstrip("/")
    if not safe_path or safe_path.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid media path.")
    media_url = f"{SUPABASE_URL}/storage/v1/object/{MEDIA_BUCKET}/{quote(safe_path, safe='/')}"
    range_header = request.headers.get("range")
    headers = supabase_headers(token)
    if range_header:
        headers["Range"] = range_header
    client = httpx.AsyncClient(follow_redirects=True, timeout=None)
    upstream = await client.send(client.build_request("GET", media_url, headers=headers), stream=True)
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=404, detail="Media not found.")
    response_headers = {key: value for key, value in upstream.headers.items() if key.lower() in {"content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"}}
    if request.query_params.get("cache") == "preview":
        response_headers["Cache-Control"] = "private, max-age=86400"

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes(1024 * 1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/watch", include_in_schema=False)
def frontend_watch() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "watch.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")
