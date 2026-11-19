import tkinter as tk
import pytest

from subshort.app import CredentialDialog
from subshort.models import Settings
from subshort.credentials import CredentialError

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



def test_dialog_save_reopen_and_delete(tmp_path, parent, monkeypatch):
    settings = Settings(root=tmp_path)
    original = CredentialDialog.body
    errors = []
    actions = iter(('save', 'delete'))

    def body(dialog, master):
        focus = original(dialog, master)
        action = next(actions)
        def interact():
            try:
                entry, delete = dialog.fields['openai_api_key']
                assert entry.get() == ''
                assert entry['show'] == '*'
                if action == 'save':
                    entry.insert(0, 'test-dialog-secret')
                else:
                    delete.set(True)
                dialog.ok()
            except Exception as exc:
                errors.append(exc)
                dialog.cancel()
        dialog.after(10, interact)
        return focus

    monkeypatch.setattr(CredentialDialog, 'body', body)
    CredentialDialog(parent, settings)
    assert settings.openai_api_key == 'test-dialog-secret'
    loaded = Settings.load(tmp_path)
    assert loaded.openai_api_key == 'test-dialog-secret'
    CredentialDialog(parent, loaded)
    assert loaded.openai_api_key == ''
    assert Settings.load(tmp_path).openai_api_key == ''
    assert 'test-dialog-secret' not in (tmp_path / 'config/settings.json').read_text(encoding='utf-8')
    assert not errors


def test_dialog_failed_save_preserves_runtime_key(tmp_path, parent, monkeypatch):
    settings = Settings(root=tmp_path, openai_api_key='old-secret')
    original = CredentialDialog.body
    results = []
    monkeypatch.setattr('subshort.app.messagebox.showerror', lambda *args, **kwargs: results.append(args))
    def fail():
        raise CredentialError('safe error')
    monkeypatch.setattr(settings, 'save', fail)
    def body(dialog, master):
        focus = original(dialog, master)
        def interact():
            dialog.fields['openai_api_key'][0].insert(0, 'new-secret')
            results.append(dialog.validate())
            dialog.cancel()
        dialog.after(10, interact)
        return focus
    monkeypatch.setattr(CredentialDialog, 'body', body)
    CredentialDialog(parent, settings)
    assert settings.openai_api_key == 'old-secret'
    assert results[-1] is False
    assert 'new-secret' not in str(results) and 'old-secret' not in str(results)


def test_dialog_cancel_does_not_replace_saved_key(tmp_path, parent, monkeypatch):
    settings = Settings(root=tmp_path, openai_api_key='retained-secret')
    settings.save()
    protected = tmp_path / 'config/credentials.dpapi'
    before = protected.read_bytes()
    original = CredentialDialog.body
    def body(dialog, master):
        focus = original(dialog, master)
        def interact():
            dialog.fields['openai_api_key'][0].insert(0, 'discarded-secret')
            dialog.cancel()
        dialog.after(10, interact)
        return focus
    monkeypatch.setattr(CredentialDialog, 'body', body)
    CredentialDialog(parent, settings)
    assert settings.openai_api_key == 'retained-secret'
    assert protected.read_bytes() == before
    assert Settings.load(tmp_path).openai_api_key == 'retained-secret'


def test_compatible_configuration_saved_together(tmp_path, parent, monkeypatch):
    settings = Settings(root=tmp_path)
    original = CredentialDialog.body

    def body(dialog, master):
        focus = original(dialog, master)

        def save():
            dialog.api_variables['translation_provider'].set('openai_compatible')
            dialog.api_variables['translation_base_url'].set('http://127.0.0.1:1234/v1')
            dialog.api_variables['translation_model'].set('local-model')
            dialog.fields['openai_compatible_api_key'][0].insert(0, 'local')
            dialog.ok()

        dialog.after(50, save)
        return focus

    monkeypatch.setattr(CredentialDialog, 'body', body)
    CredentialDialog(parent, settings)
    loaded = Settings.load(tmp_path)
    assert loaded.translation_provider == 'openai_compatible'
    assert loaded.translation_base_url == 'http://127.0.0.1:1234/v1'
    assert loaded.translation_model == 'local-model'
    assert loaded.openai_compatible_api_key == 'local'
