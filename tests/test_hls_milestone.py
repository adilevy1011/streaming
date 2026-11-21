import json
import unittest
from pathlib import Path

from backend.catalog_safety import filter_cached_media, filter_catalog_videos, is_hls_manifest_path, is_hls_segment_path, validate_hls_playlist
from media_manager.hls_generator import EVENT_VERSION, CACHE_VERSION, StorageUploadError, checkpoint_identity, hls_prefix, is_retryable_upload_error, manifest_metadata_matches, manifest_segment_names, retry_delay_seconds, validate_local_source, verified_segments
from media_manager.hls_benchmark import parse_progress


class HlsMilestoneTests(unittest.TestCase):
    def test_path_classification_keeps_original_ts(self):
        self.assertTrue(is_hls_segment_path("Movies/Film-hls/segment-00123.ts"))
        self.assertFalse(is_hls_segment_path("Movies/Film.ts"))
        self.assertTrue(is_hls_manifest_path("Movies/Film-hls/index.m3u8"))

    def test_original_wins_over_adjacent_hls(self):
        rows = [
            {"path": "Movies/Film-hls/index.m3u8", "name": "index.m3u8"},
            {"path": "Movies/Film.mp4", "name": "Film.mp4"},
            {"path": "Movies/Film-hls/segment-00000.ts", "name": "segment-00000.ts"},
        ]
        result = filter_catalog_videos(rows)
        self.assertEqual([row["path"] for row in result], ["Movies/Film.mp4"])
        self.assertEqual(filter_cached_media(rows)[-1]["path"], "Movies/Film.mp4")

    def test_hls_only_manifest_is_visible_but_segments_are_not(self):
        rows = [
            {"path": "Movies/Film-hls/index.m3u8", "name": "index.m3u8"},
            {"path": "Movies/Film-hls/segment-00000.ts", "name": "segment-00000.ts"},
        ]
        result = filter_catalog_videos(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Film")

    def test_manifest_requires_endlist_and_all_segments(self):
        playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment-00000.ts\n#EXT-X-ENDLIST\n"
        self.assertFalse(validate_hls_playlist(playlist, lambda _: False))
        self.assertTrue(validate_hls_playlist(playlist, lambda _: True))
        self.assertFalse(validate_hls_playlist(playlist.replace("#EXT-X-ENDLIST\n", ""), lambda _: True))

    def test_event_envelope_is_versioned(self):
        payload = {"version": EVENT_VERSION, "job_id": "job-1", "phase": "complete", "event": "complete"}
        self.assertEqual(json.loads(json.dumps(payload))["version"], 1)
        self.assertEqual(hls_prefix("Movies/Film.mp4"), "Movies/Film-hls")

    def test_local_source_identity_is_checked(self):
        path = Path(__file__).resolve().parents[1] / "media_manager" / "hls_generator.py"
        stat = path.stat()
        self.assertEqual(validate_local_source(path, stat.st_size, stat.st_mtime_ns), path)
        with self.assertRaisesRegex(RuntimeError, "changed size"):
            validate_local_source(path, stat.st_size + 1, stat.st_mtime_ns)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            validate_local_source(path.with_name("missing-source.mp4"), stat.st_size, stat.st_mtime_ns)

    def test_retry_policy_distinguishes_transient_and_permanent_errors(self):
        self.assertTrue(is_retryable_upload_error(StorageUploadError("busy", 503)))
        self.assertTrue(is_retryable_upload_error(StorageUploadError("rate limited", 429)))
        self.assertFalse(is_retryable_upload_error(StorageUploadError("unauthorized", 401)))
        self.assertEqual(retry_delay_seconds(StorageUploadError("retry", 503, 2), 1), 2)

    def test_resume_identity_and_remote_size_reconciliation(self):
        identity = checkpoint_identity("Movies/Film.mp4", 123, 456)
        self.assertEqual(identity["format"], "hls-v6")
        expected = {"segment-00000.ts": 10, "segment-00001.ts": 20}
        self.assertEqual(verified_segments(expected, {"segment-00000.ts": 10, "segment-00001.ts": 19}), {"segment-00000.ts": 10})

    def test_benchmark_progress_parser_uses_stable_ffmpeg_fields(self):
        fields = parse_progress("frame=10\nout_time_us=2500000\nspeed=1.5x\nprogress=end\n")
        self.assertEqual(fields["out_time_us"], "2500000")
        self.assertEqual(fields["speed"], "1.5x")

    def test_manifest_skip_requires_metadata_and_referenced_segments(self):
        text = f"#EXTM3U\n#ADLV-HLS-FORMAT={CACHE_VERSION}\n#ADLV-SOURCE-SIZE=10\n#ADLV-SOURCE-MTIME-NS=20\n#EXTINF:6,\nsegment-00000.ts\n#EXT-X-ENDLIST\n"
        self.assertEqual(manifest_segment_names(text), ["segment-00000.ts"])
        self.assertTrue(manifest_metadata_matches(text, 10, 20))
        self.assertFalse(manifest_metadata_matches(text, 11, 20))


if __name__ == "__main__":
    unittest.main()
