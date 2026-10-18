# ADLV Media Manager

Native desktop media and permissions manager.

## Run

From the repository root:

```powershell
python media_manager/app.py
```

The small `app.py` launcher starts the implementation in `dashboard.py`. The
standalone preview and credit tools live beside it as `preview_generator.py`
and `credit_detector.py`.

## Module layout

- `app.py` — launcher
- `dashboard.py` — application shell, data loading, and save coordination
- `credentials.py` — local `.env` setup and credential editor
- `supabase_client.py` — Supabase REST and storage client
- `permissions_view.py` — people and video access workspace
- `ordering_view.py` — playback ordering workspace
- `uploads_view.py` and `uploads_actions.py` — storage browser and file actions
- `media_view.py` — video-specific manager and local script controls

On first launch, enter the Supabase project URL, secret/service-role key, and storage bucket. The app saves them to `media_manager/.env`, which is ignored by git. Subsequent launches open directly into the manager. The dashboard never reads the repository's other `.env` files.

The manager provides:

- People & access: search users, filter videos, allow/block individual videos, bulk allow/clear, and control access to new videos.
- Playback order: choose a folder and move items up or down before saving.
- Manage uploads: browse storage folders, create folders, upload media and subtitles, rename items, and delete files or folders.
- Media manager: right-click a video to edit subtitles, inspect timeline/credit metadata, set a credits timestamp, manage artwork, and view the repository scripts' live output.

The Generate previews and Detect credits actions launch the standalone `preview_generator.py` and `credit_detector.py` files inside this directory. They receive the URL, key, bucket, and video path as arguments and run locally.

The app uses Tkinter and Python's standard library, so no additional package is required.

Credit detection additionally uses the optional computer-vision stack. Install
it from the media manager directory when you want to run that tool:

```powershell
python -m pip install -r media_manager/requirements.txt
```

The bundled MobileNet checkpoint is stored at `media_manager/models/credit_mobilenet.pt`.
