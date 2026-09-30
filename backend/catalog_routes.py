"""Catalog and media-listing routes."""

from typing import Any

from fastapi import Depends, Query
from fastapi.responses import StreamingResponse

try:
    import main as api
except ImportError:
    from . import main as api


def register() -> None:
    app = api.app

    @app.get("/api/media/stream")
    def media_stream(
        path: str = Query(default=""),
        _: Any = Depends(api.current_user),
        token: str = Depends(api.current_token),
    ) -> StreamingResponse:
        safe_path = api.validate_media_path(path) if path else ""
        return StreamingResponse(api.stream_videos(token, safe_path), media_type="application/x-ndjson")

    @app.get("/api/media/files")
    def files(_: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> list[dict[str, Any]]:
        return api.list_files(token)

