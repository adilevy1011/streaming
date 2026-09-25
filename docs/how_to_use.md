# How to Organize Your Database

After the server is set up, organize the files in the media bucket and the library will build its navigation automatically.

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

## Admin access and video permissions

The admin feature controls which authenticated users can see and play individual videos. It is implemented at both the Supabase database layer and the application API layer, so hiding a video from the library also prevents a user from opening it by guessing its storage path.

### Enabling an administrator

The `public.profiles` table includes an `admin_access` boolean column. It defaults to `false` for all existing and newly created profiles. Users cannot change this column through the normal profile API.

Promote an account to admin by changing its `admin_access` column to `true`.

### Controlling video access

The `public.videos` table includes a `user_access text[]` column. The value is an email allowlist:

- An empty list (`{}`) means the video follows each profile's `new_videos_access` setting.
- A non-empty list means only users whose email appears in that list can view and play the video.
- Emails are normalized to lowercase by the admin API.
- Admins always have access, regardless of `user_access`, as long as their profile has `admin_access = true`.

The `public.profiles` table also includes `new_videos_access`, which defaults to `true`. This controls access to videos whose `user_access` list is empty:

- When `new_videos_access` is `true`, the user automatically sees new videos that have no specific allowlist.
- When it is `false`, the user does not see those videos unless their email is explicitly added to a video's `user_access` list.

Administrators can change this setting from the Admin Actions panel by selecting a user and toggling **Automatically allow new videos**. It is saved together with any pending video permission changes. Admin users always retain access to all videos, and are also included in every explicit video allowlist so their explicitly granted access survives removal of admin status.

To manage permissions in the application, sign in as an administrator and click **Admin Actions** next to **Refresh library**. The panel shows every Auth user and every cataloged video. Select a user to see their effective access, then check or uncheck videos and hit save.

When an unrestricted video is deselected for one user, the application converts it into an allowlist containing the other non-admin users. This preserves the unrestricted-by-default behavior while allowing that user to be removed from the video.

### Security model

The database uses a `public.is_admin()` security-definer function and row-level security policies on `public.videos`. Authenticated users can only select permitted video rows, while administrators can select all video rows and update `user_access`.

The admin user list is returned through a restricted database function because `auth.users` is not directly readable by normal authenticated clients. It exposes only the user ID, email, admin status, and new-video access setting needed by the admin panel.

The backend also checks access before serving video files, subtitles, poster images, folder artwork, and generated preview sheets. This second check protects media even when a user has a direct or previously cached media URL.

No `SERVICE_ROLE` key is required for this implementation. Administrator promotion should still be performed through the Supabase dashboard or another trusted database connection, never through a client-controlled request.



### Disclaimer
- This setup technically does not require a pro subscription to supabase, but Supabase does have a limit of 50mg per file on free projects. So if you build this server using a free tier project you will not be able to host large video files.
