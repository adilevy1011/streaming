"""Authentication and user-profile HTTP routes."""

from typing import Any

from fastapi import Depends, Header, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

try:  # Running from the backend directory (the production layout).
    import main as api
except ImportError:  # Running as the backend package (tests/tools).
    from . import main as api


def register() -> None:
    app = api.app

    @app.post("/api/auth/login")
    async def login(payload: api.LoginRequest) -> dict[str, Any]:
        email = api.validate_email(payload.email)
        api.reject_unallowed(email)
        retry_after = await api.rate_limiter.check(
            f"login-email:{email}", api.LOGIN_RATE_LIMIT, api.LOGIN_RATE_WINDOW_SECONDS
        )
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts; please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        try:
            session = await run_in_threadpool(
                api.supabase.auth.sign_in_with_password,
                {"email": email, "password": payload.password},
            )
            return api.session_response(session)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid email or password.") from exc

    @app.post("/api/auth/refresh")
    def refresh(payload: api.RefreshRequest) -> dict[str, Any]:
        try:
            session = api.supabase.auth.refresh_session(payload.refresh_token)
            return api.session_response(session)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Unable to refresh session.") from exc

    @app.get("/api/auth/session")
    def session(user: Any = Depends(api.current_user)) -> dict[str, Any]:
        return {"user": {"id": user.id, "email": user.email}}

    @app.get("/api/profile")
    def profile(user: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> dict[str, Any]:
        params = {
            "select": "user_id,subtitles_enabled,admin_access,new_videos_access",
            "user_id": f"eq.{user.id}",
            "limit": "1",
        }
        data = api.supabase_request("GET", "/rest/v1/profiles", token, params=params) or []
        if data:
            return data[0]
        row = {"user_id": user.id, "subtitles_enabled": False}
        created = api.supabase_request(
            "POST", "/rest/v1/profiles", token,
            headers={"Prefer": "return=representation"}, json=row,
        ) or []
        return created[0] if created else {**row, "admin_access": False, "new_videos_access": True}

    @app.patch("/api/profile")
    def update_profile(
        payload: api.ProfileRequest,
        user: Any = Depends(api.current_user),
        token: str = Depends(api.current_token),
    ) -> dict[str, Any]:
        row = {"user_id": user.id, "subtitles_enabled": payload.subtitles_enabled}
        data = api.supabase_request(
            "POST", "/rest/v1/profiles", token,
            params={"on_conflict": "user_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"}, json=row,
        ) or []
        return data[0] if data else row


def admin_token(user: Any = Depends(api.current_user), token: str = Depends(api.current_token)) -> str:
    data = api.supabase_request(
        "GET", "/rest/v1/profiles", token,
        params={"select": "admin_access", "user_id": f"eq.{user.id}", "limit": "1"},
    ) or []
    if not data or not data[0].get("admin_access"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return token
