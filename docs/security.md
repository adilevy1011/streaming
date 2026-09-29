# Security

This document describes the security controls currently implemented by the application and the deployment assumptions they rely on.

## Request boundary

Browser requests use the FastAPI `/api/*` endpoints. FastAPI performs authentication, authorization, validation, rate limiting, and access checks before making requests to Supabase Auth, PostgREST, or Storage.

The backend is intended to run on `127.0.0.1:8000` behind nginx. Do not expose the Uvicorn port publicly. FastAPI uses nginx's `X-Real-IP` header for rate limiting, so clients must not be able to connect directly to Uvicorn or spoof that header.

The trusted maintenance workers are separate from the browser request path. They run on a trusted computer and use the Supabase service-role key directly. They are not public API endpoints.

## Authentication and authorization

- Login is available only through FastAPI at `/api/auth/login`.
- The login email is normalized and checked against `ALLOWED_EMAILS` before Supabase Auth is called.
- Disallowed login attempts never reach Supabase Auth.
- Access tokens are validated through Supabase Auth before protected routes continue.
- The allowlist is checked again against the authenticated user's email after token validation.
- Admin routes require the authenticated user's `admin_access` profile flag.
- Media, subtitle, preview, progress, and watch-room routes require authentication.
- Video and media-asset access is checked against the user's visible catalog before Storage is accessed.
- Supabase Row Level Security policies remain the database-level enforcement layer.

## Rate limiting and request limits

All `/api/*` HTTP requests pass through the FastAPI rate limiter. WebSocket connection attempts are rate-limited as well.

Default limits are:

- General API traffic: 120 requests per IP per 60 seconds.
- Login traffic: 5 requests per IP per 60 seconds.
- Login traffic: an additional 5 attempts per normalized email per 60 seconds.
- API request body size: 2 MiB.
- API query-string size: 4096 characters.

When `REDIS_URL` is configured, rate-limit counters are shared between workers. Without Redis, counters are process-local. Production deployments with multiple Uvicorn workers should configure Redis.

The nginx configuration also applies `client_max_body_size 2m`, which protects requests before they reach FastAPI, including requests that do not provide a `Content-Length` header.

The limits can be changed with:

```env
API_RATE_LIMIT=120
API_RATE_WINDOW_SECONDS=60
LOGIN_RATE_LIMIT=5
LOGIN_RATE_WINDOW_SECONDS=60
MAX_API_BODY_BYTES=2097152
```

## Input validation

Before user-controlled values are used in Supabase requests, the backend validates:

- Email format and maximum length.
- Password and refresh-token length.
- Access-token length and control characters.
- UUID format for administrative user identifiers.
- Media-path length, traversal segments, backslashes, control characters, and PostgREST-special characters.
- Admin email-list size and email values.
- Progress and watch-room payload sizes and numeric ranges.

The backend rejects oversized query strings, invalid content-length headers, control characters, directory traversal, and malformed identifiers locally.

Supabase request paths and selected fields are defined by the backend. User-controlled values are passed as HTTP parameters or JSON values rather than being used to construct SQL statements.

## Supabase and database security

- The browser never receives `SUPABASE_SERVICE_ROLE_KEY`.
- The media bucket is private.
- Authenticated users cannot directly insert, update, or delete preview manifests or preview storage objects after migration `20260929130000_restrict_preview_writes.sql`.
- Preview generation is performed by the trusted service-role worker.
- Database tables use Row Level Security policies for user, admin, media, progress, and preview access.
- Profile and progress records are scoped to the authenticated user where applicable.
- Administrative database functions enforce administrative access.
- Supabase credentials are loaded from environment variables and should be stored in a protected environment file.

## Media and streaming protection

- Media requests are proxied through FastAPI rather than exposing the Supabase Storage URL to the browser.
- The backend checks media visibility before opening a Storage stream.
- Media paths are normalized and checked for traversal attempts.
- Range requests are forwarded only after authorization succeeds.
- Upstream media responses are streamed rather than fully buffered in memory.
- Temporary files used for embedded subtitle extraction are deleted after processing.
- Embedded subtitle extraction has a timeout.
- API responses are marked `no-store` by FastAPI, except for explicitly cacheable preview media responses.
- nginx sends `Referrer-Policy: no-referrer` to reduce accidental token leakage through referrer headers.

## WebSocket security

- Watch-room WebSockets validate the origin against `CORS_ORIGINS`.
- Room identifiers must be valid UUIDs.
- WebSocket clients must authenticate with a valid Supabase access token.
- Room membership, bans, ownership, and media access are checked server-side.
- WebSocket messages have a size limit.
- Playback commands are rate-limited per connection.
- Duplicate command identifiers are ignored.
- Watch-room state can use Redis for shared state and expiry across workers.

## nginx deployment controls

The recommended nginx configuration:

- Redirects HTTP to HTTPS.
- Proxies only `/api/*` to FastAPI.
- Keeps Uvicorn bound to `127.0.0.1:8000`.
- Blocks backend, documentation, script, and Supabase project directories.
- Blocks hidden files such as `.env` and `.git`.
- Applies a 2 MiB request-body limit.
- Passes the client IP to FastAPI through `X-Real-IP`.
- Disables caching for API responses and authentication JavaScript.
- Supports WebSocket upgrades for watch rooms.


## Trusted workers

`scripts/generate_previews.py` and `scripts/detect_credits.py` are trusted maintenance jobs, not public application endpoints.

- Both workers must run only on trusted machines.
- The service-role key bypasses Row Level Security and must never be exposed to the browser or committed to source control.
- Worker environment files should be readable only by the account that runs the jobs.

## Secret and deployment hygiene

- Never commit `.env` files, service-role keys, passwords, JWT secrets, private keys, or TLS certificates.
- Use HTTPS in production.
- Keep Redis on a private interface and do not expose port 6379 publicly.
- Disable Supabase user sign-up when the deployment is intended to be private.
- Keep `ALLOWED_EMAILS` restricted to the intended users.
- Review Supabase RLS policies and Storage policies after every schema change.
- Restart the backend after changing environment variables.

## Known limitations

- Access tokens are currently included in some media and subtitle query-string URLs because browser media elements cannot attach arbitrary Authorization headers. Query strings can appear in browser history or proxy logs. The referrer policy reduces referrer leakage but does not remove this limitation.
- IP-based controls can be affected by shared NATs, proxies, or inaccurate proxy configuration.
- The in-process rate-limit fallback is not shared between multiple backend workers; use Redis in production.
- No application-layer defense can replace timely dependency updates, Supabase security updates, TLS maintenance, least-privilege credentials, and server hardening.
