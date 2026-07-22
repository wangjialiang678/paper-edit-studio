from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from .io import read_json, write_json


_HASH_CHUNK_SIZE = 1024 * 1024


class AsrRunner(Protocol):
    def transcribe(
        self,
        media_path: Path,
        run_root: Path,
        *,
        source_video: str,
    ) -> dict[str, Any]: ...


def sha256_file(path: str | Path, *, chunk_size: int = _HASH_CHUNK_SIZE) -> str:
    """分块计算文件内容指纹，避免将媒体文件整体读入内存。"""

    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    digest = hashlib.sha256()
    with open(Path(path), "rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class CachingAsrRunner:
    """以源媒体 SHA-256 为键复用转写结果。"""

    def __init__(self, inner: AsrRunner, cache_dir: str | Path):
        self.inner = inner
        self.cache_dir = Path(cache_dir)

    def transcribe(
        self,
        media_path: Path,
        run_root: Path,
        *,
        source_video: str,
        force: bool = False,
    ) -> dict[str, Any]:
        media_path = Path(media_path)
        cache_entry = self.cache_dir / sha256_file(media_path)

        if not force and self._is_complete(cache_entry):
            try:
                transcript = read_json(cache_entry / "transcript.json")
                vad = read_json(cache_entry / "vad.json")
                if not self._is_valid_payload(transcript, vad):
                    raise ValueError("缓存 JSON 结构无效")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
            else:
                transcript["source_video"] = source_video
                return {"transcript": transcript, "vad": vad, "cache": "hit"}

        result = self.inner.transcribe(
            media_path,
            Path(run_root),
            source_video=source_video,
        )
        self._publish(cache_entry, result)
        return result

    @staticmethod
    def _is_complete(cache_entry: Path) -> bool:
        return (cache_entry / "transcript.json").is_file() and (cache_entry / "vad.json").is_file()

    @staticmethod
    def _is_valid_payload(transcript: Any, vad: Any) -> bool:
        return (
            isinstance(transcript, dict)
            and isinstance(transcript.get("selected_segment_ids"), list)
            and isinstance(transcript.get("segments"), list)
            and isinstance(vad, dict)
            and isinstance(vad.get("speech_intervals"), list)
        )

    def _publish(self, cache_entry: Path, result: dict[str, Any]) -> None:
        _publish_cache_entry(self.cache_dir, cache_entry, result)


def _publish_cache_entry(
    cache_dir: Path,
    cache_entry: Path,
    result: dict[str, Any],
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{cache_entry.name}.", dir=cache_dir))
    backup: Path | None = None
    published = False

    try:
        write_json(temporary / "transcript.json", result["transcript"])
        write_json(temporary / "vad.json", result["vad"])

        if cache_entry.exists():
            backup = cache_dir / f".{cache_entry.name}.{uuid.uuid4().hex}.old"
            try:
                cache_entry.rename(backup)
            except FileNotFoundError:
                # 另一发布者刚移走同一条目；继续竞争最终原子 rename。
                backup = None

        try:
            temporary.rename(cache_entry)
            published = True
        except OSError:
            if CachingAsrRunner._is_complete(cache_entry):
                # 同内容的并发发布已有胜者；完整条目即可视为本次发布成功。
                published = True
                return
            if backup is not None and backup.exists() and not cache_entry.exists():
                backup.rename(cache_entry)
            raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if published and backup is not None and backup.exists():
            shutil.rmtree(backup)


def backfill_cache_entry(
    media_path: str | Path,
    transcript_path: str | Path,
    vad_path: str | Path,
    cache_dir: str | Path,
) -> dict[str, Any]:
    """把已有项目产物登记到内容指纹缓存；完整条目保持不变。"""

    digest = sha256_file(media_path)
    cache_root = Path(cache_dir)
    cache_entry = cache_root / digest
    if CachingAsrRunner._is_complete(cache_entry):
        return {"cache_key": digest, "path": str(cache_entry), "created": False}
    result = {
        "transcript": read_json(transcript_path),
        "vad": read_json(vad_path),
    }
    _publish_cache_entry(cache_root, cache_entry, result)
    return {"cache_key": digest, "path": str(cache_entry), "created": True}


__all__ = [
    "AsrRunner",
    "CachingAsrRunner",
    "backfill_cache_entry",
    "sha256_file",
]
