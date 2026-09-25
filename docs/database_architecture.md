# Database architecture

The application uses Supabase Auth, PostgreSQL, and Supabase Storage. PostgreSQL stores application metadata and indexes the media library; the media bytes remain in the private `media` Storage bucket.

## Logical model

```text
auth.users
    | 1:1 (user_id)
    +-- public.profiles
    |
    +-- public.video_progress (one row per user and media path)

storage.buckets: media (private)
    +-- source videos, subtitles, and artwork
    +-- __previews/<encoded media path>/... generated sprite sheets

public.media_objects  <-- catalog of non-generated bucket objects
    +-- public.videos  <-- one indexed row per source video

public.video_previews  <-- preview manifests, keyed by source video
public.video_credits   <-- detected credit ranges, keyed by source video
```

`media_path` and `path` contain Storage object paths and are the canonical media identifiers. They are intentionally not foreign keys to `storage.objects`: Storage is managed outside the public schema, and one source video can have multiple related preview objects.

Storage triggers keep the catalog synchronized with uploads, updates, renames, and deletes. A source-video delete also removes its related catalog, progress, preview, and credit rows.

## Catalog tables

### `public.media_objects`

This is the searchable catalog of non-generated objects in the `media` bucket. It contains videos, images, subtitles, and other objects. Objects below `__previews/` are excluded.

| Column | Type | Description |
| --- | --- | --- |
| `path` | `text` | Primary key and Storage object path |
| `name` | `text` | Object filename |
| `folder_path` | `text` | Parent folder, or an empty string |
| `kind` | `text` | `video`, `image`, `subtitle`, or `other` |
| `mime_type` | `text` | Storage MIME type, when available |
| `size_bytes` | `bigint` | Object size, when available |
| `created_at` | `timestamptz` | Storage creation time |
| `updated_at` | `timestamptz` | Storage update time |

Authenticated users can read the catalog. The application uses it for library indexing and folder artwork lookup.

### `public.videos`

This is the application-facing video index. Each row represents one source video and references its corresponding `media_objects` row with a cascading database foreign key.

| Column | Type | Description |
| --- | --- | --- |
| `path` | `text` | Primary key and source video Storage path |
| `name` | `text` | Video filename |
| `folder_path` | `text` | Parent folder |
| `mime_type` | `text` | Video MIME type, when available |
| `size_bytes` | `bigint` | Video size, when available |
| `created_at` | `timestamptz` | Storage creation time |
| `updated_at` | `timestamptz` | Storage update time |
| `subtitle_path` | `text` | Matching same-folder `.srt` path, when present |
| `preview_image_path` | `text` | Matching same-stem artwork path, when present |
| `preview_image_updated_at` | `timestamptz` | Matching artwork update time |
| `user_access` | `text[]` | Optional lower-case email allowlist |

Video visibility is enforced by RLS. Administrators can read and update video access lists. Other authenticated users can read a row when their email is explicitly listed, or when the list is empty and their profile permits access to new videos. Administrators bypass these restrictions. A trigger normalizes explicit lists and keeps current administrators included in non-empty lists.

## User and playback tables

### `public.profiles`

One row per Supabase Auth user. Rows are created for new Auth users and existing users without a profile.

| Column | Type | Default | Description |
| --- | --- | --- | --- |
| `user_id` | `uuid` | — | Primary key and cascading FK to `auth.users(id)` |
| `subtitles_enabled` | `boolean` | `false` | User subtitle preference |
| `admin_access` | `boolean` | `false` | Grants administrator capabilities |
| `new_videos_access` | `boolean` | `true` | Allows videos with an empty `user_access` list |

Users can read and change their own subtitle preference. They cannot promote themselves or change another user’s access settings. Administrators can read profiles and manage `new_videos_access`; administrator promotion is a trusted database/admin operation.

### `public.video_progress`

Per-user resumable playback checkpoints. The composite primary key `(user_id, media_path)` allows one checkpoint per user and source video.

| Column | Type | Default / constraint | Description |
| --- | --- | --- | --- |
| `user_id` | `uuid` | Part of primary key; cascading FK to `auth.users(id)` | Viewer |
| `media_path` | `text` | Part of primary key | Source video path |
| `position_seconds` | `double precision` | `0`, must be `>= 0` | Last playback position |
| `duration_seconds` | `double precision` | Nullable; if present, `> 0` | Known duration |
| `completed` | `boolean` | `false` | Whether playback is complete |
| `updated_at` | `timestamptz` | UTC `now()` | Last checkpoint update |

Authenticated users can select, insert, update, and delete only their own rows. The API upserts using `(user_id, media_path)`.

## Derived media metadata

### `public.video_previews`

One row per source video containing the manifest for generated timeline/library sprite sheets. The image bytes are Storage objects below `__previews/`.

| Column | Type | Default / constraint | Description |
| --- | --- | --- | --- |
| `media_path` | `text` | Primary key | Source video path |
| `sprite_prefix` | `text` | — | Prefix containing generated sheets |
| `sheets` | `jsonb` | `[]` | Ordered sheet object paths |
| `duration_seconds` | `double precision` | Must be `> 0` | Source duration |
| `interval_seconds` | `double precision` | `5`, must be `> 0` | Seconds between thumbnails |
| `columns` / `rows` | `integer` | `10`, must be `> 0` | Sheet layout |
| `thumbnail_width` | `integer` | `160`, must be `> 0` | Thumbnail width in pixels |
| `thumbnail_height` | `integer` | `90`, must be `> 0` | Thumbnail height in pixels |
| `updated_at` | `timestamptz` | UTC `now()` | Manifest update time |

All authenticated users can read and manage preview manifests. This supports the authenticated preview worker; the rows are not user-owned.

### `public.video_credits`

One row per source video containing the detected end-credit range.

| Column | Type | Default / constraint | Description |
| --- | --- | --- | --- |
| `id` | `uuid` | Primary key; generated UUID | Row identifier |
| `media_path` | `text` | Unique and indexed | Source video path |
| `credits_start_seconds` | `numeric` | Must be `>= 0` | Credit start |
| `credits_end_seconds` | `numeric` | Nullable; if present, `>=` start | Credit end |
| `confidence_score` | `numeric` | Nullable; between `0` and `1` | Detector confidence |
| `detected_via` | `text` | `local-opencv-easyocr-binary-search` | Detector identifier |
| `created_at` | `timestamptz` | `now()` | Initial detection time |
| `updated_at` | `timestamptz` | `now()` | Last detection update |

Authenticated users can read credit rows. A trusted worker or service-role process writes and removes them.

## Supabase Auth and Storage

`auth.users` is managed by Supabase Auth. The `profiles` and `video_progress` foreign keys use `ON DELETE CASCADE`, so deleting an Auth user removes that user’s profile and playback history.

The `media` bucket is private. Authenticated users can list and read objects. Generated preview objects are restricted to the `__previews/` path for authenticated insert, update, and delete operations. Source media is served through authenticated Storage requests and backend proxy endpoints.

The catalog is maintained by database functions and triggers on `storage.objects`:

- `media_catalog_kind` classifies bucket objects.
- `refresh_video_catalog` derives subtitle and same-stem artwork references.
- `sync_media_object_catalog` synchronizes non-generated bucket objects and refreshes affected videos.
- `cleanup_video_references_on_storage_delete` removes database references when a source video is deleted.

## Security model

- RLS is enabled on all application tables in `public`; an event trigger enables it for subsequently created public tables.
- Application access is authenticated through Supabase Auth. The anonymous role has no application-table grants.
- Profiles and playback rows are user-scoped with `auth.uid()` checks.
- Video visibility is controlled by `admin_access`, `new_videos_access`, and `videos.user_access`.
- Preview metadata is readable and writable by authenticated users for the fallback worker; credit metadata is readable by authenticated users and written by trusted services.
- Storage remains private, and generated-object write policies are limited to `__previews/`.
