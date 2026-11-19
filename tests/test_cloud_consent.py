"""Selected cloud providers run with isolated local media and provider fakes."""
import sys
import wave
from pathlib import Path

import pytest

from subshort import app
from subshort.models import Job, Settings
from subshort.media import MediaAdapter
from subshort.providers import Segment


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    calls = []

    class Media:
        def __init__(self, *args):
            pass

        def probe(self, video):
            calls.append("probe")
            return {"has_audio": True, "duration": 2.0}

        def extract_audio(self, video, audio):
            calls.append("extract_audio")

        def prepare_audio(self, source, output, *args):
            calls.append("prepare_audio")
            output.write_bytes(b"fake audio")

        def output_dimensions(self, width, height, layout=None):
            return width, height

        def render(self, video, audio, subtitles, output, *args, **kwargs):
            calls.append("render")
            output.write_bytes(b"fake video")
        def validate_output(self, output, *args):
            calls.append("validate_output")
            assert output.read_bytes() == b"fake video"

    class ASR:
        def transcribe(self, audio, language):
            calls.append("asr")
            return [Segment(0, 1, "source text")]

    class Queue:
        def update(self, job):
            pass

    def forbidden(*args, **kwargs):
        pytest.fail("unselected cloud provider was constructed")

    monkeypatch.setattr(app, "MediaAdapter", Media)
    monkeypatch.setattr(app, "ASRSession", lambda *args, **kwargs: ASR())
    monkeypatch.setattr(app, "OpenAITranslator", forbidden)
    monkeypatch.setattr(app, "GeminiTranslator", forbidden)
    monkeypatch.setattr(app, "OpenAICompatibleTranslator", forbidden)
    monkeypatch.setattr(app, "EdgeTTS", forbidden)
    model = tmp_path / "asr-model"
    model.mkdir()
    settings = Settings(root=tmp_path, ffmpeg=sys.executable, ffprobe=sys.executable,
                        asr_model=str(model), mode="subtitle", slow_speed=1.0, final_speed=1.0,
                        openai_api_key="fake-openai", gemini_api_key="fake-gemini")
    job = Job("consent", str(tmp_path / "source.mp4"), "vi", mode="subtitle")
    pipeline = app.Pipeline(settings, Queue(), lambda *args: None)
    return settings, job, pipeline, calls


@pytest.mark.parametrize("provider,translator_name", [("openai", "OpenAITranslator"), ("gemini", "GeminiTranslator"), ("openai_compatible", "OpenAICompatibleTranslator")])
def test_selected_translation_runs_without_extra_permission(pipeline_env, monkeypatch, provider, translator_name):
    settings, job, pipeline, calls = pipeline_env
    settings.translation_provider = provider
    if provider == "openai_compatible":
        settings.translation_model = "custom-model"
        settings.translation_base_url = "http://127.0.0.1:1234/v1"
    requests = []

    class Translator:
        def __init__(self, *args, **kwargs):
            pass

        def translate(self, segments, target):
            requests.append((segments, target))
            return segments

    monkeypatch.setattr(app, translator_name, Translator)
    pipeline.run(job)
    assert requests == [([Segment(0, 1, "source text", "0")], "vi")]
    assert job.status == "done", job.error
    assert Path(job.output).read_bytes() == b"fake video"
    assert calls[-2:] == ["render", "validate_output"]


def test_gemini_dispatches_selected_key_model_and_target(pipeline_env, monkeypatch):
    settings, job, pipeline, calls = pipeline_env
    settings.translation_provider = "gemini"
    settings.translation_model = "gemini-chosen"
    job.target_language = "ja"
    requests = []

    class Gemini:
        def __init__(self, key, model):
            requests.append((key, model))

        def translate(self, segments, target):
            requests.append((segments, target))
            return [Segment(0, 1, "translated text")]

    monkeypatch.setattr(app, "GeminiTranslator", Gemini)
    pipeline.run(job)
    assert requests == [("fake-gemini", "gemini-chosen"), ([Segment(0, 1, "source text", "0")], "ja")]
    assert job.status == "done", job.error
    assert Path(job.output).read_bytes() == b"fake video"
    assert calls[-2:] == ["render", "validate_output"]
    assert "translated text" in (settings.root / "output_artifacts" / job.id / "translated.srt").read_text(encoding="utf-8-sig")


def test_compatible_dispatches_private_key_model_and_base_url(pipeline_env, monkeypatch):
    settings, job, pipeline, calls = pipeline_env
    settings.translation_provider = "openai_compatible"
    settings.translation_model = "custom-model"
    settings.translation_base_url = "http://127.0.0.1:1234/v1"
    settings.openai_compatible_api_key = "private-compatible-key"
    requests = []

    class Compatible:
        def __init__(self, key, model, base_url):
            requests.append((key, model, base_url))

        def translate(self, segments, target):
            return [Segment(0, 1, "bản dịch")]

    monkeypatch.setattr(app, "OpenAICompatibleTranslator", Compatible)
    pipeline.run(job)
    assert job.status == "done", job.error
    assert requests == [("private-compatible-key", "custom-model", "http://127.0.0.1:1234/v1")]
    assert "private-compatible-key" not in str(job.config)
    assert "bản dịch" in (settings.root / "output_artifacts" / job.id / "translated.srt").read_text(encoding="utf-8-sig")


def test_selected_edge_synthesizes_and_publishes_without_extra_permission(pipeline_env, monkeypatch):
    settings, job, pipeline, calls = pipeline_env
    settings.tts_provider = "edge"
    settings.tts_voice = "vi-VN-HoaiMyNeural"
    job.mode = "dub"
    requests = []

    class Translator:
        def __init__(self, key):
            assert key == "fake-openai"

        def translate(self, segments, target):
            return segments

    class Edge:
        async def synthesize(self, text, output, voice):
            requests.append((text, voice))
            with wave.open(str(output), "wb") as stream:
                stream.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\0\0" * 2400)

    monkeypatch.setattr(app, "OpenAITranslator", Translator)
    monkeypatch.setattr(app, "EdgeTTS", Edge)
    monkeypatch.setattr(app.MediaAdapter, "normalize_clip", lambda self, raw, output: output.write_bytes(raw.read_bytes()), raising=False)
    monkeypatch.setattr(app.MediaAdapter, "fit_clip", MediaAdapter.fit_clip, raising=False)
    monkeypatch.setattr(app.MediaAdapter, "clip_frames", staticmethod(MediaAdapter.clip_frames), raising=False)
    monkeypatch.setattr(app.MediaAdapter, "timeline_audio", lambda self, clips, starts, output, duration: output.write_bytes(b"timeline"), raising=False)
    pipeline.run(job)
    assert requests == [("source text", "vi-VN-HoaiMyNeural")]
    assert job.status == "done", job.error
    assert Path(job.output).read_bytes() == b"fake video"
    assert calls[-2:] == ["render", "validate_output"]


def test_job_snapshot_keeps_provider_model_when_current_settings_change(pipeline_env, monkeypatch):
    settings, job, pipeline, calls = pipeline_env
    settings.translation_provider = "gemini"
    settings.translation_model = "gemini-snapshotted"
    job.config = settings.snapshot()
    settings.translation_provider = "openai"
    settings.translation_model = "gpt-current"
    requests = []

    class Gemini:
        def __init__(self, key, model):
            requests.append((key, model))

        def translate(self, segments, target):
            return segments

    monkeypatch.setattr(app, "GeminiTranslator", Gemini)
    pipeline.run(job)
    assert job.status == "done", job.error
    assert requests == [("fake-gemini", "gemini-snapshotted")]
    assert job.config["translation_provider"] == "gemini"
    assert job.config["translation_model"] == "gemini-snapshotted"
    assert "gemini_api_key" not in job.config and "openai_api_key" not in job.config

@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_pipeline_passes_source_region_to_renderer_and_metadata(pipeline_env, monkeypatch, mode):
    from subshort import subtitle_region
    settings, job, pipeline, calls = pipeline_env
    settings.old_subtitle_mode = mode
    settings.old_subtitle_region = "[0.1,0.7,0.8,0.2]"
    monkeypatch.setattr(app, "OpenAITranslator", lambda *a, **k: type("Translator", (), {"translate": lambda self, segments, target: segments})())
    expected = (.1, .7, .8, .2)
    if mode == "auto":
        monkeypatch.setattr(subtitle_region, "sample_frames", lambda *a: [])
        monkeypatch.setattr(subtitle_region, "detect", lambda *a: expected)
        monkeypatch.setattr(subtitle_region, "refine_region", lambda *a: expected)
    seen = []
    original = app.MediaAdapter.render
    def render(self, *args, **kwargs):
        seen.append(kwargs.get("blur_region"))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(app.MediaAdapter, "render", render)
    pipeline.run(job)
    assert job.status == "done", job.error
    assert seen == [expected]
    assert job.metadata["subtitle_region"]["region"] == expected


def test_pipeline_auto_vision_samples_and_renders_without_extra_permission(pipeline_env, monkeypatch):
    from subshort import subtitle_region
    settings, job, pipeline, calls = pipeline_env
    settings.old_subtitle_mode = "auto"
    monkeypatch.setattr(app, "OpenAITranslator", lambda *a, **k: type("Translator", (), {"translate": lambda self, segments, target: segments})())
    def sample(*args):
        calls.append("sample_frames")
        return []
    def detect(*args):
        calls.append("detect")
        return None
    monkeypatch.setattr(subtitle_region, "sample_frames", sample)
    monkeypatch.setattr(subtitle_region, "detect", detect)
    pipeline.run(job)
    assert job.status == "done", job.error
    assert calls.index("sample_frames") < calls.index("detect") < calls.index("render")
    assert job.metadata["subtitle_region"]["result"] == "no_subtitles"

@pytest.mark.parametrize("malformed", [False, True])
def test_pipeline_auto_no_box_or_malformed_never_blurs(pipeline_env, monkeypatch, malformed):
    from subshort import subtitle_region
    settings, job, pipeline, calls = pipeline_env
    settings.old_subtitle_mode = "auto"
    monkeypatch.setattr(app, "OpenAITranslator", lambda *a, **k: type("Translator", (), {"translate": lambda self, segments, target: segments})())
    monkeypatch.setattr(subtitle_region, "sample_frames", lambda *a: [])
    def detect(*args):
        return subtitle_region.parse_detection('{}' if malformed else '{"has_subtitles":false,"box":null}')
    monkeypatch.setattr(subtitle_region, "detect", detect)
    seen = []
    original = app.MediaAdapter.render
    def render(self, *args, **kwargs):
        seen.append(kwargs.get("blur_region"))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(app.MediaAdapter, "render", render)
    pipeline.run(job)
    if malformed:
        assert job.status == "failed" and seen == [] and not job.output
    else:
        assert job.status == "done", job.error
        assert seen == [None] and job.metadata["subtitle_region"]["result"] == "no_subtitles"
