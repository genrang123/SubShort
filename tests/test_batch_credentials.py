import threading

from subshort.batch import Batch
from subshort.models import Job, Settings
from subshort.pipeline import Pipeline
from subshort.queue import JobQueue


def test_key_change_applies_next_batch_not_active_batch(tmp_path):
    queue = JobQueue(tmp_path / 'queue.json')
    for name in ('one', 'two'):
        queue.add(Job(name, name + '.mp4', 'vi'))
    settings = Settings(root=tmp_path, openai_api_key='old-key')
    entered, release = threading.Event(), threading.Event()
    keys = []
    class Worker(Pipeline):
        def run(self, job, *, keep_open=False):
            if job.id == 'one' and not keys:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test release timed out')
            keys.append(self.settings.for_job(job).openai_api_key)
            job.status = 'done'
            queue.update(job)
    batch = Batch(settings, queue, Worker, lambda *_: None)
    try:
        assert batch.start()
        assert entered.wait(3)
        settings.openai_api_key = 'new-key'
    finally:
        release.set()
        batch.join(3)
    assert not batch.running and not batch.error
    assert keys == ['old-key', 'old-key']
    queue.add(Job('three', 'three.mp4', 'vi'))
    assert batch.start()
    batch.join(3)
    assert not batch.running and not batch.error
    assert keys == ['old-key', 'old-key', 'new-key']
    assert 'old-key' not in queue.path.read_text(encoding='utf-8')
    assert 'new-key' not in queue.path.read_text(encoding='utf-8')
