import json
import shutil
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from subshort.voices import VoiceError, VoiceRegistry


def wav(path, seconds=3):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * (24000 * seconds))
    return path


class Media:
    ffprobe = "local-ffprobe"

    def normalize_clip(self, source, output):
        shutil.copyfile(source, output)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    def probe(args, **kwargs):
        with wave.open(args[-1], "rb") as stream:
            duration = stream.getnframes() / stream.getframerate()
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "format": {"duration": str(duration)}, "streams": [{"codec_type": "audio"}]}))
    monkeypatch.setattr("subshort.voices.run_process", probe)
    return VoiceRegistry(tmp_path, Media())


def test_enrollment_owns_copy_and_keeps_stable_id_and_exact_transcript(registry, tmp_path):
    source = wav(tmp_path / "source.wav")
    transcript = "  Xin chào.\nĐây là giọng của tôi.  "
    voice = registry.add(" My voice ", source, "omnivoice", transcript, consent=True)
    assert str(uuid.UUID(voice["id"])) == voice["id"]
    assert voice["name"] == "My voice"
    assert voice["ref_text"] == transcript
    assert Path(voice["path"]).parent == tmp_path / "state" / "voices"
    assert Path(voice["path"]) != source
    source.unlink()
    reopened = VoiceRegistry(tmp_path, Media())
    assert reopened.list() == [voice]
    assert reopened.resolve(voice["id"]) == voice
    reopened.remove(voice["id"])
    assert reopened.list() == []
    assert not Path(voice["path"]).exists()


@pytest.mark.parametrize("consent", [False, None, 1, "yes"])
def test_consent_must_be_explicit_boolean(registry, tmp_path, consent):
    with pytest.raises(VoiceError, match="consent"):
        registry.add("voice", tmp_path / "never-read.wav", "vieneu", consent=consent)
    assert not registry.directory.exists()


@pytest.mark.parametrize("provider,text", [("omnivoice", ""), ("omnivoice", " \n"),
                                           ("vienneu", ""), ("edge", "")])
def test_invalid_contract_rejected_before_read(registry, tmp_path, provider, text):
    with pytest.raises(VoiceError):
        registry.add("voice", tmp_path / "never-read.wav", provider, text, consent=True)
    assert not registry.directory.exists()


@pytest.mark.parametrize("provider,seconds,valid", [("vieneu", 3, True), ("vieneu", 8, True),
    ("vieneu", 9, False), ("omnivoice", 10, True), ("omnivoice", 11, False),
    ("vieneu", 2, False)])
def test_application_duration_boundaries(registry, tmp_path, provider, seconds, valid):
    source = wav(tmp_path / "source.wav", seconds)
    if valid:
        assert registry.add("voice", source, provider, "exact", consent=True)["provider"] == provider
    else:
        with pytest.raises(VoiceError, match="seconds"):
            registry.add("voice", source, provider, "exact", consent=True)
        assert registry.list() == []


@pytest.mark.parametrize("duration,streams,returncode", [("nan", [{"codec_type": "audio"}], 0),
    ("inf", [{"codec_type": "audio"}], 0), (True, [{"codec_type": "audio"}], 0),
    ("3", [{"codec_type": "video"}], 0), ("3", [], 0),
    ("3", [{"codec_type": "audio"}], 1)])
def test_invalid_probe_rejected(registry, tmp_path, monkeypatch, duration, streams, returncode):
    source = wav(tmp_path / "source.wav")
    monkeypatch.setattr("subshort.voices.run_process", lambda *a, **k: SimpleNamespace(
        returncode=returncode, stdout=json.dumps({"format": {"duration": duration}, "streams": streams})))
    with pytest.raises(VoiceError):
        registry.add("voice", source, "vieneu", consent=True)
    assert registry.list() == []


def test_tampered_or_missing_reference_is_not_resolved_or_deleted(registry, tmp_path):
    voice = registry.add("voice", wav(tmp_path / "source.wav"), "vieneu", consent=True)
    owned = Path(voice["path"])
    owned.write_bytes(b"unrelated replacement")
    with pytest.raises(VoiceError, match="changed"):
        registry.resolve(voice["id"])
    registry.remove(voice["id"])
    assert owned.read_bytes() == b"unrelated replacement"
    assert registry.list() == []


def test_missing_reference(registry, tmp_path):
    voice = registry.add("voice", wav(tmp_path / "source.wav"), "vieneu", consent=True)
    Path(voice["path"]).unlink()
    with pytest.raises(VoiceError, match="missing"):
        registry.resolve(voice["id"])
    registry.remove(voice["id"])
    assert registry.list() == []


def test_metadata_failure_preserves_old_registry_and_audio(registry, tmp_path, monkeypatch):
    source = wav(tmp_path / "source.wav")
    first = registry.add("first", source, "vieneu", consent=True)
    original = registry.path.read_bytes()

    def fail(*args):
        raise OSError("disk error")
    monkeypatch.setattr("subshort.voices.os.replace", fail)
    with pytest.raises(OSError):
        registry.add("second", source, "vieneu", consent=True)
    assert registry.path.read_bytes() == original
    assert sorted(p.name for p in registry.directory.iterdir()) == sorted(["registry.json", f"{first['id']}.wav"])
    with pytest.raises(OSError):
        registry.remove(first["id"])
    assert Path(first["path"]).is_file()
    assert registry.path.read_bytes() == original


def test_metadata_removed_before_audio_cleanup(registry, tmp_path, monkeypatch):
    voice = registry.add("voice", wav(tmp_path / "source.wav"), "vieneu", consent=True)
    unlink = Path.unlink

    def observe(path, *args, **kwargs):
        if path == Path(voice["path"]):
            assert json.loads(registry.path.read_text(encoding="utf-8")) == []
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", observe)
    registry.remove(voice["id"])


def test_corrupt_metadata_does_not_delete_external_file(registry, tmp_path):
    source = wav(tmp_path / "source.wav")
    voice = registry.add("voice", source, "vieneu", consent=True)
    records = json.loads(registry.path.read_text(encoding="utf-8"))
    records[0]["path"] = str(source)
    registry.path.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(VoiceError):
        registry.remove(voice["id"])
    assert source.is_file()
    assert Path(voice["path"]).is_file()


@pytest.mark.parametrize("voice_id", ["../outside", "not-a-uuid", "", None])
def test_invalid_id_rejected(registry, voice_id):
    with pytest.raises(VoiceError):
        registry.resolve(voice_id)
    with pytest.raises(VoiceError):
        registry.remove(voice_id)


def test_failed_normalization_leaves_no_enrollment(registry, tmp_path, monkeypatch):
    source = wav(tmp_path / "source.wav")

    def fail(source, output):
        output.write_bytes(b"partial")
        raise RuntimeError("normalization failed")
    monkeypatch.setattr(registry.media, "normalize_clip", fail)
    with pytest.raises(RuntimeError, match="normalization failed"):
        registry.add("voice", source, "vieneu", consent=True)
    assert registry.list() == []
    assert list(registry.directory.iterdir()) == []


def test_corrupt_json_is_preserved(registry, tmp_path):
    source = wav(tmp_path / "source.wav")
    registry.directory.mkdir(parents=True)
    registry.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(VoiceError):
        registry.add("voice", source, "vieneu", consent=True)
    assert registry.path.read_text(encoding="utf-8") == "{broken"
