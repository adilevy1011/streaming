# Preview Generation Script

The `scripts/generate_previews.py` worker creates timeline preview sprite sheets for videos in the Supabase `media` bucket. It stores the generated sheets under `__previews/` and records their metadata in the `video_previews` table.

## Requirements

- Python 3.10 or newer.
- `ffmpeg` and `ffprobe` available on `PATH`.
- A configured `backend/.env` containing the Supabase URL and service-role key.
- The Supabase schema and storage policies applied with the setup instructions.

The service-role key is required because the worker reads source videos and uploads generated preview files. Keep it private and run the worker only in a trusted environment.

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
- If Supabase requests fail, verify `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `backend/.env`.
- If a video is skipped, confirm that its extension is supported and that the worker can read the `media` bucket.
- Use `--force` only when existing previews need to be rebuilt.
