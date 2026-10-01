"""Watch Together room creation and WebSocket handling."""

import asyncio
import json
import time
import uuid
from collections import deque
from typing import Any

from fastapi import Depends, HTTPException, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

try:
    import core as api
    import watch_store as store
except ImportError:
    from .. import core as api
    from .. import watch_store as store


for _name in (
    "room_connections", "room_socket_info", "redis_client", "initial_room_state",
    "get_room_state", "set_room_state", "get_room_meta", "set_room_meta",
    "refresh_room_auxiliary_ttl", "is_room_banned", "ban_room_user",
    "get_room_participants", "add_room_participant", "remove_room_participant",
    "publish_room", "publish_participants", "set_participant_ready",
    "maybe_start_room", "apply_room_command", "redis_room_listener", "room_heartbeat",
):
    setattr(api, _name, getattr(store, _name))


async def watch_room(websocket: WebSocket, room_id: str) -> None:
    client_ip = (
        websocket.headers.get("x-real-ip")
        or (websocket.client.host if websocket.client else "unknown")
    ).strip()
    retry_after = await api.rate_limiter.check(
        f"api:{client_ip}", api.API_RATE_LIMIT, api.API_RATE_WINDOW_SECONDS
    )
    if retry_after is not None:
        await websocket.close(code=4429, reason="Too many requests; please try again later.")
        return
    try:
        uuid.UUID(room_id)
    except ValueError:
        await websocket.close(code=4404)
        return
    origin = websocket.headers.get("origin")
    if origin and "*" not in api.CORS_ORIGINS and origin not in api.CORS_ORIGINS:
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
        user = await run_in_threadpool(api.current_user, token)
        meta = await api.get_room_meta(room_id)
        if not meta or await api.is_room_banned(room_id, user.id):
            await websocket.send_json({"type": "error", "detail": "You are not allowed to join this watch room."})
            await websocket.close(code=4406)
            return
        participant_id = str(uuid.uuid4())
        is_owner = user.id == meta["owner_id"]
        if api.redis_client:
            listener_ready = asyncio.Event()
            listener = asyncio.create_task(
                api.redis_room_listener(room_id, websocket, listener_ready, participant_id, user.id)
            )
            await listener_ready.wait()
        else:
            state = await api.get_room_state(room_id)
            if not state:
                await websocket.send_json({"type": "error", "detail": "Watch room not found or expired."})
                await websocket.close(code=4404)
                return
            api.room_connections.setdefault(room_id, set()).add(websocket)

        state = await api.get_room_state(room_id)
        if not state:
            await websocket.send_json({"type": "error", "detail": "Watch room not found or expired."})
            await websocket.close(code=4404)
            return
        await api.refresh_room_auxiliary_ttl(room_id)
        await run_in_threadpool(api.ensure_video_access, token, state["media_path"])
        await api.add_room_participant(room_id, participant_id, user)
        if not api.redis_client:
            api.room_socket_info[websocket] = {"participant_id": participant_id, "user_id": user.id}
        await websocket.send_json({"type": "participant", "participant_id": participant_id, "owner": is_owner})
        await api.publish_participants(room_id)
        await websocket.send_json(state)
        heartbeat = asyncio.create_task(api.room_heartbeat(websocket))
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
            if await api.is_room_banned(room_id, user.id):
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
                current_state = await api.get_room_state(room_id)
                if current_state:
                    sync_state = dict(current_state)
                    sync_state["sync"] = True
                    await websocket.send_json(sync_state)
                continue
            if message.get("type") == "ready":
                if await api.set_participant_ready(room_id, participant_id):
                    await api.publish_participants(room_id)
                    current_state = await api.get_room_state(room_id)
                    if current_state and current_state.get("started"):
                        await websocket.send_json(current_state)
                    else:
                        await api.maybe_start_room(room_id)
                continue
            if message.get("type") == "kick":
                if not is_owner:
                    await websocket.send_json({"type": "error", "detail": "Only the room creator can kick participants."})
                    continue
                target_id = message.get("participant_id")
                if not isinstance(target_id, str) or len(target_id) > 128:
                    await websocket.send_json({"type": "error", "detail": "Invalid participant."})
                    continue
                participants = await api.get_room_participants(room_id)
                target = participants.get(target_id)
                if not target or target.get("user_id") == user.id:
                    await websocket.send_json({"type": "error", "detail": "Participant not found."})
                    continue
                await api.ban_room_user(room_id, target["user_id"])
                await api.publish_room(room_id, {
                    "type": "kick", "participant_id": target_id, "target_user_id": target["user_id"],
                })
                for target_participant_id, participant in participants.items():
                    if participant.get("user_id") == target["user_id"]:
                        await api.remove_room_participant(room_id, target_participant_id)
                await api.publish_participants(room_id)
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
                command = api.WatchCommand.model_validate(message)
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
            current_state = await api.get_room_state(room_id)
            if not current_state or not current_state.get("started"):
                await websocket.send_json({"type": "error", "detail": "Everyone must be ready before playback can be controlled."})
                continue
            updated = await api.apply_room_command(room_id, command, user.id)
            if updated is None:
                await websocket.send_json({"type": "error", "detail": "Invalid watch command."})
            else:
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
        if "participant_id" in locals():
            await api.remove_room_participant(room_id, participant_id)
            api.room_socket_info.pop(websocket, None)
            if "meta" in locals() and meta:
                await api.publish_participants(room_id)
                await api.maybe_start_room(room_id)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if api.redis_client:
            if listener:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)
        else:
            api.room_connections.get(room_id, set()).discard(websocket)


def register() -> None:
    @api.app.post("/api/watch/rooms")
    async def create_watch_room(
        payload: api.WatchRoomRequest,
        user: Any = Depends(api.current_user),
        token: str = Depends(api.current_token),
    ) -> dict[str, Any]:
        media_path = api.validate_media_path(payload.media_path)
        await run_in_threadpool(api.ensure_video_access, token, media_path)
        room_id = str(uuid.uuid4())
        state = api.initial_room_state(room_id, media_path)
        await api.set_room_state(state)
        await api.set_room_meta(room_id, {"owner_id": user.id, "media_path": media_path})
        return {"room_id": room_id, "media_path": media_path}

    api.app.websocket("/api/watch/rooms/{room_id}")(watch_room)
