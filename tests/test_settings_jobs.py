import json
import math

import pytest

from subshort import models
from subshort.models import Job, Settings
from subshort.providers import Segment
from subshort.queue import JobQueue

# Deterministic executable lookup: only these names "exist" for validation.
STUB_EXECUTABLES = {"ffmpeg", "ffprobe", "firered.bat", "tts-run.bat"}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(models.shutil, "which", lambda name: f"/stub/{name}" if name in STUB_EXECUTABLES else None)
    (tmp_path / "config").mkdir()
    return tmp_path


@pytest.fixture
def make(root):
    model = root / "asr-model"; model.mkdir()
    vad = root / "asr-vad"; vad.touch()
    aligner = root / "asr-aligner"; aligner.mkdir()
    def make(**kwargs):
        kwargs.setdefault("asr_model", "asr-model")
        kwargs.setdefault("asr_vad_model", "asr-vad")
        kwargs.setdefault("asr_aligner", "asr-aligner")
        return Settings(root=root, **kwargs)
    return make

def test_load_defaults_source_language_is_empty_auto(root):
    settings = Settings.load(root)
    assert settings.source_language == ""
    assert settings.mode == "subtitle_dub"
    assert settings.background_volume == 0.25
    with pytest.raises(ValueError, match="asr_model"):
        settings.validate()


def test_load_legacy_config_missing_source_language_and_volume(root):
    (root / "config" / "settings.json").write_text(json.dumps({"merge_segments": 3}), encoding="utf-8")
    settings = Settings.load(root)
    assert settings.source_language == ""
    assert not hasattr(settings, "merge_words")
    assert settings.background_volume == 0.25  # missing key keeps dataclass default
    with pytest.raises(ValueError, match="asr_model"):
        settings.validate()

@pytest.mark.parametrize("value", [False, True])
def test_removed_service_consents_are_ignored_in_legacy_settings_and_jobs(root, make, value):
    obsolete = dict.fromkeys(("consent_translation", "consent_edge_tts", "consent_vision_frames"), value)
    (root / "config" / "settings.json").write_text(json.dumps(obsolete), encoding="utf-8")
    loaded = Settings.load(root)
    effective = make().for_job(Job("legacy", "source.mp4", "vi", config=obsolete))
    for settings in (loaded, effective):
        assert all(not hasattr(settings, field) for field in obsolete)
        assert obsolete.keys().isdisjoint(settings.snapshot())



def test_load_does_not_convert_percent_volume(root):
    (root / "config" / "settings.json").write_text(json.dumps({"background_volume": 25}), encoding="utf-8")
    settings = Settings.load(root)
    assert settings.background_volume == 25  # no heuristic conversion on load
    with pytest.raises(ValueError):
        settings.validate()


def test_source_language_is_independent_of_target(make):
    for source in ("", "zh", "en"):
        settings = make(source_language=source, target_language="vi")
        settings.validate()
        assert settings.source_language == source
    make(source_language="", target_language="zh").validate()
    with pytest.raises(ValueError):
        make(source_language=None, target_language="vi").validate()
    with pytest.raises(ValueError):
        make(source_language="zh", target_language="   ").validate()


@pytest.mark.parametrize("value", [0, -0.5, math.nan, math.inf, -math.inf, True, "1.0"])
def test_speeds_must_be_finite_positive_numbers(make, value):
    with pytest.raises(ValueError):
        make(slow_speed=value).validate()
    with pytest.raises(ValueError):
        make(final_speed=value).validate()


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0])
def test_background_volume_accepts_unit_range(make, value):
    make(background_volume=value).validate()


@pytest.mark.parametrize("value", [-0.01, 1.01, 25, 100, math.nan, math.inf, True, "0.5"])
def test_background_volume_rejects_out_of_range_without_conversion(make, value):
    with pytest.raises(ValueError):
        make(background_volume=value).validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("asr_provider", "whisper"),
        ("translation_provider", "google"),
        ("tts_provider", "piper"),
        ("mode", "subtitle_only"),
    ],
)
def test_unknown_providers_and_modes_rejected(make, field, value):
    with pytest.raises(ValueError):
        make(**{field: value}).validate()


def test_validate_mode_override_checks_requested_mode(make):
    settings = make(mode="subtitle_dub", tts_provider="none")
    settings.validate()
    settings.validate("subtitle_dub")
    with pytest.raises(ValueError):
        settings.validate("dub_only")


def test_snapshot_excludes_secrets_extra_and_root(make):
    settings = make(openai_api_key="sk-secret", gemini_api_key="gm-secret", source_language="zh",
                    final_speed=1.25, extra={"token": "x", "nested": {"a": [1]}})
    snap = settings.snapshot()
    assert set(snap).isdisjoint({"root", "openai_api_key", "gemini_api_key", "extra"})
    assert snap["source_language"] == "zh" and snap["final_speed"] == 1.25
    json.dumps(snap)  # JSON-compatible

    snap["final_speed"] = 9.9
    snap["extra"] = {"mutated": True}
    assert settings.final_speed == 1.25
    assert settings.extra == {"token": "x", "nested": {"a": [1]}}
    assert settings.snapshot() is not snap
    assert "extra" not in settings.snapshot()


def test_for_job_uses_job_config_and_current_credentials(make):
    settings = make(openai_api_key="current-key", source_language="en", target_language="fr",
                    final_speed=1.0, tts_provider="none")
    job = Job("j1", "clip.mp4", "vi", config={
        "source_language": "zh", "final_speed": 1.25, "tts_provider": "none",
        "openai_api_key": "snapshot-secret", "gemini_api_key": "snapshot-secret",
    })
    effective = settings.for_job(job)
    assert effective is not settings
    assert effective.source_language == "zh"          # config snapshot wins
    assert effective.target_language == "vi"          # job field wins
    assert effective.final_speed == 1.25
    assert effective.openai_api_key == "current-key"  # credentials never snapshotted
    assert effective.gemini_api_key == ""
    assert effective.root == settings.root
    effective.validate()

    effective.final_speed = 2.0
    assert settings.final_speed == 1.0
    assert job.config["final_speed"] == 1.25


def test_for_job_legacy_empty_config_uses_current_snapshot(make):
    settings = make(source_language="", target_language="fr", final_speed=0.9, tts_provider="none")
    job = Job("legacy", "clip.mp4", "zh")
    assert job.config == {}
    effective = settings.for_job(job)
    assert effective.source_language == ""
    assert effective.final_speed == 0.9
    assert effective.target_language == "zh"  # legacy job target overrides current "fr"
    assert effective.mode == "subtitle_dub"
    assert job.config == {}  # for_job does not write the job
    effective.validate()


def test_for_job_ignores_legacy_wrapper_and_unknown_keys(make):
    settings = make(tts_provider="none")
    job = Job("j", "clip.mp4", "vi", config={
        "qwen_runtime": "missing/runtime.exe", "qwen_device": "bogus",
        "firered_command": "missing-wrapper", "vienneu_tts_command": "missing-tts",
        "extra": {"ignored": True}, "bogus_key": 1,
    })
    effective = settings.for_job(job)
    assert not hasattr(effective, "bogus_key")
    assert effective.extra == {}
    effective.validate()

def test_selected_model_paths_are_validated(root, make):
    with pytest.raises(ValueError):
        make(asr_model="missing-model").validate()
    make(asr_provider="qwen").validate()
    with pytest.raises(ValueError):
        make(asr_provider="qwen", asr_aligner="missing-aligner").validate()
    (root / "vad-directory").mkdir()
    with pytest.raises(ValueError):
        make(asr_provider="qwen", asr_vad_model="vad-directory").validate()
    with pytest.raises(ValueError):
        make(asr_provider="firered", asr_vad_model="vad-directory").validate()
    with pytest.raises(ValueError):
        make(asr_provider="firered", asr_vad_model="missing-vad").validate()
    (root / "tts-model").mkdir()
    (root / "tts-cache").mkdir()
    make(tts_provider="vieneu", tts_model="tts-model", tts_hf_cache="tts-cache").validate()
    with pytest.raises(ValueError):
        make(tts_provider="vieneu").validate()


@pytest.mark.parametrize("provider", ["qwen", "firered"])
def test_installed_asr_uses_runtime_next_to_exe(root, monkeypatch, provider):
    install = root / "install"
    monkeypatch.setattr(models.sys, "frozen", True, raising=False)
    monkeypatch.setattr(models.sys, "executable", str(install / "SubShort.exe"))
    settings = Settings(root=root, asr_provider=provider)
    with pytest.raises(ValueError, match=models.ASR_MODELS[provider]):
        settings.asr_worker_python()

    runtime = install / "runtimes" / provider / "Scripts" / "python.exe"
    runtime.parent.mkdir(parents=True)
    runtime.touch()
    assert settings.asr_worker_python() == runtime.resolve()
    setattr(settings, f"{provider}_python", "old/missing/python.exe")
    assert settings.asr_worker_python() == runtime.resolve()


def test_selected_media_commands_are_validated(root, make):
    make(ffmpeg="ffmpeg", ffprobe="ffprobe").validate()
    with pytest.raises(ValueError):
        make(ffmpeg="no-such-ffmpeg").validate()
    with pytest.raises(ValueError):
        make(ffprobe="no-such-ffprobe").validate()
    stub = root / "ffmpeg.bin"
    stub.write_text("x", encoding="utf-8")
    make(ffmpeg=str(stub), ffprobe=str(stub)).validate()


def test_prepare_persists_snapshot_and_sets_running(make):
    settings = make(openai_api_key="current-key", source_language="en", tts_provider="none", final_speed=1.0)
    job = Job("j", "clip.mp4", "vi", config={"source_language": "zh", "final_speed": 1.25, "tts_provider": "none"},
              output="stale.mp4", error="old", progress=0.4, metadata={"stale": 1}, stage="old", status="failed")
    effective = job.prepare(settings)
    assert job.status == "running"
    assert (job.output, job.error, job.progress, job.metadata, job.stage) == ("", "", 0.0, {}, "")
    assert job.config == effective.snapshot()
    assert "openai_api_key" not in job.config
    assert job.config["final_speed"] == 1.25

    settings.final_speed, settings.openai_api_key = 9.0, "rotated-key"
    retry = job.prepare(settings)
    assert retry.final_speed == 1.25  # persisted config survives retries
    assert retry.openai_api_key == "rotated-key"  # credentials stay current
    assert job.config["final_speed"] == 1.25


def test_prepare_invalid_clears_stale_fields_before_failing(make):
    settings = make(tts_provider="none")
    job = Job("j", "clip.mp4", "vi", config={"final_speed": 0}, output="old.mp4", error="old error",
              progress=0.5, metadata={"x": 1}, stage="render", status="failed")
    with pytest.raises(ValueError):
        job.prepare(settings)
    assert (job.output, job.error, job.progress, job.metadata, job.stage) == ("", "", 0.0, {}, "")
    assert job.status == "failed"
    assert job.config == {"final_speed": 0}  # not overwritten by invalid attempt


def test_job_status_validation():
    for status in ("queued", "running", "done", "failed", "cancelled"):
        assert Job("j", "a.mp4", "vi", status=status).status == status
    with pytest.raises(ValueError):
        Job("j", "a.mp4", "vi", status="paused")
    assert Job("j", "a.mp4", "vi").stage == ""


def _pipeline_stubs(monkeypatch, calls):
    class FakeMedia:
        def __init__(self, ffmpeg, ffprobe):
            calls["media"].append((ffmpeg, ffprobe))

        def probe(self, video):
            return {"duration": 10.0, "has_audio": True}

        def extract_audio(self, video, audio):
            calls["extract"].append((video, audio))

        def prepare_audio(self, source, output, speed, duration):
            pass

        def output_dimensions(self, width, height, layout):
            return width, height

        def validate_output(self, output, duration):
            assert output.exists()

        def render(self, video, audio, subtitles, output, speed=1.0, duration=None, **kwargs):
            calls["render"].append(speed)
            output.write_bytes(b"rendered test output")

    class FakeASR:
        def __init__(self, *args, **kwargs):
            calls["asr_init"] += 1

        def transcribe(self, audio, language=None):
            calls["asr"].append(language)
            return [Segment(0, 1, "hello")]
    class FakeTranslator:
        def __init__(self, api_key):
            calls["translator_init"].append(api_key)

        def translate(self, segments, target):
            calls["translate"].append(target)
            return segments

    monkeypatch.setattr("subshort.pipeline.MediaAdapter", FakeMedia)
    monkeypatch.setattr("subshort.pipeline.ASRSession", FakeASR)
    monkeypatch.setattr("subshort.pipeline.OpenAITranslator", FakeTranslator)


def _calls():
    return {"media": [], "extract": [], "render": [], "asr": [], "asr_init": 0,
            "translator_init": [], "translate": []}


def test_pipeline_uses_snapshot_config_and_current_key(root, make, monkeypatch, tmp_path):
    from subshort.pipeline import Pipeline

    calls = _calls()
    _pipeline_stubs(monkeypatch, calls)

    current = make(openai_api_key="current-key", source_language="en", target_language="fr",
                   final_speed=1.0, tts_provider="none", output_dir="work")
    queue = JobQueue(tmp_path / "queue.json")
    queue.add(Job("job-1", str(tmp_path / "clip.mp4"), "vi", config={
        "source_language": "zh", "target_language": "vi", "final_speed": 1.25,
        "tts_provider": "none", "keep_original_audio": False,
        "output_dir": "work",
    }))
    loaded = JobQueue(tmp_path / "queue.json").jobs[0]
    assert loaded.config["source_language"] == "zh"
    assert loaded.target_language == "vi"

    notes = []
    Pipeline(current, queue, lambda job, message="": notes.append(job)).run(loaded)

    assert loaded.status == "done", loaded.error
    assert calls["asr"] == ["zh"]                       # snapshot language, not current "en"
    assert calls["translate"] == ["vi"]                 # job target, not current "fr"
    assert calls["translator_init"] == ["current-key"]  # current credentials only
    assert calls["render"] == [0.7 * 1.25]             # snapshot slow and final speed
    assert len(calls["extract"]) == 1 and calls["media"]
    timing = json.loads((root / "work_artifacts" / "job-1" / "pipeline_timing.json").read_text(encoding="utf-8"))
    assert timing["final_speed"] == 1.25
    assert notes and notes[-1].status == "done"


def test_pipeline_invalid_config_fails_before_media(make, monkeypatch, tmp_path):
    from subshort.pipeline import Pipeline

    calls = _calls()
    _pipeline_stubs(monkeypatch, calls)

    current = make(tts_provider="none")
    job = Job("bad", str(tmp_path / "clip.mp4"), "vi", config={"final_speed": -1})
    queue = JobQueue(tmp_path / "queue.json")
    queue.add(job)
    Pipeline(current, queue, lambda j: None).run(job)

    assert job.status == "failed" and job.error
    assert calls["media"] == [] and calls["extract"] == [] and calls["asr_init"] == 0
    assert job.config == {"final_speed": -1}


def test_pipeline_cancel_during_render_never_publishes_output(make, monkeypatch, tmp_path):
    from subshort.pipeline import Pipeline
    calls = _calls()
    _pipeline_stubs(monkeypatch, calls)
    queue = JobQueue(tmp_path / "queue.json")
    current = make(tts_provider="none", keep_original_audio=False, output_dir="work")
    job = Job("cancel-render", str(tmp_path / "clip.mp4"), "vi")
    queue.add(job)
    pipeline = Pipeline(current, queue, lambda *_: None)
    from subshort.pipeline import MediaAdapter
    def render(*args, **kwargs):
        pipeline.cancel()
    monkeypatch.setattr(MediaAdapter, "render", render)
    pipeline.run(job)
    assert job.status == "cancelled"
    assert not job.output
    assert queue.snapshot()[0].status == "cancelled"

@pytest.mark.parametrize("value", ["", "null", "[0,0,2,1]", "[true,0,0.2,0.2]", "[0,0,NaN,1]"])
def test_manual_subtitle_region_validation(make, value):
    with pytest.raises(ValueError, match="phụ đề"):
        make(old_subtitle_mode="manual", old_subtitle_region=value).validate()


def test_vision_settings_snapshot_and_editor(make):
    from subshort.settings_dialog import FIELD_REGISTRY
    settings = make(old_subtitle_mode="manual", old_subtitle_region="[0.1,0.7,0.8,0.2]",
                    vision_provider="gemini", vision_model="chosen")
    settings.validate()
    job = Job("vision", "source.mp4", "vi", config=settings.snapshot())
    settings.vision_model = "current-model"
    effective = settings.for_job(job)
    assert effective.vision_model == "chosen"
    assert effective.vision_provider == "gemini"
    assert {"old_subtitle_mode", "old_subtitle_region", "vision_provider", "vision_model"} <= {f[1] for f in FIELD_REGISTRY}
