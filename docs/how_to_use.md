# How to Organize Your Database

After the server is set up, organize the files in the media bucket and the library will build its navigation automatically.

> **Free-tier storage limit:** This setup does not require a Supabase Pro subscription, but free projects limit individual files to **50 MB**. If you use a free-tier project, you will not be able to host large video files.

## Organizing folders

Every first-level bucket folder that contains video files becomes a tab in the frontend. Folder names are used as the tab names, with the first letter capitalized. The names do not need to be `movies` or `shows`.

Subfolders are displayed as folder cards inside their parent tab. You can continue nesting folders, and each level is browsable in the same way. A folder containing only one video is flattened so that the video appears directly at the parent level.

For example:

```text
media bucket/
├── films/
│   └── Inception/
│       ├── Inception.mp4
│       ├── Inception.srt
│       └── Inception.png
└── documentaries/
    └── Nature/
        ├── Episode 1.mp4
        └── Episode 2.mp4
```

This creates `Films` and `Documentaries` tabs. `Inception` is shown directly as one video because its folder contains one video. `Nature` is shown as a folder because it contains a video collection.

Folders that contain no supported video files do not become tabs.

## Playback order

Every folder has a stored playback order for its direct videos and subfolders. Playback recursively enters a folder, plays all of its videos, then continues with the next item after that folder in the parent order. New items are inserted using a number immediately before the extension when available; otherwise they are inserted alphabetically without reordering existing items.

The media manager can choose a folder under **Playback order**, drag its items into the desired order, and save. At the end of a video, **Play next** shows the next permitted video and its matching preview image when available. Videos hidden from a user are skipped. The final video shows **Back to library** instead.

## Subtitles

Place an `.srt` subtitle file beside its video and give it the same base name:

```text
Episode 1.mp4
Episode 1.srt
```

Subtitle matching is case-insensitive. The subtitle must be in the same folder as the video. The player discovers it automatically when the video is opened.

## Artwork and previews

An image beside a video is used as that video’s preview when its base name matches:

```text
Inception.mp4
Inception.png
```

Supported artwork formats include `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, and `.avif`. The image is shown without cropping; if it is unavailable, the generated sprite preview is used for the video. [See how to generate sprite previews](scripts.md)

An image beside a folder is used as that folder’s card artwork. The image must match the folder name:

```text
films/
├── Nature/
└── Nature.jpg
```

Folder artwork is inherited from the folder’s parent listing and is shown only when the matching image exists. If it cannot be loaded, a regular folder card is displayed.

Artwork is cached for faster loading. The application checks the bucket’s current image timestamp and automatically refreshes cached artwork when the image changes.

## Video permissions

The media manager controls which authenticated users can see and play individual videos. Visibility is enforced at the Supabase database layer and by the application media API, so hiding a video from the library also prevents a user from opening it by guessing its storage path.

### Controlling video access

The `public.videos` table includes a `user_access text[]` column. The value is an email allowlist:

- An empty list (`{}`) means the video follows each profile's `new_videos_access` setting.
- A non-empty list means only users whose email appears in that list can view and play the video.
- Emails are normalized to lowercase by the media manager.

The `public.profiles` table also includes `new_videos_access`, which defaults to `true`. This controls access to videos whose `user_access` list is empty:

- When `new_videos_access` is `true`, the user automatically sees new videos that have no specific allowlist.
- When it is `false`, the user does not see those videos unless their email is explicitly added to a video's `user_access` list.

The media manager can change this setting by selecting a user and toggling **Automatically allow new videos**. It is saved together with pending video permission changes.

To manage permissions, open the media manager. It shows every Auth user and every cataloged video. Select a user to see their effective access, then check or uncheck videos and save.

When an unrestricted video is deselected for one user, the application converts it into an allowlist containing the other users. This preserves the unrestricted-by-default behavior while allowing that user to be removed from the video.

### Security model

The database uses row-level security policies on `public.videos`. Authenticated users can only select permitted video rows.

The media manager reads the Auth user list through its trusted Supabase connection and uses the user ID, email, and new-video access setting needed for permission management.

The backend also checks access before serving video files, subtitles, poster images, folder artwork, and generated preview sheets. This second check protects media even when a user has a direct or previously cached media URL.

The media manager keeps its secret key local and never exposes it to the browser.



