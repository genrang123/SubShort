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
    app.queue.add(Job('one', 'one.mp4', 'vi'))
    app.queue.add(Job('two', 'two.mp4', 'vi'))
    original_refresh = app.refresh

    def refresh():
        refresh_threads.append(threading.get_ident())
        original_refresh()

    app.refresh = refresh
    app.start_button.click()
    app.run()

    wait_until(entered.is_set)
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


def test_completed_video_moves_tabs_and_reveals_its_output(make_app, tmp_path, monkeypatch):
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
    calls = []
    monkeypatch.setattr(app_module.subprocess, 'Popen', calls.append)
    app.open_button.click()
    assert calls == [['explorer.exe', '/select,', str(output)]]
    app.queue_tabs.setCurrentIndex(0)
    assert not app.selected_jobs()
    assert not app.open_button.isEnabled()
