"""Content-verified intermediate artifacts with explicit per-stage dependencies."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .credentials import _write_atomic
from .execution import check_cancelled
from .providers import ProviderError, Segment, validate_segments


class ArtifactCache:
    def __init__(self, work: Path, source: Path, config: dict):
        self.work = Path(work).resolve()
        self.manifest = self.work / "artifacts.json"
        # Only explicit stage dependencies participate; unrelated config must not invalidate work.
        self.source_hash = self._hash_file(Path(source))

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                check_cancelled()
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _fingerprint(self, dependencies: dict) -> str:
        payload = json.dumps(
            {"source": self.source_hash, "dependencies": dependencies},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _contained(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            raise ValueError("Artifact path must be relative")
        resolved = (self.work / path).resolve()
        if not resolved.is_relative_to(self.work) or resolved == self.work:
            raise ValueError("Artifact path escapes work directory")
        return resolved

    def _entries(self) -> dict:
        try:
            self._contained(self.manifest.name)
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == 1 and isinstance(data.get("entries"), dict):
                return data["entries"]
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def _load(self, stage: str, dependencies: dict, kind: str) -> Path | None:
        entry = self._entries().get(stage)
        if not isinstance(entry, dict) or entry.get("kind") != kind or entry.get("dependencies") != self._fingerprint(dependencies):
            return None
        try:
            path = self._contained(entry["path"])
            if self._hash_file(path) == entry["sha256"]:
                return path
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def _save(self, stage: str, dependencies: dict, path: Path, kind: str) -> None:
        path = Path(path).resolve()
        relative = path.relative_to(self.work).as_posix()
        self._contained(relative)
        entry = {"kind": kind, "dependencies": self._fingerprint(dependencies),
                 "path": relative, "sha256": self._hash_file(path)}
        entries = self._entries()
        entries[stage] = entry
        _write_atomic(self.manifest, json.dumps(
            {"version": 1, "entries": entries}, ensure_ascii=False, sort_keys=True, indent=2,
        ).encode("utf-8"))

    def load_segments(self, stage: str, dependencies: dict) -> list[Segment] | None:
        path = self._load(stage, dependencies, "segments")
        if path is None:
            return None
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                return None
            return validate_segments([Segment(**row) for row in rows])
        except (OSError, ValueError, TypeError, ProviderError):
            return None

    def save_segments(self, stage: str, dependencies: dict, segments: list[Segment]) -> None:
        segments = validate_segments(segments)
        name = hashlib.sha256(stage.encode("utf-8")).hexdigest()
        path = self._contained(f"{name}.segments.json")
        _write_atomic(path, json.dumps([asdict(segment) for segment in segments],
                                      ensure_ascii=False, allow_nan=False).encode("utf-8"))
        self._save(stage, dependencies, path, "segments")

    def load_file(self, stage: str, dependencies: dict) -> Path | None:
        return self._load(stage, dependencies, "file")

    def save_file(self, stage: str, dependencies: dict, path: Path) -> None:
        self._save(stage, dependencies, path, "file")


