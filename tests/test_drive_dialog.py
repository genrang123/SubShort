from types import SimpleNamespace

import pytest

from subshort import drive_dialog


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    calls = []
    settings = SimpleNamespace(drive_folder_id="old", save=lambda: calls.append("save"))
    manager = SimpleNamespace(settings=settings, start=lambda *args: calls.append(args) or True)
    dialog = object.__new__(drive_dialog.DriveDialog)
    dialog.manager, dialog.root, dialog.job_id = manager, tmp_path, "job-id"
    dialog.folder, dialog.consent = Variable("folder"), Variable(False)
    monkeypatch.setattr(drive_dialog.messagebox, "showerror", lambda *a, **kw: calls.append("error"))
    monkeypatch.setattr(drive_dialog.messagebox, "showinfo", lambda *a, **kw: calls.append("info"))
    return dialog, calls


def test_explicit_consent_required(dialog):
    value, calls = dialog
    assert value.validate() is False
    assert calls == ["error"]
    assert value.manager.settings.drive_folder_id == "old"


def test_confirmed_upload_saves_only_folder_preference(dialog):
    value, calls = dialog
    value.consent.value = True
    assert value.validate() is True
    assert calls == ["save", ("job-id", "folder")]
    assert value.manager.settings.drive_folder_id == "folder"


def test_import_does_not_grant_consent_or_start_upload(dialog, monkeypatch):
    value, calls = dialog
    monkeypatch.setattr(drive_dialog.filedialog, "askopenfilename", lambda **kw: "account.json")
    monkeypatch.setattr(drive_dialog, "import_service_account", lambda *args: calls.append("import"))
    value.import_account()
    assert calls == ["import", "info"]
    assert value.consent.get() is False


def test_save_failure_restores_preference_and_never_uploads(dialog):
    value, calls = dialog
    value.consent.value = True

    def fail():
        raise OSError("secret")

    value.manager.settings.save = fail
    assert value.validate() is False
    assert value.manager.settings.drive_folder_id == "old"
    assert calls == ["error"]


def test_missing_job_and_invalid_folder_never_upload(dialog):
    value, calls = dialog
    value.consent.value = True
    value.job_id = None
    assert value.validate() is False
    value.job_id, value.folder.value = "job-id", "bad/folder"
    assert value.validate() is False
    assert calls == ["error", "error"]
