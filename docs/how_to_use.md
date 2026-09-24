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

### Disclaimer
- This setup technically does not require a pro subscription to supabase, but Supabase does have a limit of 50mg per file on free projects. So if you build this server using a free tier project you will not be able to host large video files.