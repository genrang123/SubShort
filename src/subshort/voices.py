"""Consented, application-owned reference clips; no model or network access."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
import wave
from pathlib import Path

from .execution import run_process


class VoiceError(ValueError):
    pass


class VoiceRegistry:
    # These upstream recommendations are deliberately enforced by the application.
    _MAX_SECONDS = {"vieneu": 8, "omnivoice": 10}

    def __init__(self, root, media):
        self.directory = Path(root) / "state" / "voices"
        self.path = self.directory / "registry.json"
        self.media = media

    @staticmethod
    def _id(value):
        try:
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            raise VoiceError("Invalid voice ID") from None
        return value

    def _owned_path(self, voice_id):
        self._id(voice_id)
        path = self.directory / f"{voice_id}.wav"
        if self.directory.is_symlink() or path.is_symlink():
            raise VoiceError("Voice storage must not be a symbolic link")
        return path

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def _load(self):
        if self.directory.is_symlink() or self.path.is_symlink():
            raise VoiceError("Voice storage must not be a symbolic link")
        if not self.path.exists():
            return []
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError
            seen = set()
            for record in records:
                voice_id = self._id(record["id"])
                if voice_id in seen or record["path"] != f"{voice_id}.wav":
                    raise ValueError
                seen.add(voice_id)
                if (record["provider"] not in self._MAX_SECONDS
                        or not isinstance(record["name"], str) or not record["name"].strip()
                        or not isinstance(record["ref_text"], str)
                        or (record["provider"] == "omnivoice" and not record["ref_text"].strip())
                        or not isinstance(record["sha256"], str)
                        or len(record["sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in record["sha256"])):
                    raise ValueError
            return records
        except (ValueError, TypeError, KeyError, OSError):
            raise VoiceError("Cannot read voice registry; existing data was preserved") from None

    def _save(self, records):
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".registry-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(records, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _public(self, record):
        return {key: str(self._owned_path(record["id"])) if key == "path" else record[key]
                for key in ("id", "provider", "name", "path", "ref_text")}

    def list(self):
        return [self._public(record) for record in self._load()]

    def _source_duration(self, source, provider):
        result = run_process([self.media.ffprobe, "-v", "error", "-show_format",
                              "-show_streams", "-of", "json", str(source)],
                             capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
            raw = data["format"]["duration"]
            duration = float(raw)
            if (result.returncode or isinstance(raw, bool) or not math.isfinite(duration)
                    or not 3 <= duration <= self._MAX_SECONDS[provider]
                    or not any(s.get("codec_type") == "audio" for s in data["streams"])):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise VoiceError(f"Reference must contain audio lasting 3–{self._MAX_SECONDS[provider]} seconds") from None

    def add(self, name, audio, provider, ref_text="", consent=False):
        if consent is not True:
            raise VoiceError("Voice owner consent is required")
        if not isinstance(provider, str) or provider not in self._MAX_SECONDS:
            raise VoiceError("Unsupported voice provider")
        if not isinstance(name, str) or not name.strip():
            raise VoiceError("Voice name is required")
        if not isinstance(ref_text, str) or (provider == "omnivoice" and not ref_text.strip()):
            raise VoiceError("OmniVoice requires the exact reference transcript")
        source = Path(audio)
        if not source.is_file():
            raise VoiceError("Reference audio does not exist")
        records = self._load()
        self._source_duration(source, provider)
        voice_id = str(uuid.uuid4())
        output = self._owned_path(voice_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.media.normalize_clip(source, output)
            with wave.open(str(output), "rb") as clip:
                if (clip.getnchannels() != 1 or clip.getsampwidth() != 2
                        or clip.getframerate() != 24000 or clip.getcomptype() != "NONE"
                        or not 3 <= clip.getnframes() / clip.getframerate() <= self._MAX_SECONDS[provider]):
                    raise VoiceError("Normalized reference must be a mono 24 kHz PCM WAV in the allowed duration range")
            record = {"id": voice_id, "provider": provider, "name": name.strip(),
                      "path": output.name, "ref_text": ref_text, "sha256": self._digest(output)}
            self._save([*records, record])
        except BaseException:
            if not output.is_symlink():
                output.unlink(missing_ok=True)
            raise
        return self._public(record)

    def resolve(self, voice_id):
        self._id(voice_id)
        for record in self._load():
            if record["id"] == voice_id:
                path = self._owned_path(voice_id)
                if not path.is_file() or self._digest(path) != record["sha256"]:
                    raise VoiceError("Reference audio is missing or has changed")
                return self._public(record)
        raise KeyError(voice_id)

    def remove(self, voice_id):
        self._id(voice_id)
        records = self._load()
        record = next((item for item in records if item["id"] == voice_id), None)
        if record is None:
            raise KeyError(voice_id)
        path = self._owned_path(voice_id)
        self._save([item for item in records if item["id"] != voice_id])
        # Never delete a replaced/unrelated file, even at an owned filename.
        if path.is_file() and not path.is_symlink() and self._digest(path) == record["sha256"]:
            path.unlink()
