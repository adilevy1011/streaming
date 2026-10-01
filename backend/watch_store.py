"""Watch Together room state and Redis/in-memory persistence."""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import WebSocket

try:
    import core as api
except ImportError:
    from . import core as api


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
redis_client = api.redis_client


@asynccontextmanager
async def lifespan(_app: Any):
    if redis_client:
        await redis_client.ping()
    try:
        yield
    finally:
        if redis_client:
            await redis_client.aclose()


api.app.router.lifespan_context = lifespan


def room_lock(room_id: str) -> asyncio.Lock:
    return room_locks.setdefault(room_id, asyncio.Lock())


def initial_room_state(room_id: str, media_path: str) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "type": "state", "room_id": room_id, "media_path": media_path,
        "revision": 0, "action": "snapshot", "position": 0,
        "playing": False, "playback_rate": 1, "server_time_ms": now,
        "started": False, "expires_at_ms": now + api.ROOM_TTL_SECONDS * 1000,
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
        await redis_client.set(f"{ROOM_STATE_PREFIX}{state['room_id']}", json.dumps(state), ex=api.ROOM_TTL_SECONDS)
    else:
        state["expires_at_ms"] = int(time.time() * 1000) + api.ROOM_TTL_SECONDS * 1000
        room_states[state["room_id"]] = state


async def get_room_meta(room_id: str) -> dict[str, Any] | None:
    if redis_client:
        raw = await redis_client.get(f"{ROOM_META_PREFIX}{room_id}")
        return json.loads(raw) if raw else None
    return room_meta.get(room_id)


async def set_room_meta(room_id: str, meta: dict[str, Any]) -> None:
    if redis_client:
        await redis_client.set(f"{ROOM_META_PREFIX}{room_id}", json.dumps(meta), ex=api.ROOM_TTL_SECONDS)
    else:
        room_meta[room_id] = meta


async def refresh_room_auxiliary_ttl(room_id: str) -> None:
    if not redis_client:
        return
    await redis_client.expire(f"{ROOM_META_PREFIX}{room_id}", api.ROOM_TTL_SECONDS)
    await redis_client.expire(f"{ROOM_BANS_PREFIX}{room_id}", api.ROOM_TTL_SECONDS)
    await redis_client.expire(f"{ROOM_PARTICIPANTS_PREFIX}{room_id}", api.ROOM_TTL_SECONDS)


async def is_room_banned(room_id: str, user_id: str) -> bool:
    if redis_client:
        return bool(await redis_client.sismember(f"{ROOM_BANS_PREFIX}{room_id}", user_id))
    return user_id in room_bans.get(room_id, set())


async def ban_room_user(room_id: str, user_id: str) -> None:
    if redis_client:
        key = f"{ROOM_BANS_PREFIX}{room_id}"
        await redis_client.sadd(key, user_id)
        await redis_client.expire(key, api.ROOM_TTL_SECONDS)
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
        await redis_client.expire(key, api.ROOM_TTL_SECONDS)
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
                and payload.get("target_user_id") != info.get("user_id")
            ):
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
        await redis_client.expire(key, api.ROOM_TTL_SECONDS)
    else:
        room_participants.setdefault(room_id, {})[participant_id] = participant
    return True


async def maybe_start_room(room_id: str) -> dict[str, Any] | None:
    state = await get_room_state(room_id)
    participants = await get_room_participants(room_id)
    if not state or state.get("started") or not participants or not all(p.get("ready") for p in participants.values()):
        return None
    if redis_client:
        claimed = await redis_client.set(f"{ROOM_START_PREFIX}{room_id}", "1", nx=True, ex=api.ROOM_TTL_SECONDS)
        if not claimed:
            return None
    else:
        async with room_lock(room_id):
            state = await get_room_state(room_id)
            if not state or state.get("started"):
                return None
    return await apply_room_command(room_id, api.WatchCommand(action="play", command_id="room-ready"), "system")


async def apply_room_command(room_id: str, command: Any, user_id: str) -> dict[str, Any] | None:
    now = int(time.time() * 1000)
    if redis_client:
        if command.action not in {"play", "pause", "seek", "rate"}:
            return None
        encoded = await redis_client.eval(
            ROOM_STATE_SCRIPT, 2,
            f"{ROOM_STATE_PREFIX}{room_id}", f"{ROOM_CHANNEL_PREFIX}{room_id}",
            command.action, str(command.position if command.position is not None else 0),
            str(now), user_id, command.command_id or str(uuid.uuid4()), str(api.ROOM_TTL_SECONDS),
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
        elif command.playback_rate is None:
            return None
        else:
            state["playback_rate"] = command.playback_rate
        state.update({
            "revision": state["revision"] + 1, "action": command.action,
            "server_time_ms": now, "actor_id": user_id,
            "command_id": command.command_id or str(uuid.uuid4()),
        })
        await set_room_state(state)
        await publish_room(room_id, state)
        return state


async def redis_room_listener(
    room_id: str, websocket: WebSocket, ready: asyncio.Event, participant_id: str, user_id: str,
) -> None:
    pubsub = redis_client.pubsub()
    try:
        await pubsub.subscribe(f"{ROOM_CHANNEL_PREFIX}{room_id}")
        ready.set()
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            payload = json.loads(message["data"])
            if payload.get("type") == "kick":
                if payload.get("participant_id") != participant_id and payload.get("target_user_id") != user_id:
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
