# Setup

## Requirements

- A Supabase project.
- Python 3.10 or newer.
- nginx and a domain for public HTTPS deployment.
- Git, if installing from the repository.

The application can run locally without nginx; Uvicorn serves the frontend and API from one process.

## 1. Get the project

```bash
git clone https://github.com/adilevy1011/streaming media-streamer
cd media-streamer
```

## 2. Configure Supabase

1. Create a project at [supabase.com](https://supabase.com/).
2. Record the project URL, anon/public key, and service-role key.
3. Enable Email provider authentication and choose the email-confirmation policy.

Install and authenticate the Supabase CLI using its official instructions, then link and migrate the project:

```bash
supabase login
supabase link --project-ref <YOUR_PROJECT_REF>
supabase db push
```

The migrations create `video_progress`, `video_previews`, a private `media` Storage bucket, and their RLS/storage policies. The bucket name is fixed as `media` in the SQL policies; update both the policies and `MEDIA_BUCKET` if you change it.

Create at least one user in Supabase Dashboard → Authentication → Users. The user’s email must appear in `ALLOWED_EMAILS`.

## 3. Configure the backend

Create `backend/.env` locally and keep it private:

```env
SUPABASE_URL=https://<YOUR_PROJECT_REF>.supabase.co
SUPABASE_ANON_KEY=<YOUR_SUPABASE_ANON_KEY>
ALLOWED_EMAILS=<you@example.com>
MEDIA_BUCKET=media
CORS_ORIGINS=https://<YOUR_DOMAIN>
```

For local development:

```env
ALLOWED_EMAILS=owner@example.com,viewer@example.com
CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
```

On Linux, restrict the file with `chmod 600 backend/.env`. Never expose `SUPABASE_SERVICE_ROLE_KEY` to the browser.

## 4. Run locally

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

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The health endpoint is `http://127.0.0.1:8000/api/health`.

## 5. Upload media

Upload videos to the `media` bucket in Supabase Dashboard → Storage. Subdirectories become library categories.

For preview generation, see [Preview generation script](preview_generation_script.md).

## 6. Deploy with nginx and HTTPS

Use `backend/nginx.conf.example` and replace the domain, checkout path, and certificate paths. A typical checkout is:

```bash
sudo mkdir -p /var/www
sudo git clone https://github.com/adilevy1011/streaming /var/www/media-streamer
```

Set `root /var/www/media-streamer;`. Proxy `/api/` to Uvicorn at `127.0.0.1:8000`; serve other paths from the repository root.

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d <YOUR_DOMAIN>
```

Never commit private keys or certificate directories.

## 7. Run as a system service

Edit `backend/uvicorn.service` for the server’s paths, user, and environment file. Protect the environment file:

```bash
sudo mkdir -p /etc/streaming
sudo cp backend/.env /etc/streaming/backend.env
sudo chmod 600 /etc/streaming/backend.env
```

The service should use values like:

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

View logs with `sudo journalctl -u media-streamer -f`.

## 8. Update the application

```bash
git pull
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
supabase db push
sudo systemctl restart media-streamer
sudo nginx -t && sudo systemctl reload nginx
```

If needed, run the preview worker separately as described in [Preview generation script](preview_generation_script.md).

## Troubleshooting

- **Missing Supabase variables:** verify `backend/.env` and the systemd `EnvironmentFile`.
- **Unauthorized login:** verify the user, password, email allowlist, email confirmation, and matching Supabase URL/key.
- **Empty library:** verify the `media` bucket, `MEDIA_BUCKET`, storage policies, and `/api/health`.
- **Missing previews:** see [Preview generation script](preview_generation_script.md).
- **nginx 502:** check `curl http://127.0.0.1:8000/api/health` and `sudo journalctl -u media-streamer -n 100 --no-pager`.

## Security checklist

- Never commit `.env`, service-role keys, passwords, JWT secrets, private keys, or certificates.
- Never put `SUPABASE_SERVICE_ROLE_KEY` on the frontend.
- Keep the Storage bucket private and use HTTPS in production.
- Restrict environment-file permissions.
- Review storage policies before opening the project to untrusted users.
- Back up Supabase data before destructive schema changes.
- Use separate Supabase projects for development and production.
