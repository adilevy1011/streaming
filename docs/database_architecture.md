# Database architecture

This project uses Supabase Auth, PostgreSQL, and Supabase Storage. The database stores application metadata; video, subtitle, and preview image bytes live in the private `media` storage bucket.

## Logical model

```text
auth.users
    | 1:1 (user_id)
    +-- profiles
    |
    +-- video_progress (one row per user and media path)

storage.buckets: media
    +-- source media and subtitles
    +-- __previews/<encoded media path>/... sprite sheets
          | logical media_path match
          +-- video_previews
          +-- video_credits

public.media_objects  <-- synchronized catalog of bucket objects
    +-- public.videos  <-- indexed video catalog used by the application
```

`media_path` is the canonical storage object path used by the application. It is deliberately not a PostgreSQL foreign key because Supabase Storage objects are managed in the `storage` schema and a single video can have many related preview objects. Workers reconcile database rows with storage contents when generating previews and detecting credits.

The media catalog is the exception: `public.media_objects` mirrors non-generated objects in the `media` bucket, and `public.videos` contains one enriched row per video. Triggers on `storage.objects` handle uploads, deletes, renames, and metadata changes. When a source video is deleted, a cleanup trigger also removes its rows from `videos`, `media_objects`, `video_progress`, `video_previews`, and `video_credits`. The migration also backfills the catalog from existing Storage objects. Storage remains the source of truth for bytes; the catalog is the source of truth for library discovery.

### `public.videos`

The indexed application catalog. Its primary key is the Storage object path. It also stores the matching subtitle and same-name artwork paths so normal library and playback lookups do not need to list Storage.

### `public.media_objects`

The supporting catalog of videos, images, subtitles, and other bucket objects. It is used to maintain sibling metadata and folder artwork while excluding generated objects below `__previews/`.

## Public tables

### `public.profiles`

Per-user preferences. The table is keyed by the Supabase Auth user ID.

| Column | Type | Null | Default / constraint | Description |
| --- | --- | --- | --- | --- |
| `user_id` | `uuid` | No | Primary key; FK to `auth.users(id)` with `ON DELETE CASCADE` | Owner of the profile |
| `subtitles_enabled` | `boolean` | No | `false` | Whether subtitle display is enabled by default |

Authenticated users can select, insert, and update only their own profile. There is no delete grant or delete policy.

### `public.video_progress`

Per-user resumable playback checkpoints. The composite key allows each user to have one checkpoint per storage path.

| Column | Type | Null | Default / constraint | Description |
| --- | --- | --- | --- | --- |
| `user_id` | `uuid` | No | Part of primary key; FK to `auth.users(id)` with `ON DELETE CASCADE` | Viewer |
| `media_path` | `text` | No | Part of primary key | Source video path in the `media` bucket |
| `position_seconds` | `double precision` | No | `0`; must be `>= 0` | Last playback position |
| `duration_seconds` | `double precision` | Yes | Must be `NULL` or `> 0` | Duration known by the client |
| `completed` | `boolean` | No | `false` | Whether playback is complete |
| `updated_at` | `timestamptz` | No | UTC `now()` | Last checkpoint write |

Authenticated users can select, insert, update, and delete only rows whose `user_id` equals `auth.uid()`. The API upserts using the `(user_id, media_path)` conflict key.

### `public.video_previews`

Manifest metadata for timeline and library preview sprite sheets. The actual sheets are storage objects under `__previews/`.

| Column | Type | Null | Default / constraint | Description |
| --- | --- | --- | --- | --- |
| `media_path` | `text` | No | Primary key | Source video path |
| `sprite_prefix` | `text` | No | — | Prefix containing generated sprite sheets |
| `sheets` | `jsonb` | No | `[]` | Ordered sprite-sheet object paths |
| `duration_seconds` | `double precision` | No | Must be `> 0` | Source duration |
| `interval_seconds` | `double precision` | No | `5`; must be `> 0` | Seconds represented between thumbnails |
| `columns` | `integer` | No | `10`; must be `> 0` | Sprite-sheet columns |
| `rows` | `integer` | No | `10`; must be `> 0` | Sprite-sheet rows |
| `thumbnail_width` | `integer` | No | `160`; must be `> 0` | Thumbnail width in pixels |
| `thumbnail_height` | `integer` | No | `90`; must be `> 0` | Thumbnail height in pixels |
| `updated_at` | `timestamptz` | No | UTC `now()` | Manifest update time |

RLS is enabled. Authenticated users can read manifests. The later preview-generation migration also grants authenticated users full table DML and permits them to write rows when `auth.role() = 'authenticated'`; this supports the authenticated fallback worker. Preview rows are not user-owned.

### `public.video_credits`

Detected end-credit ranges for each source video.

| Column | Type | Null | Default / constraint | Description |
| --- | --- | --- | --- | --- |
| `id` | `uuid` | No | Primary key; `gen_random_uuid()` | Row identifier |
| `media_path` | `text` | No | Unique; indexed | Source video path |
| `credits_start_seconds` | `numeric` | No | Must be `>= 0` | Start of detected credits |
| `credits_end_seconds` | `numeric` | Yes | `NULL` or `>= credits_start_seconds` | End of detected credits |
| `confidence_score` | `numeric` | Yes | `NULL` or between `0` and `1` | Detector confidence |
| `detected_via` | `text` | Yes | `local-opencv-easyocr-binary-search` | Detector/model identifier |
| `created_at` | `timestamptz` | Yes | `now()` | Initial detection time |
| `updated_at` | `timestamptz` | Yes | `now()` | Last detection update time |

Authenticated users can read credit rows. The trusted credit-detection worker writes/upserts and removes stale rows using its service credentials; ordinary authenticated users have no write grant in the migrations. `detected_via` was initially defaulted to `gemini-2.5-flash`, then `gemini-3.6-flash`, and the final migration changes the default to the local OpenCV/EasyOCR detector.

## Supabase Auth and Storage

### Auth

`auth.users` is managed by Supabase Auth and is outside the application migrations. `profiles.user_id` and `video_progress.user_id` reference it with cascading deletes, so removing an Auth user removes their profile and playback history.

### Private `media` bucket

The remote-schema migration creates a non-public bucket named `media`. Authenticated users may list and read objects in that bucket. Application media is discovered from storage rather than from a database table, so folders and file paths are the library catalog.

Generated preview sheets are stored below `__previews/`. Authenticated preview workers may insert, update, and delete only objects whose names match `^__previews/`; this keeps worker cleanup scoped away from source media. Preview sheet access still uses the authenticated read policy on the bucket.

## Security model

- RLS is enabled on every application table in `public`. The remote schema also installs an event trigger that automatically enables RLS on subsequently created public tables.
- The `anon` role has no application-table grants in these migrations. Normal API access is authenticated through Supabase Auth and the backend forwards the user's access token to Supabase.
- User-owned data (`profiles`, `video_progress`) is restricted with `auth.uid() = user_id`.
- Shared metadata (`video_previews`, `video_credits`) is readable by any authenticated user. Preview metadata is also writable by authenticated users to support the fallback worker; credit metadata is read-only for that role.
- Source and preview files remain in a private storage bucket and are served through authenticated Storage requests/backend proxy endpoints.

## Migration sources

The current schema is defined by the migrations in [`supabase/migrations`](../supabase/migrations/):
