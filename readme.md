# ADLV Media Streamer

A self-hosted media player built with a static frontend, a FastAPI backend, Supabase Auth, Supabase Storage, and PostgreSQL.

The project combines a browser-based media library and player with a FastAPI backend and Supabase services for authentication, storage, and playback data. It can be run locally or deployed behind nginx with HTTPS.

## Architecture

```text
Browser
  |
  | HTTPS
  v
nginx
  |-- static files: frontend/index.html, frontend/watch.html, frontend/*.js
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

## Documentation

- [Setup and deployment](docs/setup.md)
- [Database Architecture](docs/database_architecture.md)
- [Scripts](docs/scripts.md)

## Repository layout

```text
backend/
  main.py                  FastAPI application
  requirements.txt         Python dependencies
  .env                     Local/private configuration, not committed
  nginx.conf.example       Safe nginx template
scripts/
  generate_previews.py     Preview generation and cleanup worker
  detect_credits.py        Finds the timestamp the credits start
supabase/
  migrations/              Database, RLS, bucket, and storage policies
  config.toml               Local Supabase CLI configuration
frontend/
  index.html                Main library page
  watch.html                Video player page
  app.js, watch.js, api.js  Frontend application code
```
