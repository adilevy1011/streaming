# HLS Upload Optimization and Reliability Plan

## Purpose

This document defines a safe, incremental plan for improving HLS generation and uploading in `media_manager`.

The goals are to:

1. Report the complete upload lifecycle accurately.
2. Reduce unnecessary data transfer and total processing time.
3. Make HLS uploads retryable and resumable after transient failures.
4. Preserve playback correctness while improving throughput.
5. Keep each change small enough to test and roll back independently.

The phases are ordered by dependency, but each phase should first be tested with a short fixture, then a medium fixture, and finally a representative long movie before it is enabled as the default behavior.

## Scope and non-goals

### In scope

- The media-manager upload queue and HLS generator.
- HLS segment upload performance, retries, recovery, and progress reporting.
- Supabase Storage object discovery and catalog publication rules.
- Backend media catalog filtering and HLS/original playback selection.
- Frontend fallback behavior and Recently Added visibility.
- Database migrations and repair procedures required to hide partial HLS work.

### Not in scope

- Changing authentication or media-access policy semantics.
- Changing the public HLS URL shape without a separate compatibility plan.
- Supporting multiple HLS renditions or adaptive-bitrate master playlists in this effort.
- Automatically deleting user media as part of recovery.
- Treating an incomplete HLS rendition as independently playable.

## Implementation map

The future implementation should be organized around these existing files and responsibilities:

| Area | File(s) | Responsibility |
| --- | --- | --- |
| Queue/UI | `media_manager/uploads_actions.py`, `media_manager/uploads_view.py` | Upload phases, queue state, progress display, pause/cancel, subprocess lifecycle, recovery visibility |
| HLS worker | `media_manager/hls_generator.py` | FFmpeg invocation, local staging, segment discovery, retries, checkpoints, manifest publication |
| Storage client | `media_manager/supabase_client.py` | Original-file resumable uploads, paginated storage listing, shared storage error handling where appropriate |
| Catalog migrations | `supabase/migrations/*.sql` | Classify HLS segments as non-catalog objects, repair stale rows, publish only canonical videos |
| Catalog API | `backend/core.py`, `backend/routes/catalog_routes.py` | Exclude staging objects, choose canonical original/HLS rows, preserve access and metadata |
| HLS serving | `backend/hls.py`, `backend/routes/media_routes.py` | Read complete manifests, reject incomplete renditions, serve segments, preserve cache behavior |
| Playback | `frontend/js/hls-player.js`, `frontend/js/app.js`, `frontend/js/watch.js` | HLS attempt, original fallback, Recently Added filtering, stable progress identity |
| Documentation | `media_manager/README.md`, `docs/scripts.md` | Explain phases, recovery, defaults, and operator actions |

The implementation should not duplicate catalog or playback rules in only one layer. Database classification is the primary protection; backend filtering and frontend behavior are defense in depth.

## Artifact and identity contract

The implementation must preserve these paths and meanings:

- Original source: `<folder>/<name>.<ext>`.
- HLS prefix: `<folder>/<stem>-hls/`.
- HLS manifest: `<folder>/<stem>-hls/index.m3u8`.
- HLS segments: `<folder>/<stem>-hls/segment-00000.ts`, etc.
- Canonical library path when the original exists: the original source path.
- Canonical library path for HLS-only media: the HLS manifest path, exposed as a virtual video with the original stem.

An HLS job is complete only when all of the following are true:

1. The manifest exists remotely.
2. The manifest is a valid VOD playlist with `#EXT-X-ENDLIST`.
3. Every segment named by the manifest exists remotely and matches the expected metadata.
4. The catalog exposes either the original path or the completed manifest path, never a segment path.

The manifest is the publication marker, but completion validation must still inspect its referenced segment set. A mere `GET` success for `index.m3u8` is not sufficient.

Every generated artifact set must be tied to an HLS format identity. Define one shared format/version value for the media manager and backend. It must change whenever encoding, segment naming, playlist semantics, or compatibility assumptions change. Do not rely on the existing `cache_key()` functions alone: the generator's cache-key helper is not the remote completion record.

The final manifest should carry non-playback metadata comments for the format identity and source identity, for example:

```text
#ADLV-HLS-FORMAT=hls-v6
#ADLV-SOURCE-SHA256=<digest>
```

These comments are valid HLS comments and do not affect playback. On a rerun, an existing manifest may be treated as complete only if its metadata, playlist structure, and referenced segments match the requested source and format. A legacy manifest without these markers must be validated conservatively and must not be assumed to match a newly selected replacement source.

## Non-negotiable invariants

- HLS segments are storage implementation objects, never library videos.
- An incomplete HLS rendition is never a canonical media item.
- A manifest is uploaded only after all referenced segments are uploaded and verified.
- A failed or cancelled job never publishes a new manifest.
- If an original exists, it remains the canonical library and playback-progress path.
- HLS-only media becomes visible only when its completed manifest is published.
- Recovery must tolerate more than 1,000 objects and paginated storage listings.
- Re-running a job must be safe and idempotent.
- A stale local checkpoint must never override current remote storage state.

## Current behavior and observed problems

### Current workflow

For `Upload original + HLS`, the current queue does the following:

1. Uploads the original video through the resumable upload implementation.
2. Marks the normal upload progress as complete.
3. Starts `hls_generator.py` as a subprocess.
4. The HLS generator downloads the original video back from storage.
5. FFmpeg transcodes the source to one H.264/AAC VOD rendition.
6. Each `.ts` segment is uploaded sequentially with a separate HTTP request.
7. The manifest is uploaded last.
8. The queue is marked complete only after the subprocess exits successfully.

Before generation, the worker treats any remotely readable manifest as "HLS already exists" and skips the job. It does not currently validate that the manifest is complete or that every referenced segment is present.

`Upload HLS only` avoids the storage download because it passes the local source file to the generator. `Upload original + HLS` does not currently reuse the local source.

### Completion reporting issue

The normal upload progress callback is only passed to the original-file upload. HLS generation and segment uploading run inside a subprocess and do not send progress events back to the queue.

The UI therefore has no determinate progress for:

- FFmpeg transcoding.
- HLS segment uploading.
- Manifest finalization.

The subprocess output is captured until the process exits, so useful progress messages are not visible while the job is running.

Pause and cancel events are passed to the original-file upload but not into the HLS subprocess. Pressing pause or cancel during HLS work therefore does not reliably stop FFmpeg or segment uploads.

Relevant implementation areas:

- `uploads_actions.py`: queue lifecycle and subprocess launch.
- `hls_generator.py`: transcoding and segment uploads.
- `supabase_client.py`: resumable original-file upload.

### Throughput and reliability issue

The HLS generator currently uploads segments in a serial loop. A movie lasting approximately 2 hours 22 minutes can produce roughly 1,400 segments at a nominal 6-second segment duration. Each segment requires a separate blocking upload request.

The HLS segment uploader currently has:

- No bounded parallelism.
- No retry policy.
- No durable checkpoint of completed segments.
- No recovery of a partially completed HLS run.
- No cleanup policy for abandoned partial segment folders.
- Storage discovery is not currently designed as a paginated reconciliation operation; the existing manager listing helper requests only one page of up to 1,000 objects.

A single transient timeout or server error can fail the entire HLS phase after most of the work has already completed.

## Target end-state workflows

### Original only

1. Upload the original through the existing resumable path.
2. Report original upload completion.
3. Publish the original as the canonical library item.

### Original plus HLS

1. Validate and fingerprint the local source.
2. Upload the original through the resumable path.
3. Confirm the original upload completed.
4. Generate HLS from the retained local source; do not download the original again unless the local source is unavailable.
5. Reconcile any existing HLS work for the same destination, source identity, and format version.
6. Upload missing/invalid segments with bounded retries and concurrency.
7. Validate the complete segment set.
8. Upload the final manifest with source and format metadata.
9. Keep the original path as the canonical library/playback/progress path.

### HLS only

1. Validate and fingerprint the local source.
2. Generate HLS locally.
3. Reconcile any existing HLS work for the destination, source identity, and format version.
4. Upload missing/invalid segments with bounded retries and concurrency.
5. Validate the complete segment set.
6. Upload the final manifest with source and format metadata.
7. Publish the manifest as the HLS-only virtual library item.

### Resume or recovery

1. Identify the destination, HLS prefix, source identity, and format version.
2. Discover the local checkpoint if present.
3. Discover and paginate all remote objects under the HLS prefix.
4. Validate any existing manifest and its referenced segments.
5. Reconcile verified, missing, invalid, and unexpected objects.
6. Upload only missing or invalid expected segments.
7. Finalize the manifest only after the expected set is complete.
8. Leave unexpected objects untouched until an explicit cleanup operation is requested.

## Safety principles

The implementation should follow these rules throughout all phases:

- Keep the manifest as the completion sentinel and upload it last.
- Never publish a manifest that references missing segments.
- Preserve the existing storage path layout: `<video-stem>-hls/index.m3u8` and `segment-*.ts`.
- Use bounded concurrency rather than unlimited parallel requests.
- Make retries idempotent; retrying a segment must not create duplicate logical objects.
- Do not delete remote objects automatically until cleanup behavior is explicitly tested.
- Preserve pause and cancel behavior for the original upload and define equivalent behavior for HLS work.
- Keep the current sequential uploader available as a fallback during rollout.
- Record enough information to identify the exact phase and segment that failed.
- Test with a short fixture before testing with a long movie.

## Phase 1: Phase-aware progress and diagnostics

### Objective

Make the entire job observable before changing its performance characteristics.

### Implementation steps

1. Define explicit job phases:

   - `Uploading original`
   - `Preparing source`
   - `Generating HLS`
   - `Uploading HLS segments`
   - `Finalizing HLS manifest`
   - `Complete`
   - `Failed`
   - `Paused`
   - `Cancelled`

2. Replace the current single percentage interpretation with a progress model that includes:

   - Current phase.
   - Phase percentage.
   - Overall percentage.
   - Bytes or segments completed.
   - Total bytes or segments where known.
   - Current speed and estimated remaining time where measurable.

   Keep queue status and progress state separate. A paused or failed job must retain its last measured progress rather than being reset to zero.

3. Make the HLS subprocess emit machine-readable progress events rather than only human-readable lines.

   Use a versioned event envelope so the parent process can reject malformed or unknown events safely. Each event should include a job identifier, phase, event type, timestamp, and relevant numeric fields.

4. Stream subprocess output to the queue instead of waiting for the process to finish.

   Keep stdout reserved for structured events and send human-readable diagnostics to stderr or a separate log channel. Do not parse progress from localized or version-dependent FFmpeg display text.

5. Parse FFmpeg progress using a stable progress format based on media time, not by scraping human-readable FFmpeg log text.

6. Report segment upload progress after each successful segment.

7. Include the failing phase, object path, HTTP status, and retry count in errors.

8. Ensure the UI does not show `100%` until the manifest upload succeeds.

9. Define explicit subprocess termination behavior for pause, cancel, manager close, and unexpected parent-process termination. The parent must be able to terminate FFmpeg and wait for the child before changing queue state.

### Progress calculation

The phase progress is authoritative. The UI must not present a guessed overall percentage as though it were exact while the HLS output size and segment count are unknown. During source preparation and early transcoding, show the current phase and an indeterminate or explicitly estimated overall state. Once the segment set is known, show exact HLS segment/byte progress.

When measurable, use:

- Original upload: bytes uploaded / original size.
- Transcoding: elapsed media time / source duration.
- HLS upload: uploaded segment bytes / total segment bytes.
- Manifest: zero until started, then complete when uploaded.

If an overall estimate is displayed, label it as estimated and ensure it never moves backward when changing phases. The final transition to `100%` must be reserved for successful manifest publication.

Use named configuration values rather than hidden constants for rollout tuning. Recommended initial defaults are:

- `HLS_UPLOAD_WORKERS=1` during the baseline and Phase 1.
- `HLS_UPLOAD_WORKERS=4` for the first parallel trial.
- `HLS_MAX_RETRIES=5` for transient segment failures.
- Exponential backoff starting at 1 second, capped at 30 seconds, with jitter and `Retry-After` support.
- `HLS_FORMAT_VERSION` should initially match the current `hls-v6` artifact contract for behavior-preserving work. Bump it deliberately (for example, to `hls-v7`) only when encoding, segment naming, playlist semantics, or compatibility assumptions change; keep the media manager and backend values synchronized.

### Acceptance criteria

- A test upload visibly moves through every applicable phase.
- A long transcode displays progress and does not appear idle.
- HLS segment progress advances independently of original-file progress.
- The UI identifies the current segment or at least completed/total segments.
- A forced FFmpeg failure identifies `Generating HLS` as the failed phase.
- A forced segment failure identifies the segment path and HTTP error.
- Completion is impossible until the manifest upload returns successfully.

### Rollback boundary

This phase should not change encoding settings, segment duration, upload concurrency, or storage paths. If the new progress parser is unreliable, revert only the reporting changes and retain the existing upload behavior.

## Phase 2: Reuse the local source

### Objective

Avoid downloading the original video from Supabase immediately after uploading it.

### Implementation steps

1. For `Upload original + HLS`, pass the selected local source path to the HLS generator.

2. Keep the destination path as the logical video path used to derive the HLS folder.

3. Validate that the local file still exists before starting HLS generation.

4. If the local file is unavailable, fail clearly or use the existing remote-download fallback.

5. Report whether the source came from the local file or from storage.

6. Do not remove the remote fallback until the local-source path has been tested on all supported upload modes.

7. Capture a source identity before HLS generation begins. At minimum record file size and modification time; for recovery and cross-run matching, use a content hash or another collision-resistant fingerprint.

8. If the local source changes after the original upload, stop rather than generating an HLS rendition that does not match the uploaded original.

### Expected benefit

This removes one full download of the source from storage and reduces both elapsed time and exposure to a second network failure.

It does not eliminate the need to upload the original first when the user selected `original + HLS`; it only avoids downloading that same file again for transcoding.

### Acceptance criteria

- `original + HLS` does not issue a source download when the local source is available.
- `HLS only` continues to work.
- The generated HLS output is byte-valid and playable.
- A missing local source produces a controlled, actionable error.
- The original upload remains resumable and independent of HLS generation.

## Phase 3: Retryable, bounded-parallel segment uploads

### Objective

Reduce upload time while preventing a single transient network or storage error from failing the entire job.

### Implementation steps

1. Keep the current sequential upload path as a feature-controlled fallback.

2. Add a configurable worker count, initially defaulting to a conservative value such as 4.

3. Upload segments through a bounded worker pool.

4. Aggregate progress from completed segment bytes, not from submitted work.

5. Add retry handling for transient conditions:

   - Connection resets.
   - DNS or transport errors.
   - Timeouts.
   - HTTP 408.
   - HTTP 429.
   - HTTP 5xx responses.

6. Use exponential backoff with jitter and a maximum retry count.

7. Respect `Retry-After` when the server provides it.

8. Do not retry permanent client errors such as invalid credentials, malformed paths, or authorization failures.

9. Include the segment name and final response details in the failure message.

10. Upload the manifest only after every segment succeeds.

11. Make concurrency and retry counts configurable without requiring a code change for each test.

12. Use the same idempotent object path for retries and avoid changing segment names based on worker order.

13. Do not let an upload worker write catalog or publication state. Only the finalization step may publish completion state.

14. Make the storage endpoint choice explicit. The current original-file resumable upload uses `storage_url`, while `hls_generator.py` builds HLS object URLs from the project URL. Benchmark and document the selected endpoint, authentication headers, and upload method rather than changing hosts implicitly during the optimization.

### Concurrency safety

The worker count must be low enough to avoid storage throttling. Test progressively:

1. Sequential baseline.
2. Two workers.
3. Four workers.
4. Eight workers only if four workers remain stable and improve throughput.

Stop increasing concurrency when throughput stops improving or when 429/5xx rates increase.

### Acceptance criteria

- A long test upload completes faster than the sequential baseline.
- Injected transient failures are retried automatically.
- A permanent failure stops the job with the exact segment identified.
- The manifest is never uploaded after an incomplete segment set.
- Progress remains monotonic and accurate with multiple workers.
- The queue remains responsive during parallel uploads.
- No duplicate catalog entries or unexpected paths are created.

## Phase 4: Checkpoint and resume behavior

### Objective

Allow a failed or interrupted HLS job to continue from completed work rather than restarting all segment uploads.

This phase also covers recovery of the user's already-partially-uploaded movie. The recovery path must work even when the earlier run predates checkpoint support and only left remote segment objects behind.

### Recovery of the existing partial upload

The failed upload should be treated as recoverable work, not automatically discarded. Because the original local movie is still available, the manager can reconstruct the expected HLS segment set and reconcile it with the objects already present in Supabase.

The recovery flow should be:

1. Derive the expected HLS prefix from the destination video path.
2. Check whether the final manifest exists.
3. If the manifest exists, parse and validate it before treating the job as complete. Require a VOD playlist, `#EXT-X-ENDLIST`, safe `segment-*.ts` references, and a complete remote set for every reference.
4. If the manifest does not exist, list the objects currently present under the HLS prefix.
5. Use the retained local source to generate the expected local HLS output, or otherwise recover the expected manifest and segment list.
6. Compare expected segment names against remote segment names.
7. Compare sizes and, where practical, hashes or validated downloads for remote segments that appear to be present.
8. Mark only verified remote segments as complete.
9. Upload missing or invalid segments, using the retry and bounded-concurrency rules from Phase 3.
10. Upload the manifest last after every expected segment has been verified.
11. Record that the job was recovered from a partial upload and report how much work was reused.

The first recovery implementation may need to transcode the local movie again to reconstruct the segment files. That is still preferable to re-uploading the already-completed half of the HLS set. A later optimization can preserve a local staging directory or use a durable job manifest so that recovery does not require another transcode.

The remote object listing must be paginated. A long movie can exceed the current 1,000-object listing limit, so recovery must continue requesting pages until a page is shorter than the page size. It must also handle the case where an object is added or removed while listing by reconciling against the generated manifest rather than assuming list order is stable.

### How to determine upload progress safely

Remote object presence alone is not sufficient proof that a segment is usable. The reconciliation process should account for:

- Expected filename.
- Expected byte size.
- Content type where available.
- Object metadata such as ETag or checksum where supported.
- A successful HEAD/list response rather than a stale local assumption.
- The fact that a segment may have been interrupted before the storage service completed the object.

Prefer list metadata for the initial reconciliation to avoid downloading every segment. Use targeted HEAD or ranged validation for suspicious objects. A full content hash of every remote segment is optional and should be reserved for mismatched metadata or a diagnostic mode because it can erase the performance benefit of recovery.

The manager should show a recovery summary before continuing, for example:

```text
Existing HLS work found
Verified segments: 712 / 1,421
Missing or invalid segments: 709
Manifest: not published
Action: upload missing segments and finalize manifest
```

The recovery summary should be informational and should not require the user to manually calculate which segment number failed.

### Recovery safety rules

- Never publish an existing or newly generated manifest until all referenced segments are verified.
- Do not infer completion solely from the highest segment number; there may be gaps.
- Do not trust a partial manifest as a completion marker.
- Treat a remote segment with an unexpected size as invalid and re-upload it.
- Preserve the user's local source while recovery is running; validate it before starting.
- Make the recovery operation resumable itself.
- If the local source differs from the original source or the HLS format version changed, require a fresh generation rather than mixing segment sets.

### Recommended design

Use the manifest as the published-complete marker and maintain a separate local checkpoint while the job is running.

The checkpoint should contain at least:

- Source identity, such as destination path and source fingerprint.
- HLS format/cache version.
- Segment names.
- Expected segment sizes.
- Successfully uploaded segment names and sizes.
- Current phase.
- Last update time.

Store checkpoints outside the repository and outside the temporary FFmpeg directory, using the platform's application-data directory. Write them atomically so a process interruption cannot leave a valid-looking but truncated checkpoint. Never place API keys or access tokens in the checkpoint.

The checkpoint must not be treated as proof that the remote object exists after an unclean shutdown. On resume, verify checkpointed objects or use a safe existence check before skipping them.

### Implementation steps

1. Use a stable job identifier for the local checkpoint.

2. Record a segment only after the storage request succeeds.

3. Flush checkpoint updates periodically and after each completed segment batch.

4. On restart, detect an incomplete checkpoint for the same source and HLS version.

5. Reconcile checkpoint state with remote storage before skipping segments.

6. Re-upload missing or mismatched segments.

7. Generate or reuse the manifest only after all expected segments are present.

8. Delete or archive the checkpoint only after manifest finalization succeeds.

9. Provide a controlled way to discard an abandoned job and regenerate from scratch.

10. Keep recovery and normal generation on the same segment naming, manifest, retry, and finalization code paths. Recovery should differ only in its initial set of already-verified segments.

### Partial remote objects

Failed jobs may leave segment objects in the HLS folder. The first implementation should not automatically delete them during recovery. Once resume behavior is proven, add a separate cleanup path for objects that are not referenced by the final manifest and do not belong to an active job.

### Acceptance criteria

- Interrupting after 25%, 50%, and 90% allows the job to resume.
- Resume skips verified completed segments.
- Deleted or corrupted checkpointed segments are regenerated.
- An old checkpoint from a different source or HLS version is not reused.
- A resumed job still uploads the manifest last.
- A completed manifest causes a subsequent run to skip the entire HLS job as it does today.

## Cross-system consistency: catalog visibility and playback fallback

### Problem discovered during the partial upload

Uploading a segment can currently make that individual `.ts` object look like a video to the catalog. The database catalog classifies `.ts` as `video`, storage inserts trigger catalog synchronization for each object, and the backend returns catalog video rows to the frontend. The frontend's `Recently Added` section sorts those rows by upload time.

This explains why the newest portion of a partially uploaded HLS movie appeared as recently added library items. It is not only a media-manager display problem; it affects the database catalog, backend library responses, and playback selection.

### Required publication model

An HLS rendition should have two distinct states:

1. **Staging/incomplete:** segment objects may exist, but the rendition is not a library video and is not playable through the HLS catalog path.
2. **Published/complete:** the complete segment set has been verified and the final manifest has been uploaded. Only then may the HLS rendition be represented as a virtual video.

The manifest remains the publication boundary. Segment objects alone must never create a visible video.

### Database/catalog changes to plan

1. Change `media_catalog_kind` so `segment-*.ts` objects inside a `*-hls` folder are treated as HLS implementation objects, not catalog videos. The same rule must not affect an original `.ts` source outside an HLS folder.

2. Classify the HLS manifest as a video only when its path matches the adjacent HLS convention, such as `<stem>-hls/index.m3u8`. Other HLS-folder implementation objects remain non-video catalog objects.

3. Ensure storage-object triggers do not create or refresh `public.videos` rows for HLS segments.

4. Ensure only a completed `*-hls/index.m3u8` can create the HLS virtual video row.

5. Add a repair migration or administrative reconciliation task that removes stale `media_objects` and `videos` rows for existing HLS segments without deleting the actual segment files from storage.

6. Add a server-side catalog filter as defense in depth. The backend must ignore paths matching the HLS segment pattern even if an old or manually-created catalog row remains.

7. Keep the existing rule that hides the virtual HLS entry when a sibling original video exists. The original should be the canonical library item in that case.

The repair migration must be ordered safely: update the classification function, remove stale `public.videos` rows for segment paths, update existing segment `media_objects.kind` values, then refresh canonical original and manifest rows. It must report counts and be safe to run more than once. It must not delete storage objects.

Because storage triggers can run while a migration or repair is in progress, the rollout should temporarily prevent HLS generation during the catalog repair window or use a transaction/maintenance procedure that leaves no interval where a segment can be republished as a video.

### Recently Added behavior

`Recently Added` should be built only from canonical published video rows:

- Original video objects.
- Completed HLS-only manifest objects.
- No HLS segments.
- No incomplete HLS folders.

The timestamp for an HLS-only virtual item should be the publication time of the manifest or completion record, not the creation time of the first or last segment. For an original-plus-HLS upload, the original video's library identity and timestamp should remain stable while HLS generation completes.

The frontend currently renders cached media before the fresh catalog stream completes. When the catalog classification is corrected, bump or invalidate the media-cache schema and filter legacy HLS segment paths from cached data as a defensive migration. Otherwise old segment rows can remain visible in `Recently Added` until the cache is replaced.

### Playback fallback when the original exists

If an original video exists beside a partial or unavailable HLS rendition, playback should use the original as the fallback source.

The playback contract should be:

1. The library returns the original path as the canonical media path when both original and HLS exist.
2. The player attempts the HLS manifest derived from that original path.
3. If the manifest is missing, incomplete, unavailable, or fails after retries, the player falls back to the original media URL.
4. If the original itself is not directly playable on the current device, the UI reports that HLS is not ready rather than exposing a partial segment object.
5. The fallback must preserve the same library item, artwork, subtitles, access control, and playback-progress key.

The existing frontend already has a direct-file fallback for HLS errors, but this behavior must be tested specifically for:

- Original plus no manifest.
- Original plus partial segments but no manifest.
- Original plus a manifest with a missing segment.
- Original plus a fully published HLS rendition.
- HLS-only upload with no original, both before and after publication.

If a client receives an HLS-only virtual path, the backend or catalog layer must know the associated original path, if one exists, rather than guessing from a partially visible folder. A completed HLS-only item with no original should remain HLS-only and should not fall back to `*-hls/index.m3u8` as though it were a direct media file.

### Backend safeguards

Add server-side checks so that:

- HLS manifest requests return not-ready/not-found while the rendition is incomplete.
- Manifest validation is performed at publication and when a cached manifest is first loaded; do not add a storage metadata request for every segment request in normal playback.
- Segment requests cannot make an incomplete rendition appear in the catalog.
- Catalog responses exclude HLS staging objects even if the database has stale rows.
- A canonical original path is preferred when the original exists.
- Access checks are performed against the canonical library item and its associated rendition.

### Acceptance criteria

- Uploading the first 300 HLS segments creates no `Recently Added` entries for segments.
- A partial HLS folder does not appear as a video in the library.
- A completed manifest creates exactly one HLS virtual item when no original exists.
- When the original exists, the library shows one canonical item rather than separate original and HLS items.
- Starting playback during HLS generation falls back smoothly to the original when it is directly playable.
- A missing HLS segment does not expose a raw `.ts` object as a library item.
- Existing stale catalog rows can be repaired without deleting valid storage objects.
- A previously cached segment entry is removed or ignored after the frontend cache schema is updated.
- Playback progress remains attached to the canonical original path across HLS publication.

## Phase 5: Encoding and segment-duration benchmarks

### Objective

Reduce local transcoding time and request count without sacrificing compatibility or playback quality.

### Baseline to record

Before changing encoding settings, record for a representative movie:

- Source duration, resolution, frame rate, and file size.
- Transcode elapsed time.
- Transcode speed in realtime multiples.
- Output size.
- Segment count and average segment size.
- HLS upload elapsed time.
- Effective upload throughput.
- Number of retries and failed requests.
- Playback success on every supported client/device class.

### Encoding experiments

Test one variable at a time:

1. Current `libx264` and `veryfast` baseline.
2. A faster CPU preset, measuring output-size growth.
3. An available hardware encoder, if the workstation has compatible hardware.
4. Avoiding unnecessary frame-rate conversion when the source is already compatible.
5. Avoiding scaling when the source is already within the target dimensions.
6. Segment durations such as 6, 8, and 10 seconds.

The installed FFmpeg build exposes hardware-encoding support, but actual hardware availability and driver behavior must be verified on the workstation before selecting a hardware path as the default.

### GOP and segment alignment

When changing segment duration, ensure keyframe cadence and segment boundaries remain compatible with the selected duration. The output must retain independent segments and predictable seeking behavior.

### Segment-duration tradeoff

Longer segments reduce the number of upload requests and can improve VOD upload throughput. They also increase the amount of media that must be downloaded before a seek or recovery point is reached. Select the shortest duration that gives a meaningful throughput improvement for the product's playback requirements.

### Acceptance criteria

- A chosen encoding configuration has documented quality and compatibility results.
- Transcode time improves or remains acceptable for the workstation.
- Segment count and upload request count are reduced when appropriate.
- Playback works on the supported browser and device matrix.
- No setting is adopted solely because it is faster if it causes playback regressions.

## Cross-phase pause, cancel, and failure behavior

The original upload currently supports pause and cancel checks. HLS subprocess work must eventually support equivalent behavior.

Required behavior:

- Pause stops new work and allows the current safe operation to finish or terminate cleanly.
- Cancel terminates FFmpeg and prevents new segment uploads.
- A cancelled job must not publish the manifest.
- A failed job must leave a clear failure state and preserve enough metadata for resume or cleanup.
- Closing the manager must not silently lose the state of an active job.

This behavior should be implemented alongside the phase that introduces subprocess progress, then re-tested after parallelism and resume support are added.

## Test strategy

### Fixtures

Use at least three fixtures:

1. A short video for fast functional tests.
2. A medium video with multiple audio/video characteristics.
3. A representative long movie of approximately two hours or more.

### Failure injection

Test controlled failures for:

- FFmpeg process failure.
- Source unavailable.
- One segment returning a transient 5xx.
- One segment returning 429.
- A permanent 4xx response.
- Network interruption during an upload.
- Manager close or process interruption.
- Missing local source during `original + HLS`.

### Playback verification

Verify:

- Manifest is available only after all segments are uploaded.
- Every manifest segment can be fetched.
- Playback starts, seeks, and completes.
- HLS-only catalog entries continue to work.
- Original-plus-HLS catalog entries continue to point to the expected media object.
- Rename and delete operations still handle the adjacent HLS folder correctly.

### Required recovery and catalog matrix

The implementation is not complete until these cases are tested end to end:

| Case | Expected catalog | Expected playback |
| --- | --- | --- |
| Original only | One original video | Direct original playback or existing normal behavior |
| Original plus HLS, before manifest | One original video; no HLS item or segments | Attempt HLS, then fall back to original |
| Original plus HLS, 300 segments only | One original video; no Recently Added segment entries | Original remains playable |
| Original plus invalid/missing-segment manifest | One original video | HLS rejected or fails over to original |
| Original plus complete HLS | One original video | HLS preferred, original fallback retained |
| HLS-only, before manifest | No library item | Not playable and not visible as a video |
| HLS-only, complete manifest | One virtual HLS video | HLS playback |
| Existing stale segment catalog rows | No segment videos after repair | Valid storage segments remain available to the published manifest |

Also test a generated set with more than 1,000 objects to prove storage-list pagination and a recovery interrupted while reconciliation is in progress.

There is currently no repository test suite dedicated to this workflow. The implementation should add focused automated tests for manifest parsing, HLS-path classification, progress-event parsing, retry classification, checkpoint identity, and canonical-path selection, plus an integration/manual test against a test Supabase bucket.

## Rollout sequence

1. Implement Phase 1 and validate reporting without changing upload behavior.
2. Implement and deploy the cross-system catalog repair/filter/publication safeguards before exposing any further partial HLS work to the library.
3. Implement Phase 2 and compare total elapsed time against the baseline.
4. Implement Phase 3 with retries first and bounded concurrency behind a configurable feature switch; keep sequential mode as the fallback.
5. Implement Phase 4 and test interruption/resume, including the already-partial-upload recovery path.
6. Run Phase 5 benchmarks and select production defaults based on measured results.
7. Update user-facing documentation with the selected defaults and recovery behavior.

The catalog safety track must come before enabling parallel uploads or recovery in production. Otherwise the faster/recoverable uploader would continue creating the same Recently Added pollution more efficiently.

Each phase should be committed separately so a regression can be isolated and reverted without discarding unrelated improvements.

## Instructions for implementation in a fresh chat

When implementation begins, the agent should:

1. Read this document completely before editing code.
2. Inspect the current versions of every file in the implementation map and check `git status` for unrelated user changes.
3. Establish a baseline with a short fixture before changing behavior.
4. Implement only the next rollout milestone, not all phases at once.
5. Preserve the existing storage paths and original-file behavior unless this plan explicitly calls for a change.
6. Add or update focused tests for the milestone before moving to the next milestone.
7. Run the short and medium test fixtures, then run the long-movie test at the milestone boundary.
8. Verify database/catalog behavior separately from media-manager behavior; a successful upload is not sufficient if the library exposes segments.
9. Report changed files, tests run, measured timings, and any remaining risks after each milestone.
10. Do not enable higher concurrency or automatic cleanup by default until the preceding fallback and recovery tests pass.

The first implementation milestone should be Phase 1 plus the catalog safety track's classification/filter design review. The catalog migration must be applied and verified before real partial HLS uploads are used to test later phases.

## Production readiness checklist

- [ ] Queue progress represents original upload, HLS generation, segment upload, and manifest finalization.
- [ ] Errors identify phase, object, status code, and retry count.
- [ ] Local source reuse is enabled for `original + HLS`.
- [ ] Segment uploads have bounded concurrency.
- [ ] Transient errors retry with backoff.
- [ ] Permanent errors fail clearly without publishing a manifest.
- [ ] Interrupted jobs can resume safely.
- [ ] Existing partial HLS folders can be discovered and reconciled.
- [ ] Recovery reuses verified remote segments instead of uploading them again.
- [ ] Recovery detects gaps and invalid segment sizes rather than relying on the highest segment number.
- [ ] Recovery displays the verified, missing, and invalid segment counts before continuing.
- [ ] HLS segments are excluded from `Recently Added` and all canonical library listings.
- [ ] Only a complete manifest creates an HLS-only catalog item.
- [ ] Original-plus-HLS uploads remain one canonical library item.
- [ ] Playback falls back to the original when HLS is missing or incomplete.
- [ ] Playback progress and access control remain attached to the canonical media path.
- [ ] Pause and cancel behavior is defined for every phase.
- [ ] Partial remote objects have a documented cleanup policy.
- [ ] Encoding and segment-duration defaults are benchmarked.
- [ ] Long-movie test completes successfully on the production-like environment.
- [ ] Playback and rename/delete behavior remain correct.

