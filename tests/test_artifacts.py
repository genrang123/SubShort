import hashlib
import json

import pytest

from subshort import artifacts
from subshort.artifacts import ArtifactCache
from subshort.execution import Cancelled, Execution
from subshort.providers import Segment


SEGMENTS = [Segment(0, 1, "hello")]


def make_cache(tmp_path, config=None):
    source = tmp_path / "source.mp4"
    if not source.exists():
        source.write_bytes(b"source media")
    return ArtifactCache(tmp_path / "work", source, config or {})


def test_segments_roundtrip_and_explicit_dependencies(tmp_path):
    cache = make_cache(tmp_path, {"theme": "dark"})
    deps = {"model": "whisper", "options": {"language": "en", "beam": 5}}
    cache.save_segments("asr", deps, SEGMENTS)
    other = make_cache(tmp_path, {"theme": "light", "unrelated": "changed"})
    assert other.load_segments("asr", {"options": {"beam": 5, "language": "en"}, "model": "whisper"}) == SEGMENTS
    assert other.load_segments("asr", {**deps, "model": "other"}) is None
    assert other.load_segments("translate", deps) is None
    assert other.load_file("asr", deps) is None


def test_source_content_not_metadata_invalidates(tmp_path):
    cache = make_cache(tmp_path)
    cache.save_segments("asr", {}, SEGMENTS)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"SOURCE MEDIA")
    assert make_cache(tmp_path).load_segments("asr", {}) is None
    # A cache instance hashes source only once, even across several stage accesses.
    assert cache.source_hash == hashlib.sha256(b"source media").hexdigest()
    assert cache.load_segments("asr", {}) == SEGMENTS


def test_artifact_content_invalidates_and_preserves_other_stages(tmp_path):
    cache = make_cache(tmp_path)
    cache.save_segments("asr", {}, SEGMENTS)
    clip = cache.work / "clip.wav"
    clip.write_bytes(b"audio")
    cache.save_file("clip_0", {"voice": "one"}, clip)
    assert cache.load_file("clip_0", {"voice": "one"}) == clip
    assert cache.load_file("clip_0", {"voice": "two"}) is None
    clip.write_bytes(b"AUDIO")
    assert cache.load_file("clip_0", {"voice": "one"}) is None
    assert cache.load_segments("asr", {}) == SEGMENTS
    entry = json.loads(cache.manifest.read_text())["entries"]["asr"]
    (cache.work / entry["path"]).write_text("[]")
    assert cache.load_segments("asr", {}) is None


@pytest.mark.parametrize("manifest", ["{", "null", "[]", '{"version":1,"entries":[]}', '{"version":2,"entries":{}}'])
def test_malformed_manifest_is_miss_without_modification(tmp_path, manifest):
    cache = make_cache(tmp_path)
    cache.work.mkdir()
    cache.manifest.write_text(manifest)
    existing = cache.work / "existing.wav"
    existing.write_bytes(b"keep")
    assert cache.load_segments("asr", {}) is None
    assert cache.load_file("clip_0", {}) is None
    assert cache.manifest.read_text() == manifest
    assert existing.read_bytes() == b"keep"


@pytest.mark.parametrize("relative", ["../outside.wav", "/outside.wav", None, 123])
def test_manifest_path_escape_or_invalid_path_is_miss(tmp_path, relative):
    cache = make_cache(tmp_path)
    cache.work.mkdir()
    cache.manifest.write_text(json.dumps({"version": 1, "entries": {"clip_0": {
        "kind": "file", "dependencies": cache._fingerprint({}),
        "path": relative, "sha256": hashlib.sha256(b"outside").hexdigest(),
    }}}))
    assert cache.load_file("clip_0", {}) is None


def test_saving_outside_work_rejected(tmp_path):
    cache = make_cache(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError):
        cache.save_file("clip_0", {}, outside)
    assert not cache.manifest.exists()


def test_manifest_contains_hashes_not_dependency_secrets(tmp_path):
    cache = make_cache(tmp_path, {"api_key": "config-secret"})
    deps = {"api_key": "dependency-secret", "voice": "private-voice"}
    cache.save_segments("translate", deps, SEGMENTS)
    payload = cache.manifest.read_text()
    for secret in ("config-secret", "dependency-secret", "private-voice", "api_key"):
        assert secret not in payload
    entry = json.loads(payload)["entries"]["translate"]
    assert len(entry["dependencies"]) == len(entry["sha256"]) == 64
    assert cache.load_segments("translate", deps) == SEGMENTS


def test_cancelled_hash_does_not_publish_manifest(tmp_path):
    cache = make_cache(tmp_path)
    cache.work.mkdir()
    clip = cache.work / "clip.wav"
    clip.write_bytes(b"audio")
    execution = Execution()
    execution.cancel()
    with execution.activate(), pytest.raises(Cancelled):
        cache.save_file("clip_0", {}, clip)
    assert not cache.manifest.exists()
    with execution.activate(), pytest.raises(Cancelled):
        make_cache(tmp_path)


def test_failed_atomic_publish_preserves_manifest(tmp_path, monkeypatch):
    cache = make_cache(tmp_path)
    cache.save_segments("asr", {}, SEGMENTS)
    previous = cache.manifest.read_bytes()
    clip = cache.work / "clip.wav"
    clip.write_bytes(b"audio")

    def fail(path, data):
        raise OSError("write failed")

    monkeypatch.setattr(artifacts, "_write_atomic", fail)
    with pytest.raises(OSError, match="write failed"):
        cache.save_file("clip_0", {}, clip)
    assert cache.manifest.read_bytes() == previous



def test_missing_file_and_invalid_segments_are_misses(tmp_path):
    cache = make_cache(tmp_path)
    cache.save_segments("asr", {}, SEGMENTS)
    manifest = json.loads(cache.manifest.read_text())
    entry = manifest["entries"]["asr"]
    path = cache.work / entry["path"]
    path.unlink()
    assert cache.load_segments("asr", {}) is None
    payload = b'[{"start":0,"end":-1,"text":"bad"}]'
    path.write_bytes(payload)
    entry["sha256"] = hashlib.sha256(payload).hexdigest()
    cache.manifest.write_text(json.dumps(manifest))
    assert cache.load_segments("asr", {}) is None


def test_cancellation_checked_between_hash_chunks(tmp_path, monkeypatch):
    cache = make_cache(tmp_path)
    cache.work.mkdir()
    clip = cache.work / "clip.wav"
    clip.write_bytes(b"a" * (1024 * 1024 + 1))
    calls = 0

    def cancel_after_first_chunk():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Cancelled("cancelled")

    monkeypatch.setattr(artifacts, "check_cancelled", cancel_after_first_chunk)
    with pytest.raises(Cancelled):
        cache.save_file("clip_0", {}, clip)
    assert calls == 2
    assert not cache.manifest.exists()