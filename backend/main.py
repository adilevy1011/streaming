import os
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


def is_video(name: str, metadata: dict[str, Any] | None = None) -> bool:
    extensions = {"mp4", "m4v", "webm", "mov", "mkv", "avi", "ogv", "mpeg", "mpg", "ts"}
    return bool((metadata or {}).get("mimetype", "").startswith("video/")) or name.rsplit(".", 1)[-1].lower() in extensions


def storage_list(path: str, token: str, limit: int, offset: int) -> list[dict[str, Any]]:
    return supabase_request(
        "POST",
        f"/storage/v1/object/list/{MEDIA_BUCKET}",
        token,
        json={"prefix": path, "limit": limit, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
    ) or []


def list_videos(token: str, path: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000
    while True:
        items = storage_list(path, token, page_size, offset)
        for item in items or []:
            name = item.get("name", "")
            if name == ".emptyFolderPlaceholder":
                continue
            item_path = f"{path}/{name}" if path else name
            if item.get("id") is None:
                results.extend(list_videos(token, item_path))
            elif is_video(name, item.get("metadata")):
                results.append({"name": name, "path": item_path, "uploadedAt": item.get("created_at") or item.get("updated_at")})
        if len(items or []) < page_size:
            break
        offset += page_size
    return results


def list_files(token: str, path: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000
    while True:
        items = storage_list(path, token, page_size, offset)
        for item in items or []:
            name = item.get("name", "")
            if name == ".emptyFolderPlaceholder":
                continue
            item_path = f"{path}/{name}" if path else name
            if item.get("id") is None:
                results.extend(list_files(token, item_path))
            else:
                results.append({"name": name, "path": item_path})
        if len(items or []) < page_size:
            break
        offset += page_size
    return results


def matching_subtitle(token: str, video_path: str) -> str:
    safe_path = str(PurePosixPath("/" + video_path)).lstrip("/")
    if not safe_path or safe_path.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid media path.")

    video_name = PurePosixPath(safe_path).name
    video_stem = video_name.rsplit(".", 1)[0].lower()
    directory = str(PurePosixPath(safe_path).parent)
    if directory == ".":
        directory = ""

    # Only inspect the video's own directory instead of recursively listing the
    # whole bucket. This keeps subtitle discovery fast for large libraries.
    offset = 0
    page_size = 1000
    while True:
        items = storage_list(directory, token, page_size, offset)
        for item in items:
            name = item.get("name", "")
            if item.get("id") is not None and name.lower() == f"{video_stem}.srt":
                return f"{directory}/{name}" if directory else name
        if len(items) < page_size:
            break
        offset += page_size
    return ""


@app.get("/api/media")
def media(_: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    return list_videos(token)


@app.get("/api/media/files")
def files(_: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    return list_files(token)


@app.get("/api/media/subtitle")
def subtitle(video_path: str = Query(..., min_length=1), _: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, str]:
    return {"path": matching_subtitle(token, video_path)}


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
