"""Media, progress, and administration HTTP routes."""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

try:
    import core as api
except ImportError:
    from .. import core as api
from .auth_routes import admin_token


def register() -> None:
    app = api.app

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/media/subtitle")
    def subtitle(video_path: str = Query(..., min_length=1), _: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> dict[str, str]:
        video_path = api.validate_media_path(video_path)
        api.ensure_video_access(token, video_path)
        return {"path": api.matching_subtitle(token, video_path)}

    @app.get("/api/media/embedded-subtitle")
    async def embedded_subtitle(video_path: str = Query(..., min_length=1), _: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> Response:
        safe_path = api.validate_media_path(video_path)
        api.ensure_video_access(token, safe_path)
        media_url = f"{api.SUPABASE_URL}/storage/v1/object/{api.MEDIA_BUCKET}/{quote(safe_path, safe='/')}"
        temp_path = ""
        try:
            suffix = Path(safe_path).suffix or ".video"
            with tempfile.NamedTemporaryFile(prefix="embedded-subtitle-", suffix=suffix, delete=False) as temp_file:
                temp_path = temp_file.name
                async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                    async with client.stream("GET", media_url, headers=api.supabase_headers(token)) as upstream:
                        if upstream.status_code >= 400:
                            raise HTTPException(status_code=404, detail="Media not found.")
                        async for chunk in upstream.aiter_bytes(1024 * 1024):
                            temp_file.write(chunk)
            try:
                webvtt = await run_in_threadpool(api.extract_embedded_subtitle, temp_path)
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
    def credits(video_path: str = Query(..., min_length=1), _: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> dict[str, Any]:
        video_path = api.validate_media_path(video_path)
        api.ensure_video_access(token, video_path)
        data = api.supabase_request("GET", "/rest/v1/video_credits", token, params={"select": "media_path,credits_start_seconds,credits_end_seconds", "media_path": f"eq.{video_path}", "limit": "1"}) or []
        return data[0] if data else {"media_path": video_path, "credits_start_seconds": None, "credits_end_seconds": None}

    @app.get("/api/previews")
    def previews(_: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> list[dict[str, Any]]:
        select = "media_path,sheets,duration_seconds,interval_seconds,columns,rows,thumbnail_width,thumbnail_height,updated_at"
        visible = {row["path"] for row in api.supabase_request("GET", "/rest/v1/videos", token, params={"select": "path"}) or []}
        rows = api.supabase_request("GET", f"/rest/v1/video_previews?select={select}", token) or []
        return [row for row in rows if row.get("media_path") in visible]

    @app.get("/api/admin/users")
    def admin_users(token: str = Depends(admin_token)) -> list[dict[str, Any]]:
        return api.supabase_request("POST", "/rest/v1/rpc/admin_list_users", token, json={}) or []

    @app.patch("/api/admin/user-access")
    def update_admin_user_access(payload: api.AdminNewVideosAccessRequest, user_id: str = Query(..., min_length=1), token: str = Depends(admin_token)) -> dict[str, Any]:
        user_id = api.validate_uuid(user_id, "user id")
        try:
            data = api.supabase_request("POST", "/rest/v1/rpc/admin_set_new_videos_access", token, json={"target_user_id": user_id, "enabled": payload.new_videos_access}) or []
        except HTTPException:
            data = api.supabase_request("PATCH", "/rest/v1/profiles", token, params={"user_id": f"eq.{user_id}", "select": "user_id,admin_access,new_videos_access"}, headers={"Prefer": "return=representation"}, json={"new_videos_access": payload.new_videos_access}) or []
        if not data:
            data = api.supabase_request("GET", "/rest/v1/profiles", token, params={"select": "user_id,admin_access,new_videos_access", "user_id": f"eq.{user_id}", "limit": "1"}) or []
        if not data:
            raise HTTPException(status_code=404, detail="Profile not found.")
        if data[0].get("new_videos_access") != payload.new_videos_access:
            raise HTTPException(status_code=403, detail="Unable to update new video access.")
        return data[0]

    @app.get("/api/admin/videos")
    def admin_videos(token: str = Depends(admin_token)) -> list[dict[str, Any]]:
        return api.supabase_request("GET", "/rest/v1/videos", token, params={"select": "path,name,user_access", "order": "path.asc"}) or []

    @app.patch("/api/admin/video-access")
    def update_admin_video_access(payload: api.AdminVideoAccessRequest, path: str = Query(..., min_length=1), token: str = Depends(admin_token)) -> dict[str, Any]:
        path = api.validate_media_path(path)
        emails = sorted({email.strip().casefold() for email in payload.user_access if email.strip()})
        if any(not api.EMAIL_PATTERN.fullmatch(api.validate_text(email, "email", 320)) for email in emails):
            raise HTTPException(status_code=400, detail="Invalid email in user access list.")
        data = api.supabase_request("PATCH", "/rest/v1/videos", token, params={"path": f"eq.{path}", "select": "path,name,user_access"}, headers={"Prefer": "return=representation"}, json={"user_access": emails}) or []
        if not data:
            raise HTTPException(status_code=404, detail="Video not found.")
        return data[0]

    @app.get("/api/media/next")
    def next_media(path: str = Query(..., min_length=1), _: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> dict[str, Any] | None:
        safe_path = api.validate_media_path(path)
        visible_by_path = {video["path"]: video for video in api.catalog_videos(token)}
        if safe_path not in visible_by_path:
            raise HTTPException(status_code=404, detail="Video not found.")
        root_folder = safe_path.split("/", 1)[0] if "/" in safe_path else ""
        rows = api.supabase_request("GET", "/rest/v1/folder_orderings", token, params={"select": "ordered_video_paths", "folder_path": f"eq.{root_folder}", "limit": "1"}) or []
        if not rows:
            return None
        ordered_paths = rows[0].get("ordered_video_paths") or []
        try:
            current_index = ordered_paths.index(safe_path)
        except ValueError:
            return None
        for candidate_path in ordered_paths[current_index + 1:]:
            candidate = visible_by_path.get(candidate_path)
            if candidate:
                preview_rows = api.supabase_request("GET", "/rest/v1/video_previews", token, params={"select": "media_path,sheets,updated_at", "media_path": f"eq.{candidate_path}", "limit": "1"}) or []
                result = dict(candidate)
                if preview_rows:
                    result["previewManifest"] = preview_rows[0]
                return result
        return None

    @app.get("/api/admin/folder-orderings")
    def admin_folder_orderings(token: str = Depends(admin_token)) -> list[dict[str, Any]]:
        return api.supabase_request("GET", "/rest/v1/folder_orderings", token, params={"select": "folder_path,item_paths,ordered_video_paths,updated_at", "order": "folder_path.asc"}) or []

    @app.patch("/api/admin/folder-ordering")
    def update_admin_folder_ordering(payload: api.FolderOrderingRequest, folder_path: str = Query(default=""), token: str = Depends(admin_token)) -> dict[str, Any]:
        folder_path = api.validate_media_path(folder_path) if folder_path else ""
        item_paths = [api.validate_media_path(item) for item in payload.item_paths]
        if len(item_paths) != len(set(item_paths)):
            raise HTTPException(status_code=400, detail="Folder ordering contains duplicate items.")
        api.supabase_request("POST", "/rest/v1/rpc/admin_update_folder_ordering", token, json={"target_folder": folder_path, "new_items": item_paths})
        rows = api.supabase_request("GET", "/rest/v1/folder_orderings", token, params={"select": "folder_path,item_paths,ordered_video_paths,updated_at", "folder_path": f"eq.{folder_path}", "limit": "1"}) or []
        if not rows:
            raise HTTPException(status_code=404, detail="Folder ordering not found.")
        return rows[0]

    @app.get("/api/progress")
    def progress(user: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> list[dict[str, Any]]:
        return api.supabase_request("GET", "/rest/v1/video_progress", token, params={"select": "media_path,position_seconds,duration_seconds,completed,updated_at", "user_id": f"eq.{user.id}", "order": "updated_at.desc"}) or []

    @app.post("/api/progress")
    def save_progress(payload: api.ProgressRequest, user: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> dict[str, Any]:
        payload.media_path = api.validate_media_path(payload.media_path)
        row = payload.model_dump(exclude_none=True)
        row["user_id"] = user.id
        data = api.supabase_request("POST", "/rest/v1/video_progress", token, params={"on_conflict": "user_id,media_path"}, headers={"Prefer": "resolution=merge-duplicates,return=representation"}, json=row)
        return (data or [row])[0]

    @app.delete("/api/progress")
    def delete_progress(media_path: str = Query(..., min_length=1), user: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> Response:
        media_path = api.validate_media_path(media_path)
        api.supabase_request("DELETE", "/rest/v1/video_progress", token, params={"user_id": f"eq.{user.id}", "media_path": f"eq.{media_path}"})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/media/file/{media_path:path}")
    async def media_file(media_path: str, request: Request, _: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> Response:
        safe_path = api.validate_media_path(media_path)
        api.ensure_media_asset_access(token, safe_path)
        media_url = f"{api.SUPABASE_URL}/storage/v1/object/{api.MEDIA_BUCKET}/{quote(safe_path, safe='/')}"
        headers = api.supabase_headers(token)
        if request.headers.get("range"):
            headers["Range"] = request.headers["range"]
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
