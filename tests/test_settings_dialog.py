import copy
from dataclasses import fields
import tkinter as tk
from unittest.mock import Mock

import pytest

from subshort.credentials import CREDENTIAL_FIELDS, CredentialError
from subshort.models import Settings
from subshort.settings_dialog import FIELD_REGISTRY, SettingsDialog


@pytest.fixture(scope="session")
def tk_root():
    window = tk.Tk()
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


@pytest.fixture
def parent(tk_root):
    window = tk.Toplevel(tk_root)
    window.withdraw()
    try:
        yield window
    finally:
        window.destroy()


def open_dialog(parent, settings, monkeypatch, action, on_saved=None):
    original_body = SettingsDialog.body
    failures = []

    def body(dialog, master):
        focus = original_body(dialog, master)

        def run():
            try:
                action(dialog)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if dialog.winfo_exists():
                    dialog.cancel()

        dialog.after(10, run)
        return focus

    monkeypatch.setattr(SettingsDialog, "body", body)
    dialog = SettingsDialog(parent, settings, on_saved)
    if failures:
        raise failures[0]
    return dialog


def test_registry_covers_settings_without_credentials():
    editable = {entry[1] for entry in FIELD_REGISTRY}
    current = {field.name for field in fields(Settings)} - {"root", "extra", *CREDENTIAL_FIELDS}
    assert current <= editable
    assert not editable.intersection({"root", "extra", *CREDENTIAL_FIELDS})
    assert not editable.intersection({"merge_words", "merge_segments"})


def test_invalid_value_preserves_settings(parent, tmp_path, monkeypatch):
    settings = Settings(root=tmp_path)
    previous = copy.deepcopy(settings)
    save = Mock()
    errors = Mock()
    monkeypatch.setattr(Settings, "save", save)
    monkeypatch.setattr("subshort.settings_dialog.messagebox.showerror", errors)

    def action(dialog):
        dialog.variables["mode"].set("subtitle")
        dialog.variables["slow_speed"].set("not a number")
        assert dialog.validate() is False
        assert settings == previous

    open_dialog(parent, settings, monkeypatch, action)
    save.assert_not_called()
    errors.assert_called_once()


def test_success_saves_copy_before_updating_settings(parent, tmp_path, monkeypatch):
    (tmp_path / "asr-model").mkdir()
    settings = Settings(root=tmp_path, asr_model="asr-model")
    previous = copy.deepcopy(settings)
    saved = []
    callback = Mock()
    monkeypatch.setattr("subshort.models.shutil.which", lambda command: command)

    def save(candidate):
        assert candidate is not settings
        assert settings == previous
        saved.append(copy.deepcopy(candidate))

    monkeypatch.setattr(Settings, "save", save)

    def action(dialog):
        dialog.variables["mode"].set("subtitle")
        dialog.variables["slow_speed"].set("0.8")
        dialog.variables["final_speed"].set("1.2")
        dialog.variables["keep_original_audio"].set(True)
        dialog.variables["background_volume"].set("0.4")
        dialog.ok()

    dialog = open_dialog(parent, settings, monkeypatch, action, callback)
    assert dialog.result is True
    assert settings.mode == "subtitle"
    assert settings.slow_speed == 0.8
    assert settings.final_speed == 1.2
    assert settings.keep_original_audio is True
    assert settings.background_volume == 0.4
    assert saved == [settings]
    callback.assert_called_once_with()


@pytest.mark.parametrize("error", [ValueError("invalid configuration"), CredentialError("safe error")])
def test_failed_validation_or_save_preserves_settings(parent, tmp_path, monkeypatch, error):
    settings = Settings(root=tmp_path)
    previous = copy.deepcopy(settings)
    callback = Mock()
    errors = Mock()
    monkeypatch.setattr("subshort.settings_dialog.messagebox.showerror", errors)
    monkeypatch.setattr("subshort.models.shutil.which", lambda command: command)
    if isinstance(error, CredentialError):
        monkeypatch.setattr(Settings, "save", Mock(side_effect=error))
    else:
        monkeypatch.setattr(Settings, "validate", Mock(side_effect=error))
    
    def action(dialog):
        dialog.variables["mode"].set("dub")
        assert dialog.validate() is False
        assert settings == previous

    open_dialog(parent, settings, monkeypatch, action, callback)
    errors.assert_called_once()
    callback.assert_not_called()


def test_cancellation_keeps_settings(parent, tmp_path, monkeypatch):
    settings = Settings(root=tmp_path)
    previous = copy.deepcopy(settings)
    save = Mock()
    callback = Mock()
    monkeypatch.setattr(Settings, "save", save)

    def action(dialog):
        dialog.variables["mode"].set("dub")
        dialog.variables["keep_original_audio"].set(True)
        dialog.cancel()

    open_dialog(parent, settings, monkeypatch, action, callback)
    assert settings == previous
    save.assert_not_called()
    callback.assert_not_called()
