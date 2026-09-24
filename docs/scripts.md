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
script in a trusted environment if you decide to use it.

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



# Credit detection script

The `scripts/detect_credits.py` worker analyzes the preview sprite sheets and
records the detected end-credit range in the Supabase `video_credits` table.
Run it from the repository root after preview generation:

```bash
python scripts/detect_credits.py
```

The script loads `backend/.env`, reads preview manifests from
`video_previews`, and downloads the referenced sprite sheets from the
configured `MEDIA_BUCKET`. It samples the final quarter of each video's
timeline, scoring each tile with OpenCV image features and EasyOCR text
recognition. It looks for evidence such as credit-related words (`director`,
`cast`, `written`, and similar terms), multiple text rows, bright text over
the image, and the visual layout of credit cards. A sustained match is located
with a binary search, and the result is upserted into `video_credits` with the
start time, video duration as the end time, confidence score, and detector
name.

The detector requires `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY`. It also requires the Python packages listed in
`scripts/requirements.txt`. The service-role key is needed because the worker
reads storage objects and reconciles credit records as a trusted background
job; keep it in a private environment.

An optional trained MobileNet verifier can be enabled with:

```env
CREDITS_MODEL_PATH=models/credit_mobilenet.pt
CREDITS_MODEL_THRESHOLD=0.55
```
<small>You are welcome to use the model, but be aware that it has currently only been trained on a relativly small dataset, which makes it prone to mistakes.</small>

When configured, the model must agree with the heuristic detector for normal
matches. If OCR is inconclusive but the model finds a sustained match, the
script records a model-only fallback. `CREDITS_SCORE_THRESHOLD` controls the
heuristic score threshold, and `CREDITS_DEBUG=true` prints sampled scores and
the effective threshold for troubleshooting.

Each run also removes `video_credits` rows whose source video no longer
exists, then rechecks every available preview manifest. Videos without a
confident match are left without a credit row and can be inspected by rerunning
with `CREDITS_DEBUG=true`.
