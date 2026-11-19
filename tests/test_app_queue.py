import threading

from subshort.app import App
from subshort.models import Job, Settings


def test_ui_start_cancel_retry_close_on_main_thread(tmp_path, monkeypatch):
    Settings(root=tmp_path).save()
    entered, stopped = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    errors = []

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
    app = App(tmp_path)
    app.queue.add(Job('one', 'one.mp4', 'vi'))
    app.queue.add(Job('two', 'two.mp4', 'vi'))
    original_refresh = app.refresh

    def refresh():
        assert threading.get_ident() == main_thread
        original_refresh()

    app.refresh = refresh
    app.report_callback_exception = lambda *args: errors.append(args)
    app.run()
    app.run()

    def exercise():
        if not entered.is_set():
            app.after(10, exercise)
            return
        app.cancel_button.invoke()
        app.after(100, finish)

    def finish():
        if app.batch.running:
            app.after(10, finish)
            return
        app.refresh()
        assert app.queue.snapshot()[0].status == 'cancelled'
        assert app.queue.snapshot()[1].status == 'queued'
        app.jobs.selection_set('one')
        app.retry_button.invoke()
        assert app.queue.snapshot()[0].status == 'queued'
        app.close()

    app.after(10, exercise)
    app.after(5000, lambda: (errors.append('UI timed out'), app.close()))
    app.mainloop()
    assert not errors
    assert not app.batch.running


def test_malformed_history_opens_readonly_without_overwrite(tmp_path):
    Settings(root=tmp_path).save()
    history = tmp_path / 'queue_history.json'
    history.write_bytes(b'{broken history')
    app = App(tmp_path)
    errors = []
    def inspect():
        try:
            assert app.queue.load_error
            assert str(app.start_button['state']) == 'disabled'
            assert str(app.add_button['state']) == 'disabled'
            assert history.read_bytes() == b'{broken history'
        except Exception as exc:
            errors.append(exc)
        finally:
            app.close()
    app.after(10, inspect)
    app.mainloop()
    assert not errors


def test_remove_selected_job_from_ui(tmp_path, monkeypatch):
    Settings(root=tmp_path).save()
    app = App(tmp_path)
    app.queue.add(Job('old', 'old.mp4', 'vi'))
    app.queue.add(Job('new', 'new.mp4', 'vi'))
    app.refresh()
    errors = []
    monkeypatch.setattr('subshort.app.messagebox.askyesno', lambda *args, **kwargs: True)

    def inspect():
        try:
            assert str(app.remove_button['state']) == 'normal'
            app.jobs.selection_set('old')
            app.remove_button.invoke()
            assert [job.id for job in app.queue.snapshot()] == ['new']
        except Exception as exc:
            errors.append(exc)
        finally:
            app.close()
    app.after(10, inspect)
    app.mainloop()
    assert not errors


def test_processing_log_toggle_and_stage_lines(tmp_path):
    from types import SimpleNamespace
    Settings(root=tmp_path).save()
    app = App(tmp_path)
    errors = []
    app.notify(SimpleNamespace(id='abcdef123456', stage='asr', status='running'))

    def inspect():
        try:
            assert not app.log_frame.winfo_manager()
            app.log_toggle.invoke()
            assert app.log_frame.winfo_manager() == 'pack'
        except Exception as exc:
            errors.append(exc)
            app.close()
            return
        app.after(300, verify)

    def verify():
        try:
            text = app.log_text.get('1.0', 'end')
            assert 'abcdef' in text and 'asr' in text
            app.log_toggle.invoke()
            assert not app.log_frame.winfo_manager()
        except Exception as exc:
            errors.append(exc)
        finally:
            app.close()
    app.after(10, inspect)
    app.mainloop()
    assert not errors

