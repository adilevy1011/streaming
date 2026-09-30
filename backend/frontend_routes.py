"""Static frontend entry points."""

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    import main as api
except ImportError:
    from . import main as api


def register() -> None:
    app = api.app

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(api.FRONTEND_DIR / "index.html")

    @app.get("/watch", include_in_schema=False)
    def frontend_watch() -> FileResponse:
        return FileResponse(api.FRONTEND_DIR / "watch.html")

    @app.get("/watch-together", include_in_schema=False)
    def frontend_watch_together() -> FileResponse:
        return FileResponse(api.FRONTEND_DIR / "watch-together.html")

    @app.get("/login", include_in_schema=False)
    def frontend_login() -> FileResponse:
        return FileResponse(api.FRONTEND_DIR / "auth" / "login.html")

    @app.get("/auth.js", include_in_schema=False)
    def frontend_auth_script() -> FileResponse:
        return FileResponse(api.FRONTEND_DIR / "auth" / "auth.js")

    app.mount("/", StaticFiles(directory=api.FRONTEND_DIR), name="frontend")
