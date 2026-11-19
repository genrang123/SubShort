import threading
from types import SimpleNamespace
import pytest

from subshort.batch import Batch
from subshort.models import Job
from subshort.queue import JobQueue
from subshort.app import Pipeline
from subshort.providers import FatalProviderError


def test_batch_serial_claim_cancel_and_restart(tmp_path):
    queue = JobQueue(tmp_path / 'queue.json')
    for name in ('one', 'two'):
        queue.add(Job(name, name + '.mp4', 'vi'))
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Worker:
        def __init__(self, settings, queue, notify):
            self.cancelled = False
        def cancel(self):
            self.cancelled = True
            release.set()
        def run(self, job):
            calls.append(job.id)
            entered.set()
            assert release.wait(3)
            job.status = 'cancelled' if self.cancelled else 'done'
            queue.update(job)

    batch = Batch(SimpleNamespace(), queue, Worker, lambda *_: None)
    assert batch.start()
    assert entered.wait(3)
    assert not batch.start()
    assert calls == ['one']
    assert queue.snapshot()[0].status == 'running'
    batch.cancel()
    batch.join(3)
    assert not batch.running
    assert [job.status for job in queue.snapshot()] == ['cancelled', 'queued']
    assert batch.start()
    batch.join(3)
    assert calls == ['one', 'two']


def test_batch_continues_job_error_but_stops_fatal(tmp_path):
    queue = JobQueue(tmp_path / 'queue.json')
    for name in ('bad', 'fatal', 'untouched'):
        queue.add(Job(name, name, 'vi'))
    calls = []

    class Worker:
        def __init__(self, *_):
            pass
        def run(self, job):
            calls.append(job.id)
            job.status = 'failed'
            queue.update(job)
            if job.id == 'fatal':
                raise FatalProviderError('authentication rejected')

    batch = Batch(SimpleNamespace(), queue, Worker, lambda *_: None)
    batch.start()
    batch.join(3)
    assert calls == ['bad', 'fatal']
    assert queue.snapshot()[2].status == 'queued'
    assert batch.error == 'authentication rejected'


def test_pipeline_cancel_before_media_never_done(tmp_path):
    queue = JobQueue(tmp_path / 'queue.json')
    job = Job('one', 'one.mp4', 'vi')
    queue.add(job)

    class Settings:
        def for_job(self, job):
            return self
        def snapshot(self):
            return {}
        def validate(self, *args):
            pass

    pipeline = Pipeline(Settings(), queue, lambda *_: None)
    pipeline.cancel()
    pipeline.run(job)
    assert job.status == 'cancelled'
    assert queue.snapshot()[0].status == 'cancelled'
    assert not job.output


def test_claim_write_failure_stops_without_dispatch(tmp_path, monkeypatch):
    from subshort.queue import QueueError
    queue = JobQueue(tmp_path / 'queue.json')
    queue.add(Job('one', 'one.mp4', 'vi'))
    calls = []
    def claim(job):
        raise QueueError('disk full')
    monkeypatch.setattr(queue, 'claim', claim)
    batch = Batch(SimpleNamespace(), queue, lambda *args: calls.append(args), lambda *_: None)
    batch.start()
    batch.join(3)
    assert not calls
    assert batch.error == 'disk full'
    assert queue.snapshot()[0].status == 'queued'


def test_remove_selected_jobs_persists_and_refuses_active(tmp_path):
    from subshort.queue import QueueError
    queue = JobQueue(tmp_path / 'queue.json')
    for name in ('stale', 'keep', 'active'):
        queue.add(Job(name, name + '.mp4', 'vi'))
    queue.claim(queue.snapshot()[2])
    with pytest.raises(QueueError):
        queue.remove(['stale', 'active'])
    assert [job.id for job in queue.snapshot()] == ['stale', 'keep', 'active']
    queue.remove(['stale'])
    assert [job.id for job in JobQueue(tmp_path / 'queue.json').snapshot()] == ['keep', 'active']
