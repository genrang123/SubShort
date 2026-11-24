from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog

from subshort import drive_dialog


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    calls = []
    settings = SimpleNamespace(drive_folder_id="old", save=lambda: calls.append("save"))
    manager = SimpleNamespace(settings=settings, running=False,
                              start=lambda *args: calls.append(args) or True,
                              cancel=lambda: calls.append("cancel"))
    dialog = drive_dialog.DriveDialog(None, manager, tmp_path, "job-id")
    assert dialog.folder.text() == "old"
    dialog.folder.setText("folder")

    def message(parent, title, text, icon=drive_dialog.QMessageBox.Icon.Critical):
        calls.append(("info" if icon == drive_dialog.QMessageBox.Icon.Information else "error", text))

    monkeypatch.setattr(drive_dialog, "show_message", message)
    yield dialog, calls
    dialog.close()


def test_explicit_consent_required(dialog):
    value, calls = dialog
    assert not value.consent.isChecked()
    value.upload_button.click()
    assert calls == [("error", "Cần đồng ý rõ ràng trước khi gửi video.")]
    assert value.result() != QDialog.DialogCode.Accepted
    assert value.manager.settings.drive_folder_id == "old"


def test_confirmed_upload_saves_only_folder_preference(dialog):
    value, calls = dialog
    value.consent.setChecked(True)
    value.folder.setText(" folder ")
    value.upload_button.click()
    assert calls == ["save", ("job-id", "folder")]
    assert value.result() == QDialog.DialogCode.Accepted
    assert value.manager.settings.drive_folder_id == "folder"


def test_import_does_not_grant_consent_or_start_upload(dialog, monkeypatch):
    value, calls = dialog
    monkeypatch.setattr(drive_dialog.QFileDialog, "getOpenFileName", lambda *args: ("account.json", "JSON"))
    monkeypatch.setattr(drive_dialog, "import_service_account", lambda *args: calls.append(args))
    value.import_button.click()
    assert calls[0] == (value.root, Path("account.json"))
    assert calls[1:] == [("info", "Đã lưu credential được bảo vệ. Chưa có video nào được gửi.")]
    assert not value.consent.isChecked()
    assert value.result() != QDialog.DialogCode.Accepted


def test_import_failure_does_not_reveal_credentials(dialog, monkeypatch):
    value, calls = dialog
    monkeypatch.setattr(drive_dialog.QFileDialog, "getOpenFileName", lambda *args: ("account.json", "JSON"))

    def fail(*args):
        raise drive_dialog.DriveError("secret")

    monkeypatch.setattr(drive_dialog, "import_service_account", fail)
    value.import_button.click()
    assert calls == [("error", "Không nhập được service account hợp lệ; credential cũ được giữ nguyên.")]
    assert not value.consent.isChecked()


def test_save_failure_restores_preference_and_never_uploads(dialog):
    value, calls = dialog
    value.consent.setChecked(True)

    def fail():
        raise OSError("secret")

    value.manager.settings.save = fail
    value.upload_button.click()
    assert value.manager.settings.drive_folder_id == "old"
    assert value.result() != QDialog.DialogCode.Accepted
    assert calls == [("error", "Không bắt đầu được tải lên. Kiểm tra thư mục và video đã chọn.")]


def test_missing_job_and_invalid_folder_never_upload(dialog):
    value, calls = dialog
    value.consent.setChecked(True)
    value.job_id = None
    value.upload_button.click()
    value.job_id = "job-id"
    value.folder.setText("bad/folder")
    value.upload_button.click()
    assert value.result() != QDialog.DialogCode.Accepted
    assert [kind for kind, _ in calls] == ["error", "error"]


def test_active_upload_can_be_cancelled_without_starting_another(dialog):
    value, calls = dialog
    value.manager.running = True
    value.show()
    assert not value.folder.isEnabled()
    assert not value.consent.isEnabled()
    assert not value.upload_button.isEnabled()
    assert not value.import_button.isEnabled()
    value.cancel_upload_button.click()
    assert calls == ["cancel"]
    assert value.isVisible()


def test_manager_can_refuse_a_second_upload(dialog):
    value, calls = dialog
    value.manager.start = lambda *args: False
    value.consent.setChecked(True)
    value.upload_button.click()
    assert value.result() != QDialog.DialogCode.Accepted
    assert calls == ["save", ("error", "Đang có một video được tải lên.")]
