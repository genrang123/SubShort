from pathlib import Path
import sys
import types

import pytest

from subshort.execution import Cancelled, Execution
from subshort.providers import FatalProviderError, ProviderError
from subshort.sources import classify_source, resolve_source


@pytest.mark.parametrize("value,kind", [
    ("123456789012345", "local"), ("video.mp4", "local"),
    (r"C:\videos\clip.mp4", "local"),
    ("https://youtu.be/abcdefghijk", "youtube"),
    ("https://www.youtube.com/watch?v=abcdefghijk", "youtube"),
    ("https://v.douyin.com/public/", "douyin"),
    ("https://www.facebook.com/watch/?v=123", "facebook"),
    ("https://fb.watch/public/", "facebook"),
    ("https://drive.google.com/file/d/public123456/view", "drive"),
    ("yt:abcdefghijk", "youtube"), ("douyin:123456789", "douyin"),
    ("drive:public123456", "drive"),
    ("yt:https://youtu.be/abcdefghijk", "youtube"),
])
def test_classification(value, kind):
    assert classify_source(value) == kind


@pytest.mark.parametrize("value", [
    "http://youtube.com/watch?v=x", "ftp://drive.google.com/file/x",
    "https://youtube.com.evil.test/watch", "https://evilyoutube.com/watch",
    "https://youtube.com@evil.test/watch", "https://user:secret@youtube.com/watch",
    "https://youtube.com:8443/watch", "https://youtube.com:invalid/watch",
    "https://youtube.com\\@evil.test/watch", "https://youtube.com./watch",
    "https:///watch", "https://[broken", "//youtube.com/watch",
    "yt:123", "douyin:abc", "drive:tiny", "yt:https://drive.google.com/file/x",
    "https://youtube.com/watch\n", "", " https://youtube.com/watch",
])
def test_invalid_sources(value):
    with pytest.raises(ProviderError):
        classify_source(value)


class Media:
    def __init__(self, probe=None):
        self.paths = []
        self.result = probe if probe is not None else {"duration": 2.0, "has_audio": True}

    def probe(self, path):
        self.paths.append(path)
        return self.result


def fake_downloader(monkeypatch, action=None):
    calls = []

    class Downloader:
        def __init__(self, options):
            self.options = options
            calls.append((options, None))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, value, download):
            calls[-1] = (self.options, value)
            assert download is True
            if action:
                return action(self.options)
            Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
            return {"id": "public"}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Downloader))
    return calls


def test_local_is_untouched_and_not_probed(tmp_path, monkeypatch):
    source = tmp_path / "123456789"
    source.write_bytes(b"local")
    calls = fake_downloader(monkeypatch)
    media = Media()
    work = tmp_path / "unused"
    assert resolve_source(source, work, media) is source
    assert source.read_bytes() == b"local"
    assert not work.exists() and not media.paths and not calls
    with pytest.raises(ProviderError):
        resolve_source(tmp_path / "missing", work, media)
    with pytest.raises(ProviderError):
        resolve_source(tmp_path, work, media)


@pytest.mark.parametrize("source,url", [
    ("yt:abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk"),
    ("douyin:123456789", "https://www.douyin.com/video/123456789"),
    ("drive:public123456", "https://drive.google.com/file/d/public123456/view"),
])
def test_download_probe_then_publish(tmp_path, monkeypatch, source, url):
    calls = fake_downloader(monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    unrelated = work / "keep.txt"
    unrelated.write_text("keep")
    destination = work / "source.mp4"
    destination.write_bytes(b"old")
    media = Media()
    messages = []
    assert resolve_source(source, work, media, messages.append) == destination
    assert destination.read_bytes() == b"video"
    assert unrelated.read_text() == "keep"
    assert sorted(path.name for path in work.iterdir()) == ["keep.txt", "source.mp4"]
    assert media.paths[0].parent.parent == work and not media.paths[0].exists()
    options, actual_url = calls[0]
    assert actual_url == url and messages
    assert options["format"] == "bestvideo+bestaudio/best"
    assert options["merge_output_format"] == "mp4"
    assert options["noplaylist"] and options["restrictfilenames"]
    assert "cookiefile" not in options and "cookiesfrombrowser" not in options


def test_missing_dependency_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(FatalProviderError, match="pip install yt-dlp"):
        resolve_source("yt:abcdefghijk", tmp_path, Media())


def test_download_errors_are_redacted_and_cleanup_owned_only(tmp_path, monkeypatch):
    sentinel = tmp_path / "keep.mp4"
    sentinel.write_bytes(b"keep")

    def fail(options):
        Path(options["outtmpl"].replace("%(ext)s", "mp4.part")).write_bytes(b"partial")
        raise RuntimeError("https://youtube.com/?secret=token provider response body")

    fake_downloader(monkeypatch, fail)
    with pytest.raises(ProviderError) as error:
        resolve_source("https://youtu.be/abcdefghijk?token=secret", tmp_path, Media())
    assert "secret" not in str(error.value) and "response body" not in str(error.value)
    assert list(tmp_path.iterdir()) == [sentinel]


def test_progress_cancellation_cleans_partial_download(tmp_path, monkeypatch):
    execution = Execution()

    def cancel(options):
        Path(options["outtmpl"].replace("%(ext)s", "mp4.part")).write_bytes(b"partial")
        execution.cancel()
        options["progress_hooks"][0]({"status": "downloading"})

    fake_downloader(monkeypatch, cancel)
    with execution.activate(), pytest.raises(Cancelled):
        resolve_source("yt:abcdefghijk", tmp_path, Media())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("probe", [{"duration": 0}, {"duration": -1}, {"duration": float("nan")}, {"duration": True}, {"duration": 2, "has_video": False}, {}])
def test_rejected_probe_preserves_existing_destination(tmp_path, monkeypatch, probe):
    fake_downloader(monkeypatch)
    destination = tmp_path / "source.mp4"
    destination.write_bytes(b"existing")
    with pytest.raises(ProviderError):
        resolve_source("yt:abcdefghijk", tmp_path, Media(probe))
    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("names,result", [
    ([], {"id": "x"}), (["download.mp4.part"], {"id": "x"}),
    (["download.mp4", "download.webm"], {"id": "x"}),
    (["other.mp4"], {"id": "x"}), (["download.txt"], {"id": "x"}),
    (["download.mp4"], {"_type": "playlist"}), (["download.mp4"], None),
])
def test_rejects_invalid_download_output(tmp_path, monkeypatch, names, result):
    def write_outputs(options):
        directory = Path(options["outtmpl"]).parent
        for name in names:
            (directory / name).write_bytes(b"video")
        return result

    fake_downloader(monkeypatch, write_outputs)
    media = Media()
    with pytest.raises(ProviderError):
        resolve_source("yt:abcdefghijk", tmp_path, media)
    assert not list(tmp_path.iterdir()) and not media.paths


def test_empty_output_is_not_probed(tmp_path, monkeypatch):
    def empty(options):
        Path(options["outtmpl"].replace("%(ext)s", "mp4")).touch()
        return {"id": "x"}

    fake_downloader(monkeypatch, empty)
    media = Media()
    with pytest.raises(ProviderError):
        resolve_source("yt:abcdefghijk", tmp_path, media)
    assert not media.paths and not list(tmp_path.iterdir())


def test_probe_exception_is_redacted(tmp_path, monkeypatch):
    fake_downloader(monkeypatch)

    class BrokenMedia:
        def probe(self, _path):
            raise RuntimeError("private provider body")

    with pytest.raises(ProviderError) as error:
        resolve_source("yt:abcdefghijk", tmp_path, BrokenMedia())
    assert "private" not in str(error.value)
    assert not list(tmp_path.iterdir())
