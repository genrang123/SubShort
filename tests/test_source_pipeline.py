from pathlib import Path
from types import SimpleNamespace

import pytest

from subshort.app import App
from subshort.pipeline import Pipeline
from subshort.models import Job, Settings
from subshort.providers import ProviderError
from subshort.queue import JobQueue


def setup_pipeline(tmp_path, monkeypatch, source):
    monkeypatch.setattr(Settings, "validate", lambda *args, **kwargs: None)
    settings = Settings(root=tmp_path)
    queue = JobQueue(tmp_path / "queue.json")
    job = Job("source-job", source, "vi")
    queue.add(job)
    return Pipeline(settings, queue, lambda *args: None), job


def test_online_source_is_resolved_before_probe(tmp_path, monkeypatch):
    url = "https://youtu.be/abcdefghijk"
    pipeline, job = setup_pipeline(tmp_path, monkeypatch, url)
    events = []
    downloaded = None

    class Media:
        def __init__(self, *args):
            pass

        def probe(self, video):
            assert video == downloaded
            events.append("probe")
            return {"duration": 2.0, "has_audio": False}

    def resolve(source, work, media, notify=None):
        nonlocal downloaded
        assert source == url and isinstance(work, Path) and isinstance(media, Media)
        assert job.stage == "download"
        events.append("resolve")
        downloaded = work / "source.mp4"
        downloaded.write_bytes(b"resolved public video")
        notify("public source")
        return downloaded

    monkeypatch.setattr("subshort.pipeline.MediaAdapter", Media)
    monkeypatch.setattr("subshort.sources.resolve_source", resolve)
    pipeline.run(job)
    assert events == ["resolve", "probe"]
    assert job.status == "done", job.error
    assert job.stage == "no_audio"
    assert Path(job.output).read_bytes() == b"resolved public video"


def test_resolver_failure_prevents_probe_asr_and_publication(tmp_path, monkeypatch):
    pipeline, job = setup_pipeline(tmp_path, monkeypatch, "yt:abcdefghijk")
    calls = []

    def fail(source, work, media, notify=None):
        calls.append(source)
        raise ProviderError("public download rejected")

    def forbidden(*args, **kwargs):
        pytest.fail("downstream work must not run after source failure")

    class Media:
        def __init__(self, *args):
            pass

        probe = forbidden
        extract_audio = forbidden
        render = forbidden

    monkeypatch.setattr("subshort.sources.resolve_source", fail)
    monkeypatch.setattr("subshort.pipeline.MediaAdapter", Media)
    monkeypatch.setattr("subshort.pipeline.ASRSession", forbidden)
    pipeline.run(job)
    assert calls == ["yt:abcdefghijk"]
    assert job.status == "failed" and job.error == "public download rejected"
    assert job.output == ""
    assert not list((tmp_path / "output").iterdir())


def test_local_source_never_calls_downloader(tmp_path, monkeypatch):
    source = tmp_path / "123456789.mp4"
    source.write_bytes(b"local video")
    pipeline, job = setup_pipeline(tmp_path, monkeypatch, str(source))
    probed = []

    def forbidden(*args, **kwargs):
        pytest.fail("local source must not enter online resolver")

    class Media:
        def __init__(self, *args):
            pass

        def probe(self, video):
            probed.append(video)
            return {"duration": 2.0, "has_audio": False}

    monkeypatch.setattr("subshort.pipeline.MediaAdapter", Media)
    monkeypatch.setattr("subshort.sources.resolve_source", forbidden)
    pipeline.run(job)
    assert probed == [source]
    assert job.status == "done", job.error
    assert Path(job.output).read_bytes() == source.read_bytes()


def headless_app(tmp_path, monkeypatch, text):
    monkeypatch.setattr(Settings, "validate", lambda *args, **kwargs: None)
    settings = Settings(root=tmp_path, target_language="vi", mode="subtitle")
    errors = []
    refreshes = []
    app = SimpleNamespace(settings=settings, queue=JobQueue(tmp_path / "queue.json"), refresh=lambda: refreshes.append(True))
    app.can_mutate = lambda: not app.queue.load_error
    monkeypatch.setattr("subshort.app.QInputDialog.getMultiLineText", lambda *args, **kwargs: (text, True))
    monkeypatch.setattr("subshort.app.show_message", lambda *args, **kwargs: errors.append(args))
    return app, errors, refreshes


def test_add_urls_queues_each_line_with_independent_settings_snapshot(tmp_path, monkeypatch):
    sources = ["https://youtu.be/abcdefghijk", "douyin:123456789", "drive:public123456"]
    app, errors, refreshes = headless_app(tmp_path, monkeypatch, "\n".join(sources) + "\n\n")
    expected = app.settings.snapshot()
    App.add_urls(app)
    jobs = app.queue.snapshot()
    assert [job.source for job in jobs] == sources
    assert len({job.id for job in jobs}) == 3
    assert all(job.config == expected and job.target_language == "vi" and job.mode == "subtitle" for job in jobs)
    app.settings.target_language = "en"
    assert all(job.config["target_language"] == "vi" for job in app.queue.snapshot())
    assert not errors and refreshes == [True]


def test_add_urls_invalid_host_does_not_add_any_jobs(tmp_path, monkeypatch):
    app, errors, refreshes = headless_app(tmp_path, monkeypatch, "https://youtu.be/abcdefghijk\nhttps://youtube.com.evil.test/watch")
    App.add_urls(app)
    assert app.queue.snapshot() == []
    assert len(errors) == 1 and refreshes == [True]
