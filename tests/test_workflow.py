import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


pipeline = load("fast_pipeline", ROOT / "scripts" / "run_fast_pipeline.py")
commons = load("commons_assets", ROOT / "scripts" / "download_commons_assets.py")


class PipelineTests(unittest.TestCase):
    def write_visual_project(self, root: Path, signatures=None, include_videos=True):
        project = root / "project"
        source = project / "assets" / "source"
        source.mkdir(parents=True)
        signatures = signatures or ["split", "cutaway", "timeline", "recovery"]
        segments = [
            {
                "id": f"0{index}", "visual_type": signature,
                "primary_media_kind": "video", "composition_signature": signature,
                "footage_friendly": True,
            }
            for index, signature in enumerate(signatures, 1)
        ]
        segments_path = project / "segments.json"
        segments_path.write_text(json.dumps(segments), encoding="utf-8")
        manifest = project / "assets" / "manifest.jsonl"
        records = []
        if include_videos:
            for index in range(1, 5):
                clip = source / f"clip-{index}.mp4"
                clip.write_bytes(b"video")
                records.append({
                    "id": f"clip-{index}", "segment_id": f"0{index}", "kind": "video",
                    "title": f"Clip {index}", "source_url": f"https://example.test/{index}",
                    "license": "CC0", "local_source": clip.relative_to(project).as_posix(), "used": True,
                })
        manifest.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        return project, segments_path, manifest

    def test_visual_diversity_accepts_distinct_licensed_footage(self):
        with tempfile.TemporaryDirectory() as directory:
            project, segments, manifest = self.write_visual_project(Path(directory))
            result = pipeline.validate_visual_diversity(project, segments, manifest)
            self.assertEqual(result["distinct_composition_signatures"], 4)
            self.assertEqual(result["video_covered_scenes"], 4)

    def test_visual_diversity_rejects_repeated_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            project, segments, manifest = self.write_visual_project(
                Path(directory), signatures=["cards", "cards", "cards", "timeline"]
            )
            with self.assertRaisesRegex(RuntimeError, "composition signatures"):
                pipeline.validate_visual_diversity(project, segments, manifest)

    def test_visual_diversity_rejects_static_only_footage_friendly_video(self):
        with tempfile.TemporaryDirectory() as directory:
            project, segments, manifest = self.write_visual_project(Path(directory), include_videos=False)
            with self.assertRaisesRegex(RuntimeError, "moving footage covers"):
                pipeline.validate_visual_diversity(project, segments, manifest)

    def test_asset_change_invalidates_render(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            index = project / "index.html"
            timeline = project / "timeline.json"
            manifest = project / "assets" / "manifest.jsonl"
            image = project / "assets" / "source" / "scene.jpg"
            font = project / "assets" / "fonts" / "subset.ttf"
            voice = project / "audio" / "voice.wav"
            rendered = project / "renders" / "rendered.mp4"
            for path in [index, timeline, manifest, image, font, voice, rendered]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            manifest.write_text(json.dumps({"used": True, "local_source": "assets/source/scene.jpg"}) + "\n", encoding="utf-8")
            time.sleep(0.02)
            image.write_bytes(b"changed")
            inputs = pipeline.collect_render_inputs(project, index, timeline, [voice], manifest, font)
            self.assertFalse(pipeline.is_fresh([rendered], inputs))

    def test_worker_tuning_respects_memory(self):
        self.assertEqual(pipeline.choose_render_workers(cpu_count=16, total_memory_gb=32), 6)
        self.assertEqual(pipeline.choose_render_workers(cpu_count=16, total_memory_gb=8), 1)

    def test_manifest_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            outside = Path(directory) / "outside.jpg"
            outside.write_bytes(b"x")
            manifest = project / "manifest.jsonl"
            manifest.write_text(json.dumps({"used": True, "local_source": "../outside.jpg"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "escapes the project"):
                pipeline.read_manifest_assets(project, manifest)

    def test_verified_marker_detects_same_size_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "final.mp4"
            marker = root / "final.ok.json"
            rules = root / "pipeline.py"
            target.write_bytes(b"good")
            rules.write_text("rules", encoding="utf-8")
            pipeline.write_verified_marker(marker, target)
            self.assertTrue(pipeline.verified_marker_matches(marker, target, rules))
            timestamps = (target.stat().st_atime_ns, target.stat().st_mtime_ns)
            target.write_bytes(b"evil")
            os.utime(target, ns=timestamps)
            self.assertFalse(pipeline.verified_marker_matches(marker, target, rules))

    def test_render_profile_and_fps_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "render-profile.json"
            profile.write_text(json.dumps({"fps": "24", "quality": "high"}), encoding="utf-8")
            self.assertTrue(pipeline.render_profile_matches(profile, {"fps": "24", "quality": "high"}))
            self.assertFalse(pipeline.render_profile_matches(profile, {"fps": "30", "quality": "high"}))
            self.assertAlmostEqual(pipeline.fps_value("30000/1001"), 29.97003, places=4)


class CommonsTests(unittest.TestCase):
    def test_relevance_rejects_unrelated_and_reversed_phrase(self):
        self.assertEqual(commons.relevance_score("sweat gland microscopy", "File:Fingerprt.jpg"), 0)
        self.assertLess(commons.relevance_score("glass of milk", "File:Milk glass creamer.jpg"), 0.45)

    def test_relevance_accepts_specific_title(self):
        self.assertGreaterEqual(commons.relevance_score("sweat gland histology human", "File:Gray940 - sweat gland.png"), 0.45)
        self.assertGreaterEqual(commons.relevance_score('intitle:"glass of milk"', "File:Glass of milk.jpg"), 0.8)

    def test_image_signature_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.jpg"
            image.write_bytes(b"not an image" * 200)
            self.assertFalse(commons.image_file_valid(image, "image/jpeg"))
            image.write_bytes(b"\xff\xd8\xff" + b"x" * 1024)
            self.assertTrue(commons.image_file_valid(image, "image/jpeg"))


if __name__ == "__main__":
    unittest.main()
