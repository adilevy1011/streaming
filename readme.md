# ADLV Media Streamer

A self-hosted media player built with a static frontend, a small FastAPI backend, Supabase Auth, Supabase Storage, and PostgreSQL.

The application:

- Authenticates users with Supabase email/password authentication.
- Lists video files from a Supabase Storage bucket.
- Streams media through the backend instead of exposing storage URLs directly in the player.
- Stores per-user playback progress in PostgreSQL.
- Generates optional timeline preview sprite sheets with `ffmpeg` and `ffprobe`.
- Can be served publicly with nginx and HTTPS.

## Architecture

```text
Browser
  |
  | HTTPS
  v
nginx
  |-- static files: index.html, watch.html, *.js
  |
  |-- /api/*
  v
Uvicorn / FastAPI backend
  |
  |-- Supabase Auth
  |-- Supabase REST API
  |-- Supabase Storage
  v
Supabase project
```

The backend must be the only place where the Supabase service-role key is used. Never put that key in JavaScript, HTML, nginx configuration, or a public repository.

## Requirements

- A Supabase project.
- Python 3.10 or newer.
- `ffmpeg` and `ffprobe` on `PATH` if you want to generate previews.
- nginx for a public Linux deployment.
- A domain name pointing to the server for HTTPS deployment.
- Git, if installing from the repository.

The application can also run locally without nginx. In that case, Uvicorn serves the frontend and API from one process.

## 1. Get the project

Clone the repository on the machine that will run the streamer:

```bash
git clone https://github.com/adilevy1011/streaming media-streamer
cd media-streamer
```

## 2. Create and configure Supabase

### Create a project

1. Create a new project at [supabase.com](https://supabase.com/).
2. Record the project URL, anon/public key, and service-role key.
3. In Authentication, enable Email provider authentication.
4. Decide whether email confirmation should be required.

The service-role key is highly privileged. Store it only on the machine that generates previews as it is not needed for anything else.

### Install and authenticate the Supabase CLI

Install the Supabase CLI using the official instructions, then authenticate:

```bash
supabase login
```

Link this checkout to the new project. Replace the placeholder with the project reference shown in the Supabase dashboard URL:

```bash
supabase link --project-ref <YOUR_PROJECT_REF>
```

### Apply the database and storage schema

Push the migrations in `supabase/migrations`:

```bash
supabase db push
```

The migrations create:

- `public.video_progress`, including per-user RLS policies.
- `public.video_previews`, readable by authenticated users.
- A private Storage bucket named `media`.
- Storage policies for authenticated users to list and read media objects.

The current storage policy intentionally permits authenticated users to read the media bucket. The application also has an email allowlist in its backend configuration, but the database storage policy itself does not enforce that allowlist.

The bucket name is currently fixed as `media` in the SQL policies. If you change the bucket name, update the migration policies and the `MEDIA_BUCKET` environment variable together.

### Create a user

Create at least one user in Supabase Dashboard → Authentication → Users, or use the configured sign-up flow if signups are enabled.

The user’s email must appear in `ALLOWED_EMAILS` below or the backend will reject login and authenticated requests.

## 3. Configure the backend

Create `backend/.env` locally. Use real values only in this ignored file:

```env
SUPABASE_URL=https://<YOUR_PROJECT_REF>.supabase.co
SUPABASE_ANON_KEY=<YOUR_SUPABASE_ANON_KEY>
SUPABASE_SERVICE_ROLE_KEY=<YOUR_SUPABASE_SERVICE_ROLE_KEY>
ALLOWED_EMAILS=<you@example.com>
MEDIA_BUCKET=media
CORS_ORIGINS=https://<YOUR_DOMAIN>
```

Multiple allowed emails may be separated by commas:

```env
ALLOWED_EMAILS=owner@example.com,viewer@example.com
```

For local development, use:

```env
CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
```

Keep this file private and restrict its permissions on Linux:

```bash
chmod 600 backend/.env
```

The browser only receives normal login/session responses. It must never receive `SUPABASE_SERVICE_ROLE_KEY`.

## 4. Install and run locally

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd backend
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The health endpoint is:

```text
http://127.0.0.1:8000/api/health
```

The frontend calls the API through the same origin under `/api`, so no frontend environment file is required.

## 5. Upload media

Upload video files to the `media` bucket in Supabase Dashboard → Storage. Subdirectories are supported and are shown as library categories by the frontend.

Supported video extensions for preview generation include:

```text
mp4, m4v, webm, mov, mkv, avi, ogv, mpeg, mpg, ts
```

The backend lists files recursively, so a layout such as this works:

```text
media/
  Movies/
    Example Movie.mp4
  Shows/
    Example Show/
      Season 1/
        Episode 1.mkv
```

## 6. Generate timeline previews (optional)

Install ffmpeg so both `ffmpeg` and `ffprobe` are available on `PATH`. Then run the preview worker from the repository root:

```bash
python scripts/generate_previews.py
```

The script automatically loads `backend/.env`. It uses the service-role key to read videos, generate sprite sheets, upload them under `__previews/`, and write rows to `video_previews`.

To regenerate every preview:

```bash
python scripts/generate_previews.py --force
```

The worker also removes preview manifests and sprite sheets whose source videos no longer exist. Run it again after adding or removing media.

## 7. Configure nginx

`backend/nginx.conf.example` is a safe template. Copy it into your nginx configuration directory and replace the placeholders:

- `example.com` with your domain.
- `/var/www/your-app` with the absolute path to this checkout.
- The certificate paths with the paths generated for your domain.

Example deployment path:

```bash
sudo mkdir -p /var/www
sudo git clone https://github.com/adilevy1011/streaming /var/www/media-streamer
```

Then update the nginx template so the frontend root is:

```nginx
root /var/www/media-streamer;
```

The important routing behavior is:

- `/api/` is proxied to Uvicorn at `127.0.0.1:8000`.
- Other paths serve the static frontend from the repository root.
- Media requests remain authenticated through the backend.

Validate and reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### HTTPS with Let’s Encrypt

Install Certbot using your operating system’s package manager. After DNS points to the server, issue a certificate for your domain:

```bash
sudo certbot --nginx -d <YOUR_DOMAIN>
```

Let Certbot update the certificate paths, or update them manually in the nginx configuration. Never commit private keys or machine-specific certificate directories to Git.

## 8. Run the backend as a system service

The repository includes `backend/uvicorn.service` as a starting point. Edit its paths, user, and environment-file location for your server.

Create a protected system environment file:

```bash
sudo mkdir -p /etc/streaming
sudo cp backend/.env /etc/streaming/backend.env
sudo chmod 600 /etc/streaming/backend.env
```

Update the service file so these values match your deployment:

```ini
WorkingDirectory=/var/www/media-streamer/backend
EnvironmentFile=/etc/streaming/backend.env
ExecStart=/var/www/media-streamer/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
```

Install and start it:

```bash
sudo cp backend/uvicorn.service /etc/systemd/system/media-streamer.service
sudo systemctl daemon-reload
sudo systemctl enable --now media-streamer
sudo systemctl status media-streamer
```

View logs with:

```bash
sudo journalctl -u media-streamer -f
```

If the preview worker is run manually on the server, keep `backend/.env` available there. If it is run as a scheduled job, use a protected environment file and an explicit working directory.

## 9. Updating the application

Before updating production:

```bash
git pull
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
supabase db push
```

Then restart the backend and reload nginx:

```bash
sudo systemctl restart media-streamer
sudo nginx -t && sudo systemctl reload nginx
```

Run the preview worker again if the update changes preview behavior:

```bash
python scripts/generate_previews.py
```

## Troubleshooting

### `Missing SUPABASE_URL` or key errors

Confirm that `backend/.env` exists and contains every required variable. When using systemd, confirm that `EnvironmentFile` points to the correct file.

### Login returns unauthorized

Check that:

1. The user exists in Supabase Authentication.
2. The password is correct.
3. The email is present in `ALLOWED_EMAILS`.
4. Email confirmation requirements have been satisfied.
5. `SUPABASE_URL` and `SUPABASE_ANON_KEY` belong to the same project.

### The library is empty

Confirm that:

- Videos were uploaded to the `media` bucket.
- The bucket name matches `MEDIA_BUCKET`.
- The authenticated user can read the bucket under the storage policies.
- The backend is running and `/api/health` responds successfully.

### Previews are missing

Confirm that:

- Both `ffmpeg` and `ffprobe` are installed and available on `PATH`.
- `SUPABASE_SERVICE_ROLE_KEY` is present in `backend/.env`.
- The script can read and write the configured bucket.
- The source files use one of the supported video extensions.

Run the worker without `--force` first; use `--force` only when previews must be rebuilt.

### nginx returns 502 Bad Gateway

Check that Uvicorn is listening on `127.0.0.1:8000`:

```bash
curl http://127.0.0.1:8000/api/health
```

Then inspect the service logs:

```bash
sudo journalctl -u media-streamer -n 100 --no-pager
```

## Security checklist

- Never commit `.env`, service-role keys, passwords, JWT secrets, private keys, or certificate files.
- Never put `SUPABASE_SERVICE_ROLE_KEY` on the frontend, preferably keep it only on the device the runs the preview generation script.
- Keep the Storage bucket private.
- Use HTTPS in production.
- Restrict permissions on environment files.
- Review the storage policies before opening the project to untrusted users.
- Back up Supabase data before applying destructive schema changes.
- Use a separate Supabase project for development and production.

## Repository layout

```text
backend/
  main.py                  FastAPI application
  requirements.txt         Python dependencies
  .env                     Local/private configuration, not committed
  nginx.conf.example       Safe nginx template
scripts/
  generate_previews.py     Preview generation and cleanup worker
supabase/
  migrations/              Database, RLS, bucket, and storage policies
  config.toml               Local Supabase CLI configuration
index.html                  Main library page
watch.html                  Video player page
app.js, watch.js, api.js    Frontend application code
```

