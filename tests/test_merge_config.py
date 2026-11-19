import json
from pathlib import Path

import pytest

from subshort import models
from subshort.models import Job, Settings


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(models.shutil, "which", lambda name: f"/stub/{name}")
    return tmp_path


MERGE_KEYS = {"merge_words", "merge_segments"}


def test_merge_settings_are_absent_from_defaults_and_template(root):
    settings = Settings.load(root)
    assert MERGE_KEYS.isdisjoint(settings.snapshot())
    assert all(not hasattr(settings, key) for key in MERGE_KEYS)
    template = json.loads(Path(models.__file__).with_name("default_settings.json").read_text(encoding="utf-8"))
    assert MERGE_KEYS.isdisjoint(template)


@pytest.mark.parametrize("config", [
    {"merge_words": 30},
    {"merge_segments": 5},
    {"merge_segments": 5, "merge_words": 30},
    {"merge_segments": False, "merge_words": 0},
])
def test_load_ignores_retired_merge_preferences_without_rewriting_file(root, config):
    (root / "asr-model").mkdir()
    config.update(asr_model="asr-model", source_language="zh", final_speed=1.25)
    path = root / "config" / "settings.json"
    original = json.dumps(config)
    path.write_text(original, encoding="utf-8")
    settings = Settings.load(root)
    settings.validate()
    assert settings.source_language == "zh"
    assert settings.final_speed == 1.25
    assert MERGE_KEYS.isdisjoint(settings.snapshot())
    assert all(not hasattr(settings, key) for key in MERGE_KEYS)
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("key", sorted(MERGE_KEYS))
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, "2", None])
def test_old_job_merge_preferences_are_ignored_and_removed_on_prepare(root, key, value):
    (root / "asr-model").mkdir()
    settings = Settings(root=root, asr_model="asr-model", source_language="en", final_speed=1.0)
    config = {key: value, "source_language": "zh", "final_speed": 1.25}
    job = Job("j", "clip.mp4", "vi", config=dict(config))
    effective = settings.for_job(job)
    effective.validate()
    assert effective.source_language == "zh"
    assert effective.final_speed == 1.25
    assert MERGE_KEYS.isdisjoint(effective.snapshot())
    assert job.config == config
    assert settings.source_language == "en"
    assert settings.final_speed == 1.0
    job.prepare(settings)
    assert MERGE_KEYS.isdisjoint(job.config)
    assert job.config["source_language"] == "zh"
    assert job.config["final_speed"] == 1.25


def test_save_drops_both_retired_merge_preferences(root, monkeypatch):
    path = root / "config" / "settings.json"
    path.write_text(json.dumps({"merge_segments": 5, "merge_words": 30, "source_language": "zh"}), encoding="utf-8")
    settings = Settings.load(root)
    monkeypatch.setattr(models.CredentialStore, "save", lambda self, values: None)
    settings.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert MERGE_KEYS.isdisjoint(saved)
    assert saved["source_language"] == "zh"
