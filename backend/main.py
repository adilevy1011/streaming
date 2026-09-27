import os
import json
import shutil
import subprocess
import tempfile
import time
import uuid
import asyncio
from collections import deque
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from supabase import Client, create_client
from starlette.concurrency import run_in_threadpool
try:
    import redis.asyncio as redis
except ImportError: 
    redis = None


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


def parse_allowed_emails(value: str) -> set[str]:
    emails = {email.strip().casefold() for email in value.split(",") if email.strip()}
    if "*" in emails and emails != {"*"}:
        raise RuntimeError("ALLOWED_EMAILS must be either '*' or a comma-separated list of emails, not both.")
    return emails


ALLOWED_EMAILS = parse_allowed_emails(os.environ["ALLOWED_EMAILS"])
ALLOW_ALL_EMAILS = ALLOWED_EMAILS == {"*"}
MEDIA_BUCKET = os.environ["MEDIA_BUCKET"]
CORS_ORIGINS = [origin.strip() for origin in os.environ["CORS_ORIGINS"].split(",") if origin.strip()]
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
ROOM_TTL_SECONDS = int(os.environ.get("WATCH_ROOM_TTL_SECONDS", "86400"))
if ROOM_TTL_SECONDS <= 0:
    raise RuntimeError("WATCH_ROOM_TTL_SECONDS must be greater than zero.")
if REDIS_URL and redis is None:
    raise RuntimeError("REDIS_URL is configured, but the redis package is not installed.")

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


class WatchRoomRequest(BaseModel):
    media_path: str = Field(min_length=1)


class WatchCommand(BaseModel):
    action: Literal["play", "pause", "seek", "rate"]
    position: float | None = Field(default=None, ge=0)
    playback_rate: float | None = Field(default=None, gt=0, le=4)
    command_id: str | None = Field(default=None, max_length=128)


class ProfileRequest(BaseModel):
    subtitles_enabled: bool


class AdminVideoAccessRequest(BaseModel):
    user_access: list[str]


class AdminNewVideosAccessRequest(BaseModel):
    new_videos_access: bool


def reject_unallowed(email: str) -> None:
    normalized_email = email.strip().casefold()
    if not normalized_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed.")
    if ALLOW_ALL_EMAILS:
        return
    if normalized_email not in ALLOWED_EMAILS:
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
        upstream_detail = response.text.strip().replace("\n", " ")[:500]
        detail = f"Supabase request failed ({response.status_code})"
        if upstream_detail:
            detail += f": {upstream_detail}"
        raise HTTPException(status_code=502, detail=detail)
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
        "select": "user_id,subtitles_enabled,admin_access,new_videos_access",
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
    return created[0] if created else {**row, "admin_access": False, "new_videos_access": True}


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


def admin_token(user: Any = Depends(current_user), token: str = Depends(current_token)) -> str:
    data = supabase_request(
        "GET",
        "/rest/v1/profiles",
        token,
        params={"select": "admin_access", "user_id": f"eq.{user.id}", "limit": "1"},
    ) or []
    if not data or not data[0].get("admin_access"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return token


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
    # A video without a sibling .srt has a NULL subtitle_path in the catalog.
    # Normalize it so the API remains a successful empty lookup.
    return (rows[0].get("subtitle_path") or "") if rows else ""


def extract_embedded_subtitle(video_path: str) -> str:
    """Extract the first subtitle stream from a local video as WebVTT."""
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="Embedded subtitle extraction is unavailable.")

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-map", "0:s:0",
            "-c:s", "webvtt",
            "-f", "webvtt", "pipe:1",
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise HTTPException(status_code=404, detail="No embedded subtitles found.")
    return result.stdout.decode("utf-8-sig", errors="replace")


def ensure_video_access(token: str, video_path: str) -> None:
    rows = supabase_request(
        "GET",
        "/rest/v1/videos",
        token,
        params={"select": "path", "path": f"eq.{video_path}", "limit": "1"},
    ) or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")


def ensure_media_asset_access(token: str, asset_path: str) -> None:
    visible_videos = supabase_request(
        "GET",
        "/rest/v1/videos",
        token,
        params={"select": "path,folder_path,preview_image_path,subtitle_path"},
    ) or []
    if any(asset_path in {row.get("path"), row.get("preview_image_path"), row.get("subtitle_path")} for row in visible_videos):
        return

    visible_paths = {row.get("path") for row in visible_videos}
    preview_rows = supabase_request(
        "GET",
        "/rest/v1/video_previews",
        token,
        params={"select": "media_path,sheets"},
    ) or []
    for row in preview_rows:
        if row.get("media_path") in visible_paths and asset_path in (row.get("sheets") or []):
            return

    object_rows = supabase_request(
        "GET",
        "/rest/v1/media_objects",
        token,
        params={"select": "folder_path,name", "path": f"eq.{asset_path}", "kind": "eq.image", "limit": "1"},
    ) or []
    if object_rows:
        folder_path = object_rows[0].get("folder_path", "")
        image_stem = object_rows[0].get("name", "").rsplit(".", 1)[0]
        artwork_folder = f"{folder_path}/{image_stem}" if folder_path else image_stem
        folder_prefix = f"{artwork_folder}/" if artwork_folder else ""
        if any(
            row.get("folder_path") == folder_path
            or (folder_prefix and row.get("path", "").startswith(folder_prefix))
            for row in visible_videos
        ):
            return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found.")

ROOM_STATE_PREFIX = "watch:room:"
ROOM_CHANNEL_PREFIX = "watch:room:events:"
ROOM_META_PREFIX = "watch:room:meta:"
ROOM_BANS_PREFIX = "watch:room:bans:"
ROOM_PARTICIPANTS_PREFIX = "watch:room:participants:"
ROOM_START_PREFIX = "watch:room:started:"
ROOM_STATE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local state = cjson.decode(raw)
local now = tonumber(ARGV[3])
local position = tonumber(state.position) or 0
if state.playing then
  position = position + math.max(0, now - tonumber(state.server_time_ms or now)) / 1000 * tonumber(state.playback_rate or 1)
end
local action = ARGV[1]
if action == 'seek' then
  position = tonumber(ARGV[2])
elseif action == 'play' then
  state.playing = true
  state.started = true
elseif action == 'pause' then
  state.playing = false
elseif action == 'rate' then
  state.playback_rate = tonumber(ARGV[7])
else
  return false
end
state.position = math.max(0, position)
state.server_time_ms = now
state.revision = tonumber(state.revision or 0) + 1
state.action = action
state.actor_id = ARGV[4]
state.command_id = ARGV[5]
local encoded = cjson.encode(state)
redis.call('SET', KEYS[1], encoded, 'EX', ARGV[6])
redis.call('PUBLISH', KEYS[2], encoded)
return encoded
"""

room_connections: dict[str, set[WebSocket]] = {}
room_states: dict[str, dict[str, Any]] = {}
room_locks: dict[str, asyncio.Lock] = {}
room_meta: dict[str, dict[str, Any]] = {}
room_bans: dict[str, set[str]] = {}
room_participants: dict[str, dict[str, dict[str, Any]]] = {}
room_socket_info: dict[WebSocket, dict[str, str]] = {}
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if redis and REDIS_URL else None


@app.on_event("startup")
async def verify_watch_room_store() -> None:
    if redis_client:
        await redis_client.ping()


@app.on_event("shutdown")
async def close_watch_room_store() -> None:
    if redis_client:
        await redis_client.aclose()


def room_lock(room_id: str) -> asyncio.Lock:
    return room_locks.setdefault(room_id, asyncio.Lock())


def initial_room_state(room_id: str, media_path: str) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "type": "state", "room_id": room_id, "media_path": media_path,
        "revision": 0, "action": "snapshot", "position": 0,
        "playing": False, "playback_rate": 1, "server_time_ms": now,
        "started": False,
        "expires_at_ms": now + ROOM_TTL_SECONDS * 1000,
        "actor_id": None, "command_id": None,
    }


async def get_room_state(room_id: str) -> dict[str, Any] | None:
    if redis_client:
        raw = await redis_client.get(f"{ROOM_STATE_PREFIX}{room_id}")
        return json.loads(raw) if raw else None
    state = room_states.get(room_id)
    if state and state.get("expires_at_ms", 0) <= int(time.time() * 1000):
        room_states.pop(room_id, None)
        room_meta.pop(room_id, None)
        room_bans.pop(room_id, None)
        room_participants.pop(room_id, None)
        return None
    return state


async def set_room_state(state: dict[str, Any]) -> None:
    if redis_client:
        await redis_client.set(f"{ROOM_STATE_PREFIX}{state['room_id']}", json.dumps(state), ex=ROOM_TTL_SECONDS)
    else:
        state["expires_at_ms"] = int(time.time() * 1000) + ROOM_TTL_SECONDS * 1000
        room_states[state["room_id"]] = state


async def get_room_meta(room_id: str) -> dict[str, Any] | None:
    if redis_client:
        raw = await redis_client.get(f"{ROOM_META_PREFIX}{room_id}")
        return json.loads(raw) if raw else None
    return room_meta.get(room_id)


async def set_room_meta(room_id: str, meta: dict[str, Any]) -> None:
    if redis_client:
        await redis_client.set(f"{ROOM_META_PREFIX}{room_id}", json.dumps(meta), ex=ROOM_TTL_SECONDS)
    else:
        room_meta[room_id] = meta


async def refresh_room_auxiliary_ttl(room_id: str) -> None:
    if not redis_client:
        return
    await redis_client.expire(f"{ROOM_META_PREFIX}{room_id}", ROOM_TTL_SECONDS)
    await redis_client.expire(f"{ROOM_BANS_PREFIX}{room_id}", ROOM_TTL_SECONDS)
    await redis_client.expire(f"{ROOM_PARTICIPANTS_PREFIX}{room_id}", ROOM_TTL_SECONDS)


async def is_room_banned(room_id: str, user_id: str) -> bool:
    if redis_client:
        return bool(await redis_client.sismember(f"{ROOM_BANS_PREFIX}{room_id}", user_id))
    return user_id in room_bans.get(room_id, set())


async def ban_room_user(room_id: str, user_id: str) -> None:
    if redis_client:
        key = f"{ROOM_BANS_PREFIX}{room_id}"
        await redis_client.sadd(key, user_id)
        await redis_client.expire(key, ROOM_TTL_SECONDS)
    else:
        room_bans.setdefault(room_id, set()).add(user_id)


def participant_public_view(participant_id: str, participant: dict[str, Any]) -> dict[str, Any]:
    return {
        "participant_id": participant_id,
        "email": participant.get("email", ""),
        "ready": bool(participant.get("ready", False)),
    }


async def get_room_participants(room_id: str) -> dict[str, dict[str, Any]]:
    if redis_client:
        values = await redis_client.hgetall(f"{ROOM_PARTICIPANTS_PREFIX}{room_id}")
        return {participant_id: json.loads(value) for participant_id, value in values.items()}
    return dict(room_participants.get(room_id, {}))


async def add_room_participant(room_id: str, participant_id: str, user: Any) -> None:
    participant = {"user_id": user.id, "email": user.email or "", "ready": False}
    if redis_client:
        key = f"{ROOM_PARTICIPANTS_PREFIX}{room_id}"
        await redis_client.hset(key, participant_id, json.dumps(participant))
        await redis_client.expire(key, ROOM_TTL_SECONDS)
    else:
        room_participants.setdefault(room_id, {})[participant_id] = participant


async def remove_room_participant(room_id: str, participant_id: str) -> None:
    if redis_client:
        await redis_client.hdel(f"{ROOM_PARTICIPANTS_PREFIX}{room_id}", participant_id)
    else:
        room_participants.get(room_id, {}).pop(participant_id, None)


async def publish_room(room_id: str, payload: dict[str, Any]) -> None:
    if redis_client:
        await redis_client.publish(f"{ROOM_CHANNEL_PREFIX}{room_id}", json.dumps(payload))
        return
    for websocket in list(room_connections.get(room_id, set())):
        try:
            info = room_socket_info.get(websocket, {})
            if payload.get("type") == "kick" and (
                    payload.get("participant_id") != info.get("participant_id")
                    and payload.get("target_user_id") != info.get("user_id")):
                continue
            outgoing = dict(payload)
            outgoing.pop("target_user_id", None)
            if outgoing.get("type") == "kick":
                await websocket.send_json({"type": "kicked"})
                await websocket.close(code=4406)
                continue
            await websocket.send_json(outgoing)
        except Exception:
            room_connections.get(room_id, set()).discard(websocket)


async def publish_participants(room_id: str) -> None:
    participants = await get_room_participants(room_id)
    await publish_room(room_id, {
        "type": "participants",
        "participants": [participant_public_view(pid, value) for pid, value in participants.items()],
    })


async def set_participant_ready(room_id: str, participant_id: str) -> bool:
    participants = await get_room_participants(room_id)
    participant = participants.get(participant_id)
    if not participant:
        return False
    participant["ready"] = True
    if redis_client:
        key = f"{ROOM_PARTICIPANTS_PREFIX}{room_id}"
        await redis_client.hset(key, participant_id, json.dumps(participant))
        await redis_client.expire(key, ROOM_TTL_SECONDS)
    else:
        room_participants.setdefault(room_id, {})[participant_id] = participant
    return True


async def maybe_start_room(room_id: str) -> dict[str, Any] | None:
    state = await get_room_state(room_id)
    participants = await get_room_participants(room_id)
    if not state or state.get("started") or not participants or not all(p.get("ready") for p in participants.values()):
        return None
    if redis_client:
        claimed = await redis_client.set(f"{ROOM_START_PREFIX}{room_id}", "1", nx=True, ex=ROOM_TTL_SECONDS)
        if not claimed:
            return None
    else:
        async with room_lock(room_id):
            state = await get_room_state(room_id)
            if not state or state.get("started"):
                return None
    return await apply_room_command(room_id, WatchCommand(action="play", command_id="room-ready"), "system")


async def apply_room_command(room_id: str, command: WatchCommand, user_id: str) -> dict[str, Any] | None:
    now = int(time.time() * 1000)
    if redis_client:
        if command.action not in {"play", "pause", "seek", "rate"}:
            return None
        encoded = await redis_client.eval(
            ROOM_STATE_SCRIPT, 2,
            f"{ROOM_STATE_PREFIX}{room_id}", f"{ROOM_CHANNEL_PREFIX}{room_id}",
            command.action, str(command.position if command.position is not None else 0),
            str(now), user_id, command.command_id or str(uuid.uuid4()), str(ROOM_TTL_SECONDS),
            str(command.playback_rate if command.playback_rate is not None else 1),
        )
        await refresh_room_auxiliary_ttl(room_id)
        return json.loads(encoded) if encoded else None

    async with room_lock(room_id):
        state = await get_room_state(room_id)
        if not state or command.action not in {"play", "pause", "seek", "rate"}:
            return None
        if state["playing"]:
            state["position"] += max(0, now - state["server_time_ms"]) / 1000 * state.get("playback_rate", 1)
        if command.action == "seek":
            state["position"] = command.position or 0
        elif command.action == "play":
            state["playing"] = True
        elif command.action == "pause":
            state["playing"] = False
        else:
            if command.playback_rate is None:
                return None
            state["playback_rate"] = command.playback_rate
        state.update({"revision": state["revision"] + 1, "action": command.action,
                      "server_time_ms": now, "actor_id": user_id,
                      "command_id": command.command_id or str(uuid.uuid4())})
        await set_room_state(state)
        await publish_room(room_id, state)
        return state


async def redis_room_listener(
    room_id: str,
    websocket: WebSocket,
    ready: asyncio.Event,
    participant_id: str,
    user_id: str,
) -> None:
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(f"{ROOM_CHANNEL_PREFIX}{room_id}")
        ready.set()
        async for message in pubsub.listen():
            if message.get("type") == "message":
                payload = json.loads(message["data"])
                if payload.get("type") == "participants":
                    pass
                elif payload.get("type") == "kick":
                    if (payload.get("participant_id") != participant_id
                            and payload.get("target_user_id") != user_id):
                        continue
                    await websocket.send_json({"type": "kicked"})
                    await websocket.close(code=4406)
                    return
                await websocket.send_json(payload)
    except asyncio.CancelledError:
        raise
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        ready.set()
        await pubsub.unsubscribe(f"{ROOM_CHANNEL_PREFIX}{room_id}")
        await pubsub.close()


async def room_heartbeat(websocket: WebSocket) -> None:
    while True:
        await asyncio.sleep(20)
        await websocket.send_json({"type": "ping", "server_time_ms": int(time.time() * 1000)})


@app.get("/api/media/stream")
def media_stream(path: str = Query(default=""), _: Any = Depends(current_user), token: str = Depends(current_token)) -> StreamingResponse:
    safe_path = str(PurePosixPath("/" + path)).lstrip("/")
    if path and (not safe_path or safe_path.startswith("..")):
        raise HTTPException(status_code=400, detail="Invalid media path.")
    return StreamingResponse(stream_videos(token, safe_path), media_type="application/x-ndjson")


@app.get("/api/media/files")
def files(_: Any = Depends(current_user), token: str = Depends(current_token)) -> list[dict[str, Any]]:
    return list_files(token)


@app.post("/api/watch/rooms")
async def create_watch_room(
    payload: WatchRoomRequest,
    user: Any = Depends(current_user),
    token: str = Depends(current_token),
) -> dict[str, Any]:
    await run_in_threadpool(ensure_video_access, token, payload.media_path)
    room_id = str(uuid.uuid4())
    state = initial_room_state(room_id, payload.media_path)
    await set_room_state(state)
    await set_room_meta(room_id, {"owner_id": user.id, "media_path": payload.media_path})
    return {"room_id": room_id, "media_path": payload.media_path}


@app.websocket("/api/watch/rooms/{room_id}")
async def watch_room(websocket: WebSocket, room_id: str) -> None:
    try:
        uuid.UUID(room_id)
    except ValueError:
        await websocket.close(code=4404)
        return
    origin = websocket.headers.get("origin")
    if origin and "*" not in CORS_ORIGINS and origin not in CORS_ORIGINS:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    listener = None
    heartbeat = None
    try:
        auth_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        if len(auth_text) > 4096:
            await websocket.close(code=4400)
            return
        try:
            auth_message = json.loads(auth_text)
        except json.JSONDecodeError:
            await websocket.close(code=4400)
            return
        if not isinstance(auth_message, dict):
            await websocket.close(code=4400)
            return
        token = auth_message.get("token") if auth_message.get("type") == "auth" else None
        if not token:
            await websocket.close(code=4401)
            return
        user = await run_in_threadpool(current_user, token)
        meta = await get_room_meta(room_id)
        if not meta or await is_room_banned(room_id, user.id):
            await websocket.send_json({"type": "error", "detail": "You are not allowed to join this watch room."})
            await websocket.close(code=4406)
            return
        participant_id = str(uuid.uuid4())
        is_owner = user.id == meta["owner_id"]
        if redis_client:
            listener_ready = asyncio.Event()
            listener = asyncio.create_task(
                redis_room_listener(room_id, websocket, listener_ready, participant_id, user.id)
            )
            await listener_ready.wait()
        else:
            state = await get_room_state(room_id)
            if not state:
                await websocket.send_json({"type": "error", "detail": "Watch room not found or expired."})
                await websocket.close(code=4404)
                return
            room_connections.setdefault(room_id, set()).add(websocket)
        
        state = await get_room_state(room_id)
        if not state:
            await websocket.send_json({"type": "error", "detail": "Watch room not found or expired."})
            await websocket.close(code=4404)
            return
        await refresh_room_auxiliary_ttl(room_id)
        await run_in_threadpool(ensure_video_access, token, state["media_path"])
        await add_room_participant(room_id, participant_id, user)
        if not redis_client:
            room_socket_info[websocket] = {"participant_id": participant_id, "user_id": user.id}
        await websocket.send_json({"type": "participant", "participant_id": participant_id, "owner": is_owner})
        await publish_participants(room_id)
        await websocket.send_json(state)
        heartbeat = asyncio.create_task(room_heartbeat(websocket))
        command_times: deque[float] = deque()
        seen_commands: deque[str] = deque(maxlen=128)
        while True:
            message_text = await websocket.receive_text()
            if len(message_text) > 4096:
                await websocket.send_json({"type": "error", "detail": "WebSocket message is too large."})
                continue
            try:
                message = json.loads(message_text)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON message."})
                continue
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "detail": "Message must be a JSON object."})
                continue
            if await is_room_banned(room_id, user.id):
                await websocket.send_json({"type": "kicked"})
                await websocket.close(code=4406)
                return
            if message.get("type") == "pong":
                await websocket.send_json({
                    "type": "clock", "server_time_ms": int(time.time() * 1000),
                    "client_received_ms": message.get("client_received_ms"),
                })
                continue
            if message.get("type") == "sync":
                current_state = await get_room_state(room_id)
                if current_state:
                    sync_state = dict(current_state)
                    sync_state["sync"] = True
                    await websocket.send_json(sync_state)
                continue
            if message.get("type") == "ready":
                if await set_participant_ready(room_id, participant_id):
                    await publish_participants(room_id)
                    current_state = await get_room_state(room_id)
                    if current_state and current_state.get("started"):
                        await websocket.send_json(current_state)
                    else:
                        await maybe_start_room(room_id)
                continue
            if message.get("type") == "kick":
                if not is_owner:
                    await websocket.send_json({"type": "error", "detail": "Only the room creator can kick participants."})
                    continue
                target_id = message.get("participant_id")
                if not isinstance(target_id, str) or len(target_id) > 128:
                    await websocket.send_json({"type": "error", "detail": "Invalid participant."})
                    continue
                participants = await get_room_participants(room_id)
                target = participants.get(target_id)
                if not target or target.get("user_id") == user.id:
                    await websocket.send_json({"type": "error", "detail": "Participant not found."})
                    continue
                await ban_room_user(room_id, target["user_id"])
                await publish_room(room_id, {
                    "type": "kick",
                    "participant_id": target_id,
                    "target_user_id": target["user_id"],
                })
                for participant_id, participant in participants.items():
                    if participant.get("user_id") == target["user_id"]:
                        await remove_room_participant(room_id, participant_id)
                await publish_participants(room_id)
                continue
            if message.get("type") != "command":
                continue
            now = time.monotonic()
            while command_times and now - command_times[0] >= 1:
                command_times.popleft()
            if len(command_times) >= 30:
                await websocket.send_json({"type": "error", "detail": "Too many commands; slow down."})
                continue
            command_times.append(now)
            try:
                command = WatchCommand.model_validate(message)
            except Exception:
                await websocket.send_json({"type": "error", "detail": "Invalid watch command."})
                continue
            if command.action == "seek" and command.position is None:
                await websocket.send_json({"type": "error", "detail": "Seek commands require a position."})
                continue
            if command.action == "rate" and command.playback_rate is None:
                await websocket.send_json({"type": "error", "detail": "Rate commands require a playback rate."})
                continue
            if command.command_id and command.command_id in seen_commands:
                continue
            if command.command_id:
                seen_commands.append(command.command_id)
            current_state = await get_room_state(room_id)
            if not current_state or not current_state.get("started"):
                await websocket.send_json({"type": "error", "detail": "Everyone must be ready before playback can be controlled."})
                continue
            updated = await apply_room_command(room_id, command, user.id)
            if updated is None:
                await websocket.send_json({"type": "error", "detail": "Invalid watch command."})
            else:
                # Deliver an acknowledgement directly to the sender as well
                # as through Redis pub/sub. This keeps controls responsive if
                # the sender's pub/sub listener briefly reconnects.
                await websocket.send_json(updated)
    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await websocket.send_json({"type": "error", "detail": exc.detail})
            await websocket.close(code=4404 if exc.status_code == 404 else 4403)
        except Exception:
            pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if 'participant_id' in locals():
            await remove_room_participant(room_id, participant_id)
            room_socket_info.pop(websocket, None)
            if 'meta' in locals() and meta:
                await publish_participants(room_id)
                await maybe_start_room(room_id)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if redis_client:
            if listener:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
        else:
            room_connections.get(room_id, set()).discard(websocket)


@app.get("/api/media/subtitle")
def subtitle(video_path: str = Query(..., min_length=1), _: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, str]:
    ensure_video_access(token, video_path)
    return {"path": matching_subtitle(token, video_path)}


@app.get("/api/media/embedded-subtitle")
async def embedded_subtitle(
    video_path: str = Query(..., min_length=1),
    _: Any = Depends(current_user),
    token: str = Depends(current_token),
) -> Response:
    """Return the first subtitle stream stored inside a video container as WebVTT."""
    safe_path = str(PurePosixPath("/" + video_path)).lstrip("/")
    if not safe_path or safe_path.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid media path.")
    ensure_video_access(token, safe_path)

    media_url = f"{SUPABASE_URL}/storage/v1/object/{MEDIA_BUCKET}/{quote(safe_path, safe='/')}"
    temp_path = ""
    try:
        suffix = Path(safe_path).suffix or ".video"
        with tempfile.NamedTemporaryFile(prefix="embedded-subtitle-", suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name
            async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                async with client.stream("GET", media_url, headers=supabase_headers(token)) as upstream:
                    if upstream.status_code >= 400:
                        raise HTTPException(status_code=404, detail="Media not found.")
                    async for chunk in upstream.aiter_bytes(1024 * 1024):
                        temp_file.write(chunk)
        try:
            webvtt = await run_in_threadpool(extract_embedded_subtitle, temp_path)
        except HTTPException as exc:
            if exc.status_code == 404:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            raise
        return Response(content=webvtt, media_type="text/vtt")
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Embedded subtitle extraction timed out.") from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


@app.get("/api/media/credits")
def credits(video_path: str = Query(..., min_length=1), _: Any = Depends(current_user), token: str = Depends(current_token)) -> dict[str, Any]:
    ensure_video_access(token, video_path)
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
    select = "media_path,sheets,duration_seconds,interval_seconds,columns,rows,thumbnail_width,thumbnail_height,updated_at"
    visible = {
        row["path"] for row in supabase_request(
            "GET", "/rest/v1/videos", token, params={"select": "path"}
        ) or []
    }
    rows = supabase_request("GET", f"/rest/v1/video_previews?select={select}", token) or []
    return [row for row in rows if row.get("media_path") in visible]


@app.get("/api/admin/users")
def admin_users(token: str = Depends(admin_token)) -> list[dict[str, Any]]:
    return supabase_request("POST", "/rest/v1/rpc/admin_list_users", token, json={}) or []


@app.patch("/api/admin/user-access")
def update_admin_user_access(
    payload: AdminNewVideosAccessRequest,
    user_id: str = Query(..., min_length=1),
    token: str = Depends(admin_token),
) -> dict[str, Any]:
    try:
        data = supabase_request(
            "POST",
            "/rest/v1/rpc/admin_set_new_videos_access",
            token,
            json={"target_user_id": user_id, "enabled": payload.new_videos_access},
        ) or []
    except HTTPException:
        # Keep existing profile rows compatible while PostgREST refreshes its
        # RPC schema cache after the new migration is applied.
        data = supabase_request(
            "PATCH",
            "/rest/v1/profiles",
            token,
            params={"user_id": f"eq.{user_id}", "select": "user_id,admin_access,new_videos_access"},
            headers={"Prefer": "return=representation"},
            json={"new_videos_access": payload.new_videos_access},
        ) or []
    if not data:
        data = supabase_request(
            "GET",
            "/rest/v1/profiles",
            token,
            params={
                "select": "user_id,admin_access,new_videos_access",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        ) or []
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found.")
    if data[0].get("new_videos_access") != payload.new_videos_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unable to update new video access.")
    return data[0]


@app.get("/api/admin/videos")
def admin_videos(token: str = Depends(admin_token)) -> list[dict[str, Any]]:
    return supabase_request(
        "GET",
        "/rest/v1/videos",
        token,
        params={"select": "path,name,user_access", "order": "path.asc"},
    ) or []


@app.patch("/api/admin/video-access")
def update_admin_video_access(
    payload: AdminVideoAccessRequest,
    path: str = Query(..., min_length=1),
    token: str = Depends(admin_token),
) -> dict[str, Any]:
    emails = sorted({email.strip().casefold() for email in payload.user_access if email.strip()})
    data = supabase_request(
        "PATCH",
        "/rest/v1/videos",
        token,
        params={"path": f"eq.{path}", "select": "path,name,user_access"},
        headers={"Prefer": "return=representation"},
        json={"user_access": emails},
    ) or []
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found.")
    return data[0]


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


@app.delete("/api/progress")
def delete_progress(
    media_path: str = Query(..., min_length=1),
    user: Any = Depends(current_user),
    token: str = Depends(current_token),
) -> Response:
    supabase_request(
        "DELETE",
        "/rest/v1/video_progress",
        token,
        params={"user_id": f"eq.{user.id}", "media_path": f"eq.{media_path}"},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    ensure_media_asset_access(token, safe_path)
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


@app.get("/watch-together", include_in_schema=False)
def frontend_watch_together() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "watch-together.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")
