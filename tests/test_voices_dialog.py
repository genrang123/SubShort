import threading
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QPushButton

from subshort import voice_preview, voices_dialog


def wait_until(predicate, attempts=200):
    for _ in range(attempts):
        if predicate():
            return True
        QTest.qWait(10)
    return predicate()


def wait_until_enabled(button):
    wait_until(button.isEnabled)


def card(dialog, key):
    return dialog.cards[key]


def visible_titles(dialog):
    return [item.controls[0].text() for item in dialog.cards.values() if not item.isHidden()]


def card_button(item, text):
    return next(button for button in item.findChildren(QPushButton) if button.text() == text)


def choose(dialog, key):
    card(dialog, key).controls[0].click()


def test_presets_load_on_demand_without_locking_local_voices(qapp, tmp_path, monkeypatch):
    gui_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    worker_threads = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def voices(self):
            worker_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
            return [{"id": "preset", "label": "Test voice"}, None]

    monkeypatch.setattr(voices_dialog, "TtsSession", lambda *args, **kwargs: Session())
    record = {"name": "Local voice", "provider": "vieneu", "id": "local"}
    monkeypatch.setattr(voices_dialog.VoiceRegistry, "list", lambda self: [record])
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe", save=lambda: None)
    dialog = voices_dialog.VoicesDialog(None, settings)
    local = ("vieneu", "recording", "local")
    preset = ("vieneu", "preset", "preset")
    assert set(dialog.cards) == {local}
    assert visible_titles(dialog) == []
    dialog.show()
    try:
        assert not entered.is_set()
        dialog.tabs.setCurrentIndex(1)
        choose(dialog, local)
        assert dialog.select_button.isEnabled()
        dialog.tabs.setCurrentIndex(0)
        dialog.load_presets_button.click()
        assert entered.wait(2)
        assert dialog.add_button.isEnabled()
        assert dialog.close_button.isEnabled()
    finally:
        release.set()

    assert wait_until(lambda: preset in dialog.cards)
    assert visible_titles(dialog) == ["Test voice"]
    assert len(worker_threads) == 1 and worker_threads[0] != gui_thread
    dialog.tabs.setCurrentIndex(1)
    choose(dialog, local)
    dialog.select_button.click()
    assert settings.tts_custom_voice == "local"
    assert not dialog.isVisible()

    reopened = voices_dialog.VoicesDialog(None, settings, preset_cache=dialog.preset_cache)
    assert reopened.tabs.currentIndex() == 1
    assert reopened.pending is None
    assert not reopened.select_button.isEnabled()
    reopened.tabs.setCurrentIndex(0)
    choose(reopened, preset)
    reopened.select_button.click()
    assert settings.tts_voice == "preset"
    assert settings.tts_custom_voice == ""
    assert "Test voice" in reopened.current_voice.text()
    assert reopened.close()
    assert not reopened.isVisible()


def test_reopen_reuses_presets_only_for_same_model(qapp, tmp_path, monkeypatch):
    calls = []
    settings = SimpleNamespace(root=tmp_path, ffmpeg='ffmpeg', ffprobe='ffprobe',
                               tts_provider='vieneu', tts_model='first', save=lambda: None)
    monkeypatch.setattr(voices_dialog, '_load_presets', lambda execution, python, options:
                        (calls.append(options['model_path']) or [{'id': 'preset', 'label': 'Test voice'}], None))
    cache = {}
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    dialog.load_presets_button.click()
    assert wait_until(lambda: 'Test voice' in visible_titles(dialog))
    dialog.close()
    reopened = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    assert 'Test voice' in visible_titles(reopened)
    assert calls == ['first']
    reopened.close()
    settings.tts_model = 'second'
    changed = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    assert changed.cards == {}
    assert visible_titles(changed) == []
    changed.close()


def test_add_requires_consent_and_waits_for_normalization(qapp, tmp_path, monkeypatch):
    release = threading.Event()
    add_calls, consent_states, removed, saved = [], [], [], []

    def add(*args):
        add_calls.append(args)
        assert release.wait(5)
        return {"name": "Test clip", "provider": "vieneu", "id": "custom"}

    registry = SimpleNamespace(list=lambda: [], add=add, remove=removed.append)
    monkeypatch.setattr(voices_dialog, "VoiceRegistry", lambda *args: registry)
    source = tmp_path / "voice.wav"
    monkeypatch.setattr(voices_dialog.QFileDialog, "getOpenFileName", lambda *args: (str(source), "Audio"))
    monkeypatch.setattr(voices_dialog.QInputDialog, "getText", lambda *args: ("Test clip", True))
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe",
                               save=lambda: saved.append(settings.tts_custom_voice))
    dialog = voices_dialog.VoicesDialog(None, settings)
    dialog.show()

    def answer(allow):
        consent_dialog = qapp.activeModalWidget()
        try:
            box = consent_dialog.findChild(QDialogButtonBox)
            proceed = next(button for button in box.buttons() if box.buttonRole(button) == box.ButtonRole.AcceptRole)
            consent = consent_dialog.findChild(QCheckBox)
            consent_states.append((consent.isChecked(), proceed.isEnabled()))
            if allow:
                consent.setChecked(True)
                proceed.click()
        finally:
            if consent_dialog.isVisible():
                consent_dialog.reject()

    try:
        wait_until_enabled(dialog.add_button)
        assert dialog.add_button.isEnabled()
        QTimer.singleShot(0, lambda: answer(False))
        dialog.add_button.click()
        assert add_calls == []
        QTimer.singleShot(0, lambda: answer(True))
        dialog.add_button.click()
        assert not dialog.add_button.isEnabled()
        assert not dialog.close()
    finally:
        release.set()
        wait_until_enabled(dialog.add_button)

    assert consent_states == [(False, False), (False, False)]
    assert add_calls == [("Test clip", source, "vieneu", "Test clip", True)]
    custom = ("vieneu", "recording", "custom")
    assert set(dialog.cards) == {custom}
    assert visible_titles(dialog) == ["Test clip"]

    choose(dialog, custom)
    dialog.select_button.click()
    assert settings.tts_custom_voice == "custom"
    assert saved == ["custom"]

    confirmations = []
    monkeypatch.setattr(voices_dialog, "ask_confirmation",
                        lambda *args: confirmations.append(args) or True)
    card_button(card(dialog, custom), "Xóa").click()
    assert not removed
    assert confirmations == []
    assert "giọng mặc định" in dialog.status.text()

    settings.tts_custom_voice = ""
    monkeypatch.setattr(voices_dialog, "ask_confirmation", lambda *args: False)
    card_button(card(dialog, custom), "Xóa").click()
    assert not removed
    monkeypatch.setattr(voices_dialog, "ask_confirmation", lambda *args: True)
    card_button(card(dialog, custom), "Xóa").click()
    assert removed == ["custom"]

    assert dialog.cards == {}
    assert visible_titles(dialog) == []
    assert dialog.close()


def test_search_ignores_case_and_vietnamese_accents(qapp, tmp_path):
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe", save=lambda: None)
    cache = {voices_dialog.preset_key(settings): [
        {"id": "p1", "label": "Mai Phương", "gender": "Nữ", "region": "Bắc"},
        {"id": "p2", "label": "Đức Duy", "gender": "Nam"},
    ]}
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    assert visible_titles(dialog) == ["Mai Phương", "Đức Duy"]
    dialog.search.setText("mai phuong")
    assert visible_titles(dialog) == ["Mai Phương"]
    dialog.search.setText("ĐỨC DUY")
    assert visible_titles(dialog) == ["Đức Duy"]
    dialog.search.setText("nu")
    assert visible_titles(dialog) == ["Mai Phương"]
    dialog.search.setText("bac")
    assert visible_titles(dialog) == ["Mai Phương"]
    dialog.search.clear()
    assert visible_titles(dialog) == ["Mai Phương", "Đức Duy"]
    dialog.close()


def test_favorites_persist_and_do_not_change_pending_selection(qapp, tmp_path):
    saved = []
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe", extra={},
                               save=lambda: saved.append(dict(settings.extra)))
    cache = {voices_dialog.preset_key(settings): [
        {"id": "p1", "label": "Mai Phương"},
        {"id": "p2", "label": "Đức Duy"},
    ]}
    p1, p2 = ("vieneu", "preset", "p1"), ("vieneu", "preset", "p2")
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    card(dialog, p1).controls[2].click()
    assert settings.extra[voices_dialog.FAVORITES] == [["vieneu", "preset", "p1"]]
    assert dialog.favorites == {p1}

    choose(dialog, p2)
    assert dialog.select_button.isEnabled()
    card(dialog, p2).controls[2].click()
    assert dialog.pending == p2
    assert dialog.select_button.isEnabled()
    assert dialog.favorites == {p1, p2}
    assert len(saved) == 2
    assert settings.extra[voices_dialog.FAVORITES] == [["vieneu", "preset", "p1"],
                                                      ["vieneu", "preset", "p2"]]
    assert getattr(settings, "tts_voice", "") == ""
    assert getattr(settings, "tts_custom_voice", "") == ""

    dialog.tabs.setCurrentIndex(2)
    assert visible_titles(dialog) == ["Mai Phương", "Đức Duy"]
    dialog.close()

    reopened = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    assert card(reopened, p1).controls[2].isChecked()
    assert card(reopened, p2).controls[2].isChecked()
    reopened.tabs.setCurrentIndex(2)
    assert visible_titles(reopened) == ["Mai Phương", "Đức Duy"]
    assert reopened.pending is None
    assert not reopened.select_button.isEnabled()
    reopened.close()


def test_default_recording_deletion_is_guarded(qapp, tmp_path, monkeypatch):
    records = [{"name": "Default", "provider": "vieneu", "id": "default"},
               {"name": "Other", "provider": "vieneu", "id": "other"}]
    removed, confirmations, blocked = [], [], []
    reasons = {"other": "Đang được dùng bởi một video đang chờ."}

    def blocker(record):
        blocked.append(record["id"])
        return reasons.get(record["id"], "")

    registry = SimpleNamespace(list=lambda: records, remove=removed.append)
    monkeypatch.setattr(voices_dialog, "VoiceRegistry", lambda *args: registry)
    monkeypatch.setattr(voices_dialog, "ask_confirmation",
                        lambda *args: confirmations.append(args) or True)
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe",
                               tts_custom_voice="default", extra={}, save=lambda: None)
    dialog = voices_dialog.VoicesDialog(None, settings, deletion_blocker=blocker)
    dialog.tabs.setCurrentIndex(1)
    default, other = ("vieneu", "recording", "default"), ("vieneu", "recording", "other")

    card_button(card(dialog, default), "Xóa").click()
    assert blocked == []
    assert confirmations == []
    assert removed == []
    assert "giọng mặc định" in dialog.status.text()

    card_button(card(dialog, other), "Xóa").click()
    assert blocked == ["other"]
    assert confirmations == []
    assert removed == []
    assert dialog.status.text() == reasons["other"]

    reasons["other"] = ""
    card_button(card(dialog, other), "Xóa").click()
    assert len(confirmations) == 1
    assert removed == ["other"]
    assert set(dialog.cards) == {default}
    assert settings.tts_custom_voice == "default"
    dialog.close()


def test_stale_preview_completion_is_ignored(qapp, tmp_path, monkeypatch):
    entered = {voice: threading.Event() for voice in ("p1", "p2")}
    release = {voice: threading.Event() for voice in ("p1", "p2")}
    played = []

    def render(provider, voice, reference, python, options, media, *, recording=False, session=None, speed=1.0):
        entered[voice].set()
        assert release[voice].wait(5)
        return b"wav-" + voice.encode()

    monkeypatch.setattr(voices_dialog, "render_preview", render)
    monkeypatch.setattr(voices_dialog, "play_preview", played.append)
    monkeypatch.setattr(voices_dialog, "stop_preview", lambda: None)
    settings = SimpleNamespace(root=tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe", tts_speed=1.0, save=lambda: None)
    cache = {voices_dialog.preset_key(settings): [
        {"id": "p1", "label": "Giọng Một"},
        {"id": "p2", "label": "Giọng Hai"},
    ]}
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache=cache)
    p1, p2 = ("vieneu", "preset", "p1"), ("vieneu", "preset", "p2")

    card_button(card(dialog, p1), "Nghe thử").click()
    assert entered["p1"].wait(2)
    card_button(card(dialog, p2), "Nghe thử").click()
    assert entered["p2"].wait(2)

    release["p1"].set()
    QTest.qWait(100)
    assert played == []
    assert "Giọng Hai" in dialog.status.text()

    release["p2"].set()
    assert wait_until(lambda: played == [b"wav-p2"])
    assert "Giọng Hai" in dialog.status.text()
    assert not dialog.stop_button.isHidden()
    dialog.close()


def _warm_settings(tmp_path):
    return SimpleNamespace(root=tmp_path, ffmpeg='ffmpeg', ffprobe='ffprobe',
                           tts_provider='vieneu', tts_model='model', tts_speed=1.25, save=lambda: None)


def test_preview_reuses_catalog_session_and_closes_it_off_gui(tmp_path, qapp, monkeypatch):
    opened, closed, preview_calls, played = [], [], [], []
    gui_thread = threading.get_ident()

    class Session:
        def __init__(self, *args, **kwargs):
            self.process = object()
            opened.append(self)

        def voices(self):
            return [{'id': 'p1', 'label': 'Giọng Một'}]

        def synthesize(self, text, output, *, voice=None, ref_audio=None, ref_text=None):
            preview_calls.append((voice, text))
            assert threading.get_ident() != gui_thread
            output.write_bytes(b'raw')
            return output

        def close(self):
            closed.append((self, threading.get_ident()))

    def normalize_clip(raw, wav, *, speed):
        assert speed == 1.25
        wav.write_bytes(raw.read_bytes())

    media = SimpleNamespace(normalize_clip=normalize_clip)
    monkeypatch.setattr(voices_dialog, 'TtsSession', Session)
    monkeypatch.setattr(voices_dialog, 'play_preview', played.append)
    settings = _warm_settings(tmp_path)
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache={})
    dialog.media = media
    dialog.show()
    dialog.load_presets_button.click()
    assert wait_until(lambda: 'Giọng Một' in visible_titles(dialog))
    assert len(opened) == 1

    card_button(card(dialog, ('vieneu', 'preset', 'p1')), 'Nghe thử').click()
    assert wait_until(lambda: preview_calls)
    assert preview_calls[0] == ('p1', voice_preview.SAMPLE_TEXT)
    assert len(opened) == 1  # the catalog session was reused, not rebuilt
    assert wait_until(lambda: dialog.status.text().startswith('Đã phát mẫu'))
    assert played == [b'raw']
    assert dialog.close()
    assert wait_until(lambda: closed)
    assert len(closed) == 1 and closed[0][0] is opened[0] and closed[0][1] != gui_thread


def test_abandoned_catalog_session_closes_when_dialog_gone(tmp_path, qapp, monkeypatch):
    closed = []
    entered, release = threading.Event(), threading.Event()

    class Session:
        process = object()

        def voices(self):
            entered.set()
            assert release.wait(5)
            return [{'id': 'p1', 'label': 'Giọng Một'}]

        def close(self):
            closed.append(threading.get_ident())

    monkeypatch.setattr(voices_dialog, 'TtsSession', lambda *args, **kwargs: Session())
    settings = _warm_settings(tmp_path)
    dialog = voices_dialog.VoicesDialog(None, settings, preset_cache={})
    dialog.show()
    dialog.load_presets_button.click()
    try:
        assert entered.wait(2)
        assert dialog.close()
    finally:
        release.set()
    assert wait_until(lambda: closed)
    assert closed[0] != threading.get_ident()
