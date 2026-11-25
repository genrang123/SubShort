import threading
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from subshort.app import App
from subshort.models import Job, Settings


def wait_until(predicate):
    deadline = monotonic() + 5
    while not predicate():
        assert monotonic() < deadline, 'Timed out waiting for the UI'
        QApplication.processEvents()
        sleep(0.01)


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch):
    Settings(root=tmp_path).save()
    errors = []
    monkeypatch.setattr('subshort.app.show_message', lambda parent, title, text, **kwargs: errors.append((title, text)))
    app = None

    def create():
        nonlocal app
        app = App(tmp_path)
        app.show()
        return app

    yield create
    if app is not None and app.isVisible():
        app.close()
        wait_until(lambda: not app.isVisible())
    assert not errors


def test_ui_start_cancel_retry_close_on_main_thread(make_app, monkeypatch):
    entered, stopped = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    refresh_threads = []

    class Worker:
        def __init__(self, settings, queue, notify):
            self.queue, self.notify = queue, notify
        def cancel(self):
            stopped.set()
        def run(self, job):
            entered.set()
            assert stopped.wait(3)
            job.status = 'cancelled'
            self.queue.update(job)
            self.notify(job, 'cancelled')

    monkeypatch.setattr('subshort.app.Pipeline', Worker)
    app = make_app()
    assert not app.sources.isVisible() and not app.summary.isVisible()
    assert app.empty_add.isVisible() and app.empty_urls.isVisible()
    assert app.empty_add.property('variant') == 'primary'
    assert not app.batch_actions.isVisible() and app.queue_tabs.isVisible()
    app.queue.add(Job('one', 'one.mp4', 'vi'))
    app.queue.add(Job('two', 'two.mp4', 'vi'))
    app.refresh()
    assert app.sources.isVisible() and not app.empty_add.isVisible()
    assert app.start_button.isVisible() and app.start_button.isEnabled()
    assert app.start_button.property('variant') == 'primary' and app.add_button.property('variant') == ''
    original_refresh = app.refresh

    def refresh():
        refresh_threads.append(threading.get_ident())
        original_refresh()

    app.refresh = refresh
    app.start_button.click()

    wait_until(entered.is_set)
    wait_until(lambda: app.cancel_button.isVisible())
    assert app.cancel_button.text() == 'Dừng xử lý'
    app.cancel_button.click()
    refresh_count = len(refresh_threads)
    wait_until(lambda: not app.batch.running and len(refresh_threads) > refresh_count)
    assert app.queue.snapshot()[0].status == 'cancelled'
    assert app.queue.snapshot()[1].status == 'queued'
    assert app.jobs.item(0, 0).data(Qt.ItemDataRole.UserRole) == 'one'
    app.jobs.selectRow(0)
    app.retry_button.click()
    assert app.queue.snapshot()[0].status == 'queued'
    app.close()
    wait_until(lambda: not app.isVisible())
    assert not app.batch.running
    assert set(refresh_threads) == {main_thread}


def test_malformed_history_opens_readonly_without_overwrite(tmp_path, make_app):
    history = tmp_path / 'queue_history.json'
    history.write_bytes(b'{broken history')
    app = make_app()
    assert app.queue.load_error
    assert not app.start_button.isEnabled()
    assert not app.add_button.isEnabled()
    assert not app.empty_add.isVisible() and not app.empty_urls.isVisible()
    assert app.empty_hint.text() == app.queue.load_error
    assert history.read_bytes() == b'{broken history'


def test_remove_selected_job_from_ui(make_app, monkeypatch):
    app = make_app()
    app.queue.add(Job('old', 'old.mp4', 'vi'))
    app.queue.add(Job('new', 'new.mp4', 'vi'))
    app.refresh()
    monkeypatch.setattr('subshort.app.ask_confirmation', lambda *args, **kwargs: True)
    assert not app.remove_button.isEnabled()
    assert app.jobs.item(0, 0).data(Qt.ItemDataRole.UserRole) == 'old'
    app.jobs.selectRow(0)
    assert app.remove_button.isEnabled()
    app.remove_button.click()
    assert [job.id for job in app.queue.snapshot()] == ['new']
    app.jobs.selectRow(0)
    app.remove_button.click()
    assert app.empty_queue.isVisible()
    assert not app.start_button.isEnabled()


def test_processing_log_toggle_and_stage_lines(make_app):
    app = make_app()
    app.notify(SimpleNamespace(id='abcdef123456', stage='asr', status='running'))

    assert not app.log_frame.isVisible()
    app.log_toggle.click()
    assert app.log_frame.isVisible()
    wait_until(lambda: bool(app.log_text.toPlainText()))
    text = app.log_text.toPlainText()
    assert 'abcdef' in text and 'asr' in text
    app.log_toggle.click()
    assert not app.log_frame.isVisible()


def test_details_follow_current_job_without_losing_multiselection(make_app):
    from PySide6.QtCore import QItemSelectionModel

    app = make_app()
    assert app.empty_queue.isVisible()
    first = Job('shared-a', r'C:\video\first.mp4', 'vi', status='failed', stage='asr')
    second = Job('shared-b', 'second.mp4', 'vi', status='failed',
                 config={'tts_provider': 'edge', 'tts_voice': 'vi-VN-NamMinhNeural'})
    for job in (first, second):
        app.queue.add(job)
    app.refresh()
    assert not app.empty_queue.isVisible()
    assert app.jobs.item(0, 0).text() == 'first.mp4'
    assert 'HoaiMyNeural' in app.default_voice.text()
    flags = QItemSelectionModel.SelectionFlag
    app.jobs.selectRow(0)
    app.jobs.setCurrentCell(1, 0, flags.Select | flags.Rows)
    assert not app.details.isVisible()
    app.details_button.click()
    assert app.details.heading.text() == 'second.mp4'
    assert 'NamMinhNeural' in app.details.views[0].toPlainText()
    app.notify(first, 'first only')
    app.notify(second, 'second only')
    wait_until(lambda: 'second only' in app.details.views[2].toPlainText())
    assert 'first only' not in app.details.views[2].toPlainText()
    assert app.selected_jobs() == ['shared-a', 'shared-b']
    assert app.jobs.currentRow() == 1


def test_failure_recovery_uses_saved_error_without_session_events(make_app, monkeypatch):
    opened = []
    monkeypatch.setattr(App, 'api', lambda self: opened.append(self.current_job().id))
    app = make_app()
    for job in (
        Job('translate', 'translate.mp4', 'vi', status='failed', stage='translate',
            error='OpenAI từ chối xác thực hoặc hạn mức (HTTP 401)'),
        Job('asr', 'asr.mp4', 'vi', status='failed', stage='asr', error='HTTP 401 Unauthorized'),
        Job('queued', 'queued.mp4', 'vi'),
        Job('key', 'key.mp4', 'vi', status='failed', stage='translate', error='Thiếu GEMINI_API_KEY'),
        Job('blank', 'blank.mp4', 'vi', status='failed', error='  \n '),
    ):
        app.queue.add(job)
    app.refresh()
    app.jobs.selectRow(0)
    app.details_button.click()
    details = app.details
    assert details.failure.isVisible()
    assert 'Dịch vụ dịch từ chối xác thực hoặc đã chạm hạn mức.' in details.failure_text.accessibleName()
    assert details.failure_action.text() == 'Mở cài đặt API'
    assert not details.review.isVisible()
    details.failure_action.click()
    assert opened == ['translate']
    assert app.queue.snapshot()[0].status == 'failed'
    app.jobs.selectRow(1)
    assert details.tabs.currentIndex() == 0
    assert details.failure_action.text() == 'Xem log video'
    details.failure_action.click()
    assert details.tabs.currentIndex() == 2
    assert details.views[2].toPlainText() == 'Lỗi: HTTP 401 Unauthorized\n\n'
    app.jobs.selectRow(2)
    assert details.failure.isHidden()
    app.jobs.selectRow(3)
    assert 'Thiếu API key cho dịch vụ dịch văn bản.' in details.failure_text.accessibleName()
    app.jobs.selectRow(4)
    assert details.failure_text.accessibleName() == 'Lỗi · Chưa có thông báo lỗi.'
    assert details.failure_action.text() == 'Xem log video'


def test_resize_keeps_queue_context_and_restores_checked_log(make_app):
    app = make_app()
    app.queue.add(Job('tts', 'Studio tour.mp4', 'vi', status='running', stage='tts', progress=0.72))
    app.queue.add(Job('done', 'Finished.mp4', 'vi', status='done',
                      metadata={'upload': {'status': 'failed'}}))
    app.refresh()
    app.resize(1240, 680)
    app.jobs.selectRow(0)
    assert not app.jobs.isColumnHidden(1) and not app.jobs.isColumnHidden(4)
    assert app.jobs.rowHeight(0) == 40
    app.resize(850, 470)
    wait_until(lambda: app.jobs.isColumnHidden(1) and app.jobs.isColumnHidden(4))
    assert [i for i in range(5) if not app.jobs.isColumnHidden(i)] == [0, 2, 3]
    assert app.jobs.rowHeight(0) >= 48
    role = Qt.ItemDataRole.AccessibleTextRole
    assert app.jobs.item(0, 0).data(role) == 'Studio tour.mp4\nTạo giọng đọc'
    assert app.selected_jobs() == ['tts'] and app.jobs.currentRow() == 0
    app.queue_tabs.setCurrentIndex(1)
    assert app.jobs.item(1, 0).data(role) == 'Finished.mp4\nDrive: Tải lên lỗi'
    app.jobs.selectRow(1)
    app.log_toggle.setChecked(True)
    app.details_button.click()
    app.details.tabs.setCurrentIndex(2)
    app.resize(550, 300)
    wait_until(lambda: app.details.views[2].isVisible() and not app.queue_stack.isVisible())
    assert not app.log_frame.isVisible()
    assert app.details_button.text() == 'Ẩn chi tiết'
    app.details_button.click()
    assert app.jobs.isVisible() and app.log_frame.isVisible() and app.log_toggle.isChecked()
    assert app.selected_jobs() == ['done'] and app.jobs.currentRow() == 1
    app.details_button.click()
    app.resize(850, 470)
    wait_until(lambda: app.jobs.isVisible() and app.log_frame.isVisible())
    assert app.details.views[2].isVisible()
    assert app.selected_jobs() == ['done'] and app.jobs.currentRow() == 1


def test_completed_video_moves_tabs_and_reveals_its_output(make_app, tmp_path, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel

    from subshort import app as app_module

    app = make_app()
    output = tmp_path / 'Kết quả, video 2.mp4'
    output.touch()
    job = Job('finishing', 'video.mp4', 'vi')
    app.queue.add(job)
    app.refresh()
    app.jobs.selectRow(0)
    assert app.selected_jobs() == [job.id]
    job.status, job.output = 'done', str(output)
    job.metadata['upload'] = {'status': 'failed', 'error': 'Drive offline'}
    app.queue.update(job)
    app.refresh()
    assert app.empty_queue.isVisible()
    assert not app.selected_jobs()
    assert not app.remove_button.isEnabled()
    app.queue_tabs.setCurrentIndex(1)
    assert app.jobs.isVisible()
    assert app.jobs.item(0, 4).text() == 'Tải lên lỗi'
    app.jobs.selectRow(0)
    assert not app.start_button.isVisible() and not app.start_button.isEnabled()
    assert app.add_button.property('variant') == ''
    files, explorer = [], []
    monkeypatch.setattr(app_module.os, 'startfile', files.append)
    monkeypatch.setattr(app_module.subprocess, 'Popen', explorer.append)
    assert app.open_video_button.isVisible() and app.open_video_button.property('variant') == 'primary'
    app.open_video_button.click()
    assert files == [output] and not explorer
    assert app.open_button.text() == 'Hiện trong Explorer'
    app.open_button.click()
    assert explorer == [['explorer.exe', '/select,', str(output)]] and files == [output]

    another = tmp_path / 'another.mp4'
    another.touch()
    app.queue.add(Job('other', 'other.mp4', 'vi', status='done', output=str(another)))
    app.refresh()
    flags = QItemSelectionModel.SelectionFlag
    app.jobs.setCurrentCell(1, 0, flags.Select | flags.Rows)
    assert app.selected_jobs() == ['finishing', 'other']
    assert not app.open_video_button.isVisible()
    app.jobs.clearSelection()
    assert not app.open_video_button.isVisible()
    app.jobs.selectRow(0)
    output.unlink()
    dialogs = []
    monkeypatch.setattr(app_module, 'show_message', lambda parent, title, text, *args: dialogs.append(title))
    app.open_video_button.click()
    assert dialogs == ['Chưa có kết quả'] and files == [output]
    app.queue_tabs.setCurrentIndex(0)
    assert not app.selected_jobs()
    assert not app.open_button.isEnabled()
    assert not app.open_video_button.isVisible()


@pytest.mark.parametrize(('source', 'name'), [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'YouTube · dQw4w9WgXcQ'),
    ('https://www.youtube.com/watch?v=jNQXAC9IVRw', 'YouTube · jNQXAC9IVRw'),
    ('yt:jNQXAC9IVRw', 'YouTube · jNQXAC9IVRw'),
    ('yt:https://youtu.be/jNQXAC9IVRw?t=10', 'YouTube · jNQXAC9IVRw'),
    ('https://www.youtube.com/shorts/jNQXAC9IVRw/', 'YouTube · jNQXAC9IVRw'),
    ('https://m.youtube.com/live/%6ANQXAC9IVRw', 'YouTube · jNQXAC9IVRw'),
    ('https://youtube.com/embed/jNQXAC9IVRw/', 'YouTube · jNQXAC9IVRw'),
    ('https://www.youtube.com/watch?v=bad', 'www.youtube.com'),
    ('https://www.youtube.com/watch?v=jNQXAC9IVRw&v=', 'www.youtube.com'),
    ('https://www.youtube.com/watch?v=jNQXAC9IVRw&v=jNQXAC9IVRw', 'www.youtube.com'),
    ('https://youtu.be/extra/jNQXAC9IVRw', 'youtu.be · jNQXAC9IVRw'),
    ('https://www.douyin.com/video/123', 'www.douyin.com · 123'),
    (r'C:\video\demo.mp4', 'demo.mp4'),
    ('jNQXAC9IVRw', 'jNQXAC9IVRw'),
])
def test_video_name(source, name):
    from subshort.queue_view import video_name

    assert video_name(source) == name


def test_table_and_details_share_youtube_label_and_preserve_source(make_app):
    from html import escape

    app = make_app()
    source = 'https://www.youtube.com/watch?v=jNQXAC9IVRw&t=10'
    app.queue.add(Job('youtube', source, 'vi'))
    app.refresh()
    app.jobs.selectRow(0)
    app.details_button.click()
    item = app.jobs.item(0, 0)
    assert item.text() == app.details.heading.text() == 'YouTube · jNQXAC9IVRw'
    assert escape(source) in item.toolTip()
    assert f'Nguồn: {source}' in app.details.views[0].toPlainText()


def test_failure_opens_api_settings_for_jobs_saved_provider(make_app, monkeypatch):
    from subshort.settings_dialog import SettingsDialog

    app = make_app()
    job = Job('failed', 'source.mp4', 'vi', status='failed', stage='translate',
              error='Thiếu GEMINI_API_KEY', config={'translation_provider': 'gemini'})
    app.queue.add(job)
    app.refresh()
    app.jobs.selectRow(0)
    app.details_button.click()
    visited = []

    def inspect(dialog):
        assert isinstance(dialog, SettingsDialog)
        dialog.show()
        entry = dialog.key_fields['gemini_api_key'][0]
        wait_until(entry.hasFocus)
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()).replace('&&', '&') == 'API & dịch vụ'
        assert dialog.editors['translation_provider'].currentData() == 'openai'
        entry.setText('replacement-for-job')
        dialog.accept()
        visited.append(dialog.result())

    monkeypatch.setattr('subshort.app.run_dialog', inspect)
    app.details.failure_action.click()
    assert visited == [SettingsDialog.DialogCode.Accepted]
    assert app.settings.gemini_api_key == 'replacement-for-job'
    assert app.settings.translation_provider == 'openai'
    assert app.current_job().status == 'failed'
