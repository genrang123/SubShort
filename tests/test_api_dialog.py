from PySide6.QtWidgets import QDialog, QLineEdit

from subshort.app import CredentialDialog
from subshort.models import Settings
from subshort.credentials import CredentialError


def test_dialog_save_reopen_and_delete(tmp_path, qapp):
    settings = Settings(root=tmp_path)
    dialog = CredentialDialog(None, settings)
    entry, _ = dialog.fields['openai_api_key']
    assert entry.text() == ''
    assert entry.echoMode() == QLineEdit.EchoMode.Password
    entry.setText('test-dialog-secret')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert settings.openai_api_key == 'test-dialog-secret'
    loaded = Settings.load(tmp_path)
    assert loaded.openai_api_key == 'test-dialog-secret'
    dialog = CredentialDialog(None, loaded)
    entry, delete = dialog.fields['openai_api_key']
    assert entry.text() == ''
    assert entry.echoMode() == QLineEdit.EchoMode.Password
    delete.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert loaded.openai_api_key == ''
    assert Settings.load(tmp_path).openai_api_key == ''
    assert 'test-dialog-secret' not in (tmp_path / 'config/settings.json').read_text(encoding='utf-8')


def test_dialog_failed_save_preserves_runtime_key(tmp_path, qapp, monkeypatch):
    settings = Settings(root=tmp_path, openai_api_key='old-secret')
    results = []
    monkeypatch.setattr('subshort.app.show_message', lambda parent, title, text, **kwargs: results.append((title, text)))

    def fail():
        raise CredentialError('safe error')
    monkeypatch.setattr(settings, 'save', fail)
    dialog = CredentialDialog(None, settings)
    dialog.fields['openai_api_key'][0].setText('new-secret')
    assert dialog.validate() is False
    dialog.reject()
    assert settings.openai_api_key == 'old-secret'
    assert results and 'new-secret' not in str(results) and 'old-secret' not in str(results)


def test_dialog_cancel_does_not_replace_saved_key(tmp_path, qapp):
    settings = Settings(root=tmp_path, openai_api_key='retained-secret')
    settings.save()
    protected = tmp_path / 'config/credentials.dpapi'
    before = protected.read_bytes()
    dialog = CredentialDialog(None, settings)
    dialog.fields['openai_api_key'][0].setText('discarded-secret')
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert settings.openai_api_key == 'retained-secret'
    assert protected.read_bytes() == before
    assert Settings.load(tmp_path).openai_api_key == 'retained-secret'


def test_compatible_configuration_saved_together(tmp_path, qapp):
    settings = Settings(root=tmp_path)
    dialog = CredentialDialog(None, settings)
    dialog.api_variables['translation_provider'].setCurrentText('openai_compatible')
    dialog.api_variables['translation_base_url'].setText('http://127.0.0.1:1234/v1')
    dialog.api_variables['translation_model'].setText('local-model')
    dialog.fields['openai_compatible_api_key'][0].setText('local')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    loaded = Settings.load(tmp_path)
    assert loaded.translation_provider == 'openai_compatible'
    assert loaded.translation_base_url == 'http://127.0.0.1:1234/v1'
    assert loaded.translation_model == 'local-model'
    assert loaded.openai_compatible_api_key == 'local'
