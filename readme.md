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
- [How to use](docs/how_to_use.md)
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

## How to build your own server

- You can build your own media streaming server by following the instructions in the [setup documentation](docs/setup.md). 
- Once you got your server and [Supabase](https://supabase.com) project up and running, follow the instructions here to understand [how to organize your database](docs/how_to_use.md).
- Once that's done, you can host your own videos and share them with whoever you want! For more instructions on how to create timeline previews and run credit detections you can check out the [scripts documentation](docs/scripts.md)