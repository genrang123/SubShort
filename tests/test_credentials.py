import json
import sys

import pytest

from subshort import models
from subshort.credentials import CredentialError, CredentialStore
from subshort.models import Settings

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    return tmp_path


def config_path(root):
    return root / "config" / "settings.json"


def store_path(root):
    return root / "config" / "credentials.dpapi"


def test_roundtrip_remove_and_no_plaintext(root):
    store = CredentialStore(root)
    store.save({"openai_api_key": "sk-\u00e9-secret", "gemini_api_key": "gm-secret", "openai_compatible_api_key": "compatible-secret"})
    assert store.load() == {"openai_api_key": "sk-\u00e9-secret", "gemini_api_key": "gm-secret", "openai_compatible_api_key": "compatible-secret"}
    raw = store_path(root).read_bytes()
    assert b"sk-" not in raw and b"gm-secret" not in raw and b"\xc3\xa9" not in raw
    assert b"compatible-secret" not in raw

    store.save({"openai_api_key": ""})
    assert store.load() == {"openai_api_key": "", "gemini_api_key": "gm-secret", "openai_compatible_api_key": "compatible-secret"}


def test_wrong_or_corrupt_file_fails_without_overwrite(root):
    path = store_path(root)
    path.write_bytes(b"not dpapi data")
    with pytest.raises(CredentialError) as error:
        CredentialStore(root).load()
    assert "not dpapi" not in str(error.value)

    with pytest.raises(CredentialError):
        CredentialStore(root).save({"openai_api_key": "new-secret"})
    assert path.read_bytes() == b"not dpapi data"


def test_load_prefers_legacy_then_scrubs_config(root):
    config_path(root).write_text(json.dumps({
        "openai_api_key": "legacy-openai",
        "gemini_api_key": "legacy-gemini",
        "final_speed": 1.25,
        "extra": {"token": "x", "nested": {"openai_api_key": "nested-secret", "keep": [1]}},
    }), encoding="utf-8")
    CredentialStore(root).save({"openai_api_key": "encrypted-old", "gemini_api_key": ""})

    settings = Settings.load(root)
    assert settings.openai_api_key == "legacy-openai"
    assert settings.gemini_api_key == "legacy-gemini"
    assert settings.final_speed == 1.25
    assert settings.extra == {"token": "x", "nested": {"keep": [1]}}
    saved = config_path(root).read_text(encoding="utf-8")
    assert "legacy-openai" not in saved and "legacy-gemini" not in saved
    assert "nested-secret" not in saved


def test_load_without_plaintext_does_not_rewrite_store(root):
    CredentialStore(root).save({"openai_api_key": "encrypted", "gemini_api_key": "gm"})
    config_path(root).write_text(json.dumps({"openai_api_key": "", "gemini_api_key": ""}), encoding="utf-8")
    before = (store_path(root).stat().st_mtime_ns, store_path(root).read_bytes())
    assert Settings.load(root).openai_api_key == "encrypted"
    assert (store_path(root).stat().st_mtime_ns, store_path(root).read_bytes()) == before


def test_missing_config_still_reads_protected_store(root):
    CredentialStore(root).save({"openai_api_key": "only-protected", "gemini_api_key": ""})
    assert Settings.load(root).openai_api_key == "only-protected"


def test_migration_save_failure_preserves_legacy_bytes(root, monkeypatch):
    original = json.dumps({"openai_api_key": "legacy-secret", "final_speed": 1.5})
    config_path(root).write_text(original, encoding="utf-8")

    def fail(self, values):
        raise CredentialError("safe failure")

    monkeypatch.setattr(CredentialStore, "save", fail)
    with pytest.raises(CredentialError) as error:
        Settings.load(root)
    assert "legacy-secret" not in str(error.value)
    assert config_path(root).read_text(encoding="utf-8") == original


def test_scrub_failure_keeps_legacy_and_protected_for_retry(root, monkeypatch):
    original = json.dumps({"openai_api_key": "legacy-secret"})
    config_path(root).write_text(original, encoding="utf-8")
    real_write = models._write_atomic

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(models, "_write_atomic", fail)
    with pytest.raises(CredentialError) as error:
        Settings.load(root)
    assert "legacy-secret" not in str(error.value)
    assert config_path(root).read_text(encoding="utf-8") == original
    assert CredentialStore(root).load()["openai_api_key"] == "legacy-secret"

    monkeypatch.setattr(models, "_write_atomic", real_write)
    assert Settings.load(root).openai_api_key == "legacy-secret"
    assert "legacy-secret" not in config_path(root).read_text(encoding="utf-8")


def test_settings_save_persists_preferences_no_plaintext_or_dynamic_attrs(root):
    settings = Settings(root=root, openai_api_key="open-secret", gemini_api_key="gm-secret",
                        final_speed=1.25,
                        extra={"nested": {"gemini_api_key": "nested", "keep": True},
                               "Api-Key": "loose", "note": "ok"})
    settings.dynamic = "not-a-field"
    settings.save()

    saved = config_path(root).read_text(encoding="utf-8")
    assert "open-secret" not in saved and "gm-secret" not in saved
    assert "nested-secret" not in saved and '"loose"' not in saved
    data = json.loads(saved)
    assert data["final_speed"] == 1.25
    assert data["extra"] == {"nested": {"keep": True}, "note": "ok"}
    assert "openai_api_key" not in data and "gemini_api_key" not in data
    assert "dynamic" not in data

    loaded = Settings.load(root)
    assert loaded.openai_api_key == "open-secret"
    assert loaded.gemini_api_key == "gm-secret"
    assert loaded.extra == {"nested": {"keep": True}, "note": "ok"}


def test_settings_save_refuses_corrupt_protected_store(root):
    path = store_path(root)
    path.write_bytes(b"corrupt")
    with pytest.raises(CredentialError):
        Settings(root=root, openai_api_key="new-secret").save()
    assert path.read_bytes() == b"corrupt"


def test_empty_legacy_key_fields_load_and_do_not_duplicate_kwargs(root):
    config_path(root).write_text(json.dumps({"openai_api_key": "", "gemini_api_key": "",
                                             "final_speed": 1.25}), encoding="utf-8")
    settings = Settings.load(root)
    assert (settings.openai_api_key, settings.gemini_api_key, settings.final_speed) == ("", "", 1.25)


def test_repr_redacts_credentials(root):
    settings = Settings(root=root, openai_api_key="repr-openai", gemini_api_key="repr-gemini",
                        extra={"openai_api_key": "repr-extra"})
    text = repr(settings)
    assert "repr-openai" not in text and "repr-gemini" not in text and "repr-extra" not in text


def test_non_windows_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    (tmp_path / "config").mkdir()
    with pytest.raises(CredentialError):
        CredentialStore(tmp_path).load()
    with pytest.raises(CredentialError):
        CredentialStore(tmp_path).save({"openai_api_key": "x"})
