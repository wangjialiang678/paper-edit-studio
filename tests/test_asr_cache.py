from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cutpoint_lab.asr_cache import CachingAsrRunner, sha256_file
from cutpoint_lab.io import read_json, write_json


def _converted_payload(label: str) -> dict:
    return {
        "transcript": {
            "source_video": f"{label}.mp4",
            "duration_ms": 1000,
            "selected_segment_ids": ["sentence_0001"],
            "segments": [
                {
                    "id": "sentence_0001",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": label,
                    "tokens": [{"text": label, "start_ms": 0, "end_ms": 1000}],
                }
            ],
        },
        "vad": {
            "duration_ms": 1000,
            "speech_intervals": [{"start_ms": 0, "end_ms": 1000, "confidence": 0.99}],
            "source": label,
        },
    }


class RecordingAsrRunner:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[Path, Path, str]] = []

    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str) -> dict:
        self.calls.append((Path(media_path), Path(run_root), source_video))
        return deepcopy(self.payload)


class GuardedReader(io.BytesIO):
    """拒绝无上限读取，并记录每次分块大小。"""

    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.requested_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            raise AssertionError("sha256_file 不得一次性读取整个媒体文件")
        self.requested_sizes.append(size)
        # 文件对象可以比请求值少返回数据；哈希实现必须持续读到 EOF。
        return super().read(min(size, 7 * 1024))


class AsrCacheTests(unittest.TestCase):
    def test_sha256_file_is_correct_and_reads_in_bounded_chunks(self):
        payload = (b"content-fingerprint\x00" * 150_000) + b"tail"
        expected = hashlib.sha256(payload).hexdigest()
        readers: list[GuardedReader] = []

        def open_guarded(*_args, **_kwargs):
            reader = GuardedReader(payload)
            readers.append(reader)
            return reader

        with tempfile.TemporaryDirectory() as tmp:
            media_path = Path(tmp) / "large.mp4"
            media_path.write_bytes(b"placeholder")
            with patch("builtins.open", side_effect=open_guarded), patch.object(
                Path, "open", side_effect=open_guarded
            ):
                actual = sha256_file(media_path)

        self.assertEqual(actual, expected)
        self.assertEqual(len(readers), 1)
        positive_reads = [size for size in readers[0].requested_sizes if size > 0]
        self.assertGreater(len(positive_reads), 1)

    def test_cache_miss_calls_inner_and_publishes_both_json_files(self):
        payload = _converted_payload("fresh")
        inner = RecordingAsrRunner(payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"new media bytes"
            media_path.write_bytes(media_bytes)
            run_root = root / "project" / "asr"
            cache_dir = root / "cache"

            result = CachingAsrRunner(inner, cache_dir).transcribe(
                media_path,
                run_root,
                source_video="project/source.mp4",
            )

            digest = hashlib.sha256(media_bytes).hexdigest()
            entry = cache_dir / digest
            self.assertEqual(inner.calls, [(media_path, run_root, "project/source.mp4")])
            self.assertEqual(result["transcript"], payload["transcript"])
            self.assertEqual(result["vad"], payload["vad"])
            self.assertEqual(read_json(entry / "transcript.json"), payload["transcript"])
            self.assertEqual(read_json(entry / "vad.json"), payload["vad"])
            self.assertEqual({path.name for path in cache_dir.iterdir()}, {digest})

    def test_cache_hit_does_not_call_inner_and_marks_result(self):
        cached = _converted_payload("cached")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"same content"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            entry = cache_dir / hashlib.sha256(media_bytes).hexdigest()
            write_json(entry / "transcript.json", cached["transcript"])
            write_json(entry / "vad.json", cached["vad"])
            inner = RecordingAsrRunner(_converted_payload("must-not-run"))

            result = CachingAsrRunner(inner, cache_dir).transcribe(
                media_path,
                root / "unused-run-root",
                source_video="another/path.mp4",
            )

        self.assertEqual(inner.calls, [])
        expected_transcript = deepcopy(cached["transcript"])
        expected_transcript["source_video"] = "another/path.mp4"
        self.assertEqual(result["transcript"], expected_transcript)
        self.assertEqual(result["vad"], cached["vad"])
        self.assertEqual(result["cache"], "hit")

    def test_corrupt_complete_entry_falls_back_to_inner_and_rebuilds(self):
        fresh = _converted_payload("fresh")
        inner = RecordingAsrRunner(fresh)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"corrupt cache"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            entry = cache_dir / hashlib.sha256(media_bytes).hexdigest()
            entry.mkdir(parents=True)
            (entry / "transcript.json").write_text("{broken", encoding="utf-8")
            write_json(entry / "vad.json", fresh["vad"])

            result = CachingAsrRunner(inner, cache_dir).transcribe(
                media_path,
                root / "run",
                source_video="current/source.mp4",
            )

            self.assertEqual(len(inner.calls), 1)
            self.assertEqual(result["transcript"], fresh["transcript"])
            self.assertEqual(read_json(entry / "transcript.json"), fresh["transcript"])

    def test_structurally_invalid_json_entry_falls_back_to_inner(self):
        fresh = _converted_payload("fresh")
        inner = RecordingAsrRunner(fresh)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"invalid cache schema"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            entry = cache_dir / hashlib.sha256(media_bytes).hexdigest()
            write_json(entry / "transcript.json", {"source_video": "stale.mp4"})
            write_json(entry / "vad.json", {"duration_ms": 1000})

            result = CachingAsrRunner(inner, cache_dir).transcribe(
                media_path,
                root / "run",
                source_video="current/source.mp4",
            )

            self.assertEqual(len(inner.calls), 1)
            self.assertEqual(result["transcript"], fresh["transcript"])

    def test_simultaneous_misses_publish_without_failing_a_winner(self):
        payload = _converted_payload("concurrent")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"same concurrent content"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            digest = hashlib.sha256(media_bytes).hexdigest()
            rename_barrier = threading.Barrier(2)
            original_rename = Path.rename

            def synchronized_rename(path: Path, target: Path):
                if (
                    path.parent == cache_dir
                    and path.name.startswith(f".{digest}.")
                    and not path.name.endswith(".old")
                ):
                    rename_barrier.wait(timeout=5)
                return original_rename(path, target)

            errors: list[BaseException] = []

            def run_one(index: int) -> None:
                try:
                    CachingAsrRunner(RecordingAsrRunner(payload), cache_dir).transcribe(
                        media_path,
                        root / f"run-{index}",
                        source_video=f"source-{index}.mp4",
                    )
                except BaseException as exc:  # pragma: no cover - asserted below.
                    errors.append(exc)

            with patch.object(Path, "rename", synchronized_rename):
                threads = [threading.Thread(target=run_one, args=(index,)) for index in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(read_json(cache_dir / digest / "vad.json"), payload["vad"])
            self.assertFalse(any(path.name.endswith(".old") for path in cache_dir.iterdir()))

    def test_simultaneous_force_replacements_do_not_fail_or_leave_backups(self):
        payload = _converted_payload("fresh-force")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"same forced content"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            digest = hashlib.sha256(media_bytes).hexdigest()
            entry = cache_dir / digest
            stale = _converted_payload("stale")
            write_json(entry / "transcript.json", stale["transcript"])
            write_json(entry / "vad.json", stale["vad"])
            before_rename = threading.Barrier(2)
            after_rename = threading.Barrier(2)
            original_rename = Path.rename

            def synchronized_entry_rename(path: Path, target: Path):
                if path == entry:
                    before_rename.wait(timeout=5)
                    try:
                        return original_rename(path, target)
                    finally:
                        after_rename.wait(timeout=5)
                return original_rename(path, target)

            errors: list[BaseException] = []

            def run_one(index: int) -> None:
                try:
                    CachingAsrRunner(RecordingAsrRunner(payload), cache_dir).transcribe(
                        media_path,
                        root / f"run-force-{index}",
                        source_video=f"force-{index}.mp4",
                        force=True,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below.
                    errors.append(exc)

            with patch.object(Path, "rename", synchronized_entry_rename):
                threads = [threading.Thread(target=run_one, args=(index,)) for index in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(read_json(entry / "vad.json"), payload["vad"])
            self.assertFalse(any(path.name.endswith(".old") for path in cache_dir.iterdir()))

    def test_force_bypasses_hit_and_replaces_cached_payload(self):
        cached = _converted_payload("stale")
        fresh = _converted_payload("fresh")
        inner = RecordingAsrRunner(fresh)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"force refresh content"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            entry = cache_dir / hashlib.sha256(media_bytes).hexdigest()
            write_json(entry / "transcript.json", cached["transcript"])
            write_json(entry / "vad.json", cached["vad"])
            run_root = root / "project" / "asr"

            result = CachingAsrRunner(inner, cache_dir).transcribe(
                media_path,
                run_root,
                source_video="project/source.mp4",
                force=True,
            )

            self.assertEqual(inner.calls, [(media_path, run_root, "project/source.mp4")])
            self.assertNotEqual(result.get("cache"), "hit")
            self.assertEqual(result["transcript"], fresh["transcript"])
            self.assertEqual(read_json(entry / "transcript.json"), fresh["transcript"])
            self.assertEqual(read_json(entry / "vad.json"), fresh["vad"])
            self.assertEqual({path.name for path in cache_dir.iterdir()}, {entry.name})

    def test_incomplete_cache_entry_is_a_miss(self):
        for existing_name in ("transcript.json", "vad.json"):
            with self.subTest(existing_name=existing_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                media_path = root / "source.mp4"
                media_bytes = f"half-cache-{existing_name}".encode()
                media_path.write_bytes(media_bytes)
                cache_dir = root / "cache"
                entry = cache_dir / hashlib.sha256(media_bytes).hexdigest()
                stale = _converted_payload("stale")
                write_json(entry / existing_name, stale[existing_name.removesuffix(".json")])
                fresh = _converted_payload("fresh")
                inner = RecordingAsrRunner(fresh)

                result = CachingAsrRunner(inner, cache_dir).transcribe(
                    media_path,
                    root / "run",
                    source_video="source.mp4",
                )

                self.assertEqual(len(inner.calls), 1)
                self.assertEqual(result["transcript"], fresh["transcript"])
                self.assertEqual(read_json(entry / "transcript.json"), fresh["transcript"])
                self.assertEqual(read_json(entry / "vad.json"), fresh["vad"])

    def test_failed_publish_leaves_no_entry_or_temporary_directory(self):
        invalid_payload = _converted_payload("invalid")
        invalid_payload["vad"]["not_json_serializable"] = object()
        inner = RecordingAsrRunner(invalid_payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_path = root / "source.mp4"
            media_bytes = b"publish must be atomic"
            media_path.write_bytes(media_bytes)
            cache_dir = root / "cache"
            digest = hashlib.sha256(media_bytes).hexdigest()

            with self.assertRaises(TypeError):
                CachingAsrRunner(inner, cache_dir).transcribe(
                    media_path,
                    root / "run",
                    source_video="source.mp4",
                )

            self.assertFalse((cache_dir / digest).exists())
            if cache_dir.exists():
                self.assertEqual(list(cache_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
