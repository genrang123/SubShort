import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from subshort.settings_dialog import SettingsDialog
from subshort.models import Settings
from subshort.credentials import CredentialError
from subshort import providers
from subshort.ui import apply_theme


def test_dialog_save_reopen_and_delete(tmp_path, qapp):
    settings = Settings(root=tmp_path)
    dialog = SettingsDialog(None, settings)
    entry, _ = dialog.key_fields['openai_api_key']
    assert entry.text() == ''
    assert entry.echoMode() == QLineEdit.EchoMode.Password
    entry.setText('test-dialog-secret')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert settings.openai_api_key == 'test-dialog-secret'
    loaded = Settings.load(tmp_path)
    assert loaded.openai_api_key == 'test-dialog-secret'
    dialog = SettingsDialog(None, loaded)
    entry, delete = dialog.key_fields['openai_api_key']
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
    monkeypatch.setattr(SettingsDialog, '_show_error', lambda self, text: results.append(text))

    def fail(candidate):
        raise CredentialError('safe error')
    monkeypatch.setattr(Settings, 'save', fail)
    dialog = SettingsDialog(None, settings)
    dialog.key_fields['openai_api_key'][0].setText('new-secret')
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.reject()
    assert settings.openai_api_key == 'old-secret'
    assert results and 'new-secret' not in str(results) and 'old-secret' not in str(results)


def test_dialog_cancel_does_not_replace_saved_key(tmp_path, qapp):
    settings = Settings(root=tmp_path, openai_api_key='retained-secret')
    settings.save()
    protected = tmp_path / 'config/credentials.dpapi'
    before = protected.read_bytes()
    dialog = SettingsDialog(None, settings)
    dialog.key_fields['openai_api_key'][0].setText('discarded-secret')
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert settings.openai_api_key == 'retained-secret'
    assert protected.read_bytes() == before
    assert Settings.load(tmp_path).openai_api_key == 'retained-secret'


@pytest.fixture
def model_catalog():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get('Authorization')))
            if self.path == '/v1/redirect/models':
                self.send_response(302)
                self.send_header('Location', '/v1/models')
                self.end_headers()
                return
            body = json.dumps({'data': [{'id': 'other-model'}, {'id': 'local-model'}, {'id': 'local-model'}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1', requests
        finally:
            server.shutdown()
            worker.join()


def test_compatible_configuration_saved_together(tmp_path, qapp, monkeypatch, model_catalog):
    base_url, requests = model_catalog
    settings = Settings(root=tmp_path, ffmpeg='missing-ffmpeg.exe', asr_model='missing-model')
    dialog = SettingsDialog(None, settings, api_provider='')
    url = dialog.editors['translation_base_url']
    assert url.isHidden()
    dialog.editors['translation_provider'].setCurrentText('OpenAI-compatible')
    assert not url.isHidden()
    model = dialog.editors['translation_model']
    url.setText(base_url + '/redirect')
    dialog.key_fields['openai_compatible_api_key'][0].setText('local')
    errors = []
    monkeypatch.setattr(dialog, '_show_error', errors.append)
    dialog.accept()
    assert errors == ['Cần chọn model cho OpenAI-compatible']
    assert settings.translation_provider == 'openai'
    assert not (tmp_path / 'config/credentials.dpapi').exists()
    fetch = dialog.findChild(QPushButton, 'fetch_translation_model')

    def load():
        fetch.click()
        for _ in range(200):
            if fetch.isEnabled():
                break
            QTest.qWait(25)
        assert fetch.isEnabled()

    load()
    assert requests == [('/v1/redirect/models', 'Bearer local')]
    assert model.currentData() is None
    url.setText(base_url)
    load()
    assert requests[-1] == ('/v1/models', 'Bearer local')
    assert [model.itemData(i) for i in range(model.count())] == ['local-model', 'other-model']
    assert model.currentData() is None
    model.setCurrentIndex(model.findData('local-model'))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    loaded = Settings.load(tmp_path)
    assert loaded.translation_provider == 'openai_compatible'
    assert loaded.translation_base_url == base_url
    assert loaded.translation_model == 'local-model'
    assert loaded.openai_compatible_api_key == 'local'


def test_long_model_popup_stays_inside_compact_settings(tmp_path, qapp):
    theme = qapp.style().objectName(), qapp.font(), qapp.palette(), qapp.styleSheet()
    apply_theme(qapp)
    dialog = SettingsDialog(None, Settings(root=tmp_path), api_provider='')
    try:
        dialog.resize(520, 290)
        dialog.show()
        qapp.processEvents()
        model = dialog.editors['vision_model']
        model.clear()
        for index in range(137):
            model.addItem(f'Model {index}', f'model-{index}')
        dialog.api_page.ensureWidgetVisible(model, 0, 24)
        model.setFocus()
        QTest.keyClick(model, Qt.Key.Key_F4)
        popup = model.view().window()
        for _ in range(40):
            if popup.isVisible():
                break
            QTest.qWait(25)
        assert popup.isVisible()
        bounds = QRect(dialog.mapToGlobal(dialog.rect().topLeft()), dialog.size())
        assert bounds.contains(popup.geometry()), f'Popup {popup.geometry()} exceeds settings {bounds}'
        QTest.keyClick(model.view(), Qt.Key.Key_End)
        QTest.keyClick(model.view(), Qt.Key.Key_Return)
        assert model.currentData() == 'model-136'
    finally:
        dialog.reject()
        style, font, palette, stylesheet = theme
        qapp.setStyle(style)
        qapp.setFont(font)
        qapp.setPalette(palette)
        qapp.setStyleSheet(stylesheet)


@pytest.mark.parametrize(('provider', 'endpoint'), [
    ('openai', 'https://api.openai.com/v1/models'),
    ('gemini', 'https://generativelanguage.googleapis.com/v1beta/openai/models'),
])
def test_vision_picker_uses_its_provider_and_keeps_saved_model(tmp_path, qapp, monkeypatch, provider, endpoint):
    def run(request, *, timeout):
        request = json.loads(request)
        if request['url'] != endpoint or request['headers'].get('Authorization') != 'Bearer vision-key':
            return '{"ok":false,"status":401,"body":""}'
        return json.dumps({'ok': True, 'body': json.dumps({'data': [{'id': 'new-vision-model'}]})})

    monkeypatch.setattr(providers, '_run_worker', run)
    settings = Settings(root=tmp_path, vision_provider=provider, vision_model='old-vision-model',
                        **{f'{provider}_api_key': 'vision-key'})
    dialog = SettingsDialog(None, settings, api_provider='')
    model = dialog.editors['vision_model']
    dialog.findChild(QPushButton, 'fetch_vision_model').click()
    for _ in range(200):
        if model.findData('new-vision-model') != -1:
            break
        QTest.qWait(25)
    assert model.currentData() == 'old-vision-model'
    index = model.findData('new-vision-model')
    assert index != -1
    model.setCurrentIndex(index)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert Settings.load(tmp_path).vision_model == 'new-vision-model'
