# Preview Generation Script

The `scripts/generate_previews.py` worker creates timeline preview sprite sheets for videos in the Supabase `media` bucket. It stores the generated sheets under `__previews/` and records their metadata in the `video_previews` table.

## Requirements

- Python 3.10 or newer.
- `ffmpeg` and `ffprobe` available on `PATH`.
- A configured `backend/.env` containing the Supabase URL, anon key, and bucket name.
- The Supabase schema and storage policies applied with the setup instructions.

When `SUPABASE_SERVICE_ROLE_KEY` is configured, the worker uses it for
unattended trusted-job execution. If it is missing, the worker prompts for an
application login through `/api/auth/login` and uses the authenticated user's
access token with `SUPABASE_ANON_KEY` instead. Set `PREVIEW_API_URL` in `backend/.env` when the
backend is not running at `http://127.0.0.1:8000`.

For service-role mode, add:
```env
SUPABASE_SERVICE_ROLE_KEY=<YOUR_SUPABASE_SERVICE_ROLE_KEY>
```
The service role key bypasses all levels of security, so make sure you run this
script in a trusted environment.

## Run the worker

Run these commands from the repository root:

```bash
python scripts/generate_previews.py
```

To rebuild previews even when a manifest already exists:

```bash
python scripts/generate_previews.py --force
```

The script automatically loads `backend/.env`.

## Supported video types

Preview generation supports:

```text
mp4, m4v, webm, mov, mkv, avi, ogv, mpeg, mpg, ts
```

## Behavior

- Existing previews are reused unless `--force` is supplied.
- Preview metadata is written only after the preview sheets are successfully created and uploaded.
- Stale manifests and preview sheets are removed when their source videos no longer exist.
- The worker can be run manually after media changes or scheduled as a trusted background job.

## Troubleshooting

- If the worker cannot start, verify that both `ffmpeg` and `ffprobe` are installed and available on `PATH`.
- If Supabase requests fail, verify `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `MEDIA_BUCKET` in `backend/.env`.
- If login fails, verify the backend is running at `PREVIEW_API_URL` and that the account is in `ALLOWED_EMAILS`.
- If a video is skipped, confirm that its extension is supported and that the worker can read the `media` bucket.
- Use `--force` only when existing previews need to be rebuilt.
