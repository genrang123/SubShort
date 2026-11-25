import copy
from dataclasses import fields
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QInputDialog, QLineEdit, QMessageBox, QPushButton,
    QTabWidget, QWidget,
)

from subshort.credentials import CREDENTIAL_FIELDS, CredentialError
from subshort.models import Settings
from subshort.settings_dialog import FIELD_REGISTRY, SettingsDialog


@pytest.fixture
def parent(qapp):
    window = QWidget()
    yield window
    window.close()


@pytest.fixture
def errors(monkeypatch):
    messages = []

    def capture(box):
        messages.append(box.text())
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", capture)
    return messages


def click_button(dialog, role):
    button_box = dialog.findChild(QDialogButtonBox)
    button = next(button for button in button_box.buttons() if button_box.buttonRole(button) == role)
    button.click()


def save_dialog(dialog):
    click_button(dialog, QDialogButtonBox.ButtonRole.AcceptRole)


def test_registry_covers_settings_without_credentials():
    editable = {entry[1] for entry in FIELD_REGISTRY}
    current = {field.name for field in fields(Settings)} - {
        "root", "extra", *CREDENTIAL_FIELDS, "asr_model", "asr_aligner", "asr_vad_model", "firered_type",
        "qwen_python", "firered_python",
    }
    assert editable == current


def test_dialog_tabs_and_editors(parent, tmp_path):
    settings = Settings(root=tmp_path, mode="dub", keep_original_audio=True, asr_model="model with spaces")
    dialog = SettingsDialog(parent, settings)
    tabs = dialog.findChild(QTabWidget)
    groups = ["Chung", "API & dịch vụ", "Nhận dạng", "Người nói", "Giọng đọc", "Âm thanh / Đầu ra",
              "Đồng bộ", "Video", "Phụ đề cũ", "Hook trích văn bản"]
    assert dialog.isModal()
    assert [tabs.tabText(index).replace('&&', '&') for index in range(tabs.count())] == groups
    for group, name, _, value_type, choices in FIELD_REGISTRY:
        page = tabs.widget(groups.index(group)).widget()
        value = getattr(settings, name)
        if choices:
            editor = page.findChild(QComboBox, name)
            assert not editor.isEditable()
            assert [editor.itemData(index) for index in range(editor.count())] == list(choices)
            assert editor.currentData() == value
        elif name in ('translation_model', 'vision_model', 'source_language', 'target_language',
                     'tts_voice', 'tts_custom_voice'):
            editor = page.findChild(QComboBox, name)
            assert not editor.isEditable()
            assert editor.currentData() == value
        elif value_type is bool:
            assert page.findChild(QCheckBox, name).isChecked() is value
        else:
            assert page.findChild(QLineEdit, name).text() == str(value)
    recognition = tabs.widget(groups.index("Nhận dạng")).widget()
    model = recognition.findChild(QComboBox, "asr_provider")
    assert recognition.layout().labelForField(model).text() == "Model"
    assert [model.itemText(i) for i in range(model.count())] == [
        "faster-whisper-small", "Qwen3-ASR-0.6B", "FireRedASR-AED-L",
    ]
    for hidden in ("asr_model", "asr_aligner", "asr_vad_model", "firered_type", "qwen_python", "firered_python"):
        assert recognition.findChild(QWidget, hidden) is None


def test_invalid_value_preserves_settings(parent, tmp_path, monkeypatch, errors):
    settings = Settings(root=tmp_path)
    previous = copy.deepcopy(settings)
    save = Mock()
    callback = Mock()
    monkeypatch.setattr(Settings, "save", save)

    dialog = SettingsDialog(parent, settings, on_saved=callback)
    dialog.show()
    dialog.findChild(QComboBox, "mode").setCurrentText("Chỉ phụ đề")
    dialog.findChild(QLineEdit, "slow_speed").setText("not a number")
    save_dialog(dialog)

    assert dialog.isVisible()
    assert settings == previous
    save.assert_not_called()
    callback.assert_not_called()
    assert len(errors) == 1 and errors[0].endswith(": giá trị không hợp lệ")
    dialog.reject()


def test_success_saves_copy_before_updating_settings(parent, tmp_path, monkeypatch, errors):
    model_path = tmp_path / "ModelASR" / "Qwen3-ASR-0.6B"
    settings = Settings(root=tmp_path, asr_model="previous-whisper-path")
    previous = copy.deepcopy(settings)
    saved = []
    callback_state = []
    monkeypatch.setattr("subshort.models.shutil.which", lambda command: command)
    original_save = Settings.save

    def save(candidate):
        live_before_save = copy.deepcopy(settings)
        original_save(candidate)
        saved.append((candidate is settings, live_before_save, copy.deepcopy(candidate)))

    def on_saved():
        callback_state.append((copy.deepcopy(settings), Settings.load(tmp_path)))

    callback = Mock(side_effect=on_saved)
    monkeypatch.setattr(Settings, "save", save)

    dialog = SettingsDialog(parent, settings, on_saved=callback)
    dialog.findChild(QComboBox, "mode").setCurrentText("Chỉ phụ đề")
    dialog.findChild(QComboBox, "asr_provider").setCurrentText("Qwen3-ASR-0.6B")
    dialog.findChild(QLineEdit, "slow_speed").setText("0.8")
    dialog.findChild(QLineEdit, "final_speed").setText("1.2")
    dialog.findChild(QCheckBox, "keep_original_audio").setChecked(True)
    dialog.findChild(QLineEdit, "background_volume").setText("0.4")
    dialog.findChild(QLineEdit, "output_dir").setText("output with spaces")
    dialog.findChild(QLineEdit, "hook_seed").setText("-15")
    save_dialog(dialog)
    assert settings == previous
    assert not saved
    assert errors and "Qwen3-ASR-0.6B" in errors[0]
    callback.assert_not_called()

    model_path.mkdir(parents=True)
    (model_path.parent / "Qwen3-ForcedAligner-0.6B").mkdir()
    (model_path.parent / "silero_vad.jit").touch()
    save_dialog(dialog)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert settings.mode == "subtitle"
    assert settings.slow_speed == 0.8
    assert settings.final_speed == 1.2
    assert settings.keep_original_audio is True
    assert settings.background_volume == 0.4
    assert settings.asr_provider == "qwen"
    assert Settings.load(tmp_path).asr_assets()[0] == model_path
    assert settings.output_dir == "output with spaces"
    assert settings.hook_seed == -15
    assert saved == [(False, previous, settings)]
    assert callback_state == [(settings, settings)]
    callback.assert_called_once_with()


@pytest.mark.parametrize(("stage", "error", "message"), [
    ("validate", ValueError("invalid configuration"), "invalid configuration"),
    ("save", OSError("write failed"), "write failed"),
    ("save", CredentialError("sensitive error"),
     "Không lưu được cấu hình an toàn. Cài đặt hiện tại được giữ nguyên."),
])
def test_failed_validation_or_save_preserves_settings(parent, tmp_path, monkeypatch, errors, stage, error, message):
    (tmp_path / "asr-model").mkdir()
    settings = Settings(root=tmp_path, asr_model="asr-model")
    previous = copy.deepcopy(settings)
    callback = Mock()
    monkeypatch.setattr("subshort.models.shutil.which", lambda command: command)
    save = Mock(side_effect=error if stage == "save" else None)
    monkeypatch.setattr(Settings, "save", save)
    if stage == "validate":
        monkeypatch.setattr(Settings, "validate", Mock(side_effect=error))

    dialog = SettingsDialog(parent, settings, on_saved=callback)
    dialog.show()
    dialog.findChild(QComboBox, "mode").setCurrentText("Chỉ lồng tiếng")
    save_dialog(dialog)

    assert dialog.isVisible()
    assert settings == previous
    assert errors == [message]
    assert save.call_count == (stage == "save")
    callback.assert_not_called()
    dialog.reject()


def test_unsupported_stored_choice_is_not_replaced_silently(parent, tmp_path, monkeypatch, errors):
    settings = Settings(root=tmp_path, mode="legacy")
    save = Mock()
    monkeypatch.setattr(Settings, "save", save)
    dialog = SettingsDialog(parent, settings)

    save_dialog(dialog)

    assert settings.mode == "legacy"
    assert errors and "mode" in errors[0]
    save.assert_not_called()


@pytest.mark.parametrize("escape", [False, True])
def test_cancellation_keeps_settings(parent, tmp_path, monkeypatch, escape):
    settings = Settings(root=tmp_path)
    previous = copy.deepcopy(settings)
    save = Mock()
    callback = Mock()
    monkeypatch.setattr(Settings, "save", save)

    dialog = SettingsDialog(parent, settings, on_saved=callback)
    dialog.show()
    dialog.findChild(QComboBox, "mode").setCurrentText("Chỉ lồng tiếng")
    dialog.findChild(QCheckBox, "keep_original_audio").setChecked(True)
    if escape:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        click_button(dialog, QDialogButtonBox.ButtonRole.RejectRole)

    assert not dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert settings == previous
    save.assert_not_called()
    callback.assert_not_called()


def test_multilingual_edge_selection_persists(parent, tmp_path, monkeypatch):
    async def list_voices():
        return [
            {'ShortName': 'en-US-AriaNeural', 'FriendlyName': 'Aria', 'Locale': 'en-US'},
            {'ShortName': 'ja-JP-NanamiNeural', 'FriendlyName': 'Nanami', 'Locale': 'ja-JP'},
        ]

    monkeypatch.setattr('edge_tts.list_voices', list_voices)
    monkeypatch.setattr(Settings, 'validate', lambda self: None)
    dialog = SettingsDialog(parent, Settings(root=tmp_path))
    source, target, voices = (dialog.editors[name] for name in ('source_language', 'target_language', 'tts_voice'))
    assert source.currentText() == 'Tự nhận diện'
    source.setCurrentIndex(source.findData('es'))
    assert dialog.editors['tts_custom_voice'].isEnabled() is False
    target.setCurrentIndex(target.findData('ja'))
    fetch = dialog.findChild(QPushButton, 'fetch_tts_voice')
    fetch.click()
    for _ in range(120):
        if fetch.isEnabled():
            break
        QTest.qWait(25)
    assert voices.itemData(0) == 'ja-JP-NanamiNeural'
    assert voices.itemData(1) == 'en-US-AriaNeural'
    assert voices.currentData() == 'vi-VN-HoaiMyNeural'
    voices.setCurrentIndex(0)
    save_dialog(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = Settings.load(tmp_path)
    assert (saved.source_language, saved.target_language, saved.tts_voice) == ('es', 'ja', 'ja-JP-NanamiNeural')


def test_other_target_and_recorded_voice_use_stable_ids(parent, tmp_path, monkeypatch):
    recording_id = '6b93a608-01b7-4e08-8771-ed26fc5c6f92'
    monkeypatch.setattr('subshort.settings_dialog.VoiceRegistry.list', lambda self: [
        {'id': recording_id, 'name': 'Bản thu tiếng Nhật', 'provider': 'omnivoice'},
    ])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('is', True))
    monkeypatch.setattr(Settings, 'validate', lambda self: None)
    dialog = SettingsDialog(parent, Settings(root=tmp_path, tts_provider='vieneu', source_language='uk'))
    source, target, recordings = (dialog.editors[name] for name in ('source_language', 'target_language', 'tts_custom_voice'))
    asr = dialog.editors['asr_provider']
    asr.setCurrentIndex(asr.findData('qwen'))
    assert source.currentData() == '' and source.findData('uk') == -1 and source.findData('ja') >= 0
    target.setCurrentIndex(target.findText('Khác…'))
    assert recordings.findData(recording_id) == -1
    provider = dialog.editors['tts_provider']
    provider.setCurrentIndex(provider.findData('omnivoice'))
    assert recordings.itemText(recordings.findData(recording_id)) == 'Bản thu tiếng Nhật'
    recordings.setCurrentIndex(recordings.findData(recording_id))
    save_dialog(dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = Settings.load(tmp_path)
    assert (saved.target_language, saved.tts_custom_voice) == ('is', recording_id)
