import json
import unittest
from pathlib import Path

from backend.catalog_safety import filter_cached_media, filter_catalog_videos, is_hls_manifest_path, is_hls_segment_path, validate_hls_playlist
from media_manager.hls_generator import EVENT_VERSION, hls_prefix, validate_local_source


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


if __name__ == "__main__":
    unittest.main()
