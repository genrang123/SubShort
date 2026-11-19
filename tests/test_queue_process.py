import json
import os
from pathlib import Path
import subprocess
import sys

from subshort.models import Job
from subshort.queue import JobQueue


def test_reopen_after_abrupt_worker_exit(tmp_path):
    path = tmp_path / 'queue.json'
    queue = JobQueue(path)
    queue.add(Job('one', 'one.mp4', 'vi'))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, '-c',
        'import os,sys; from pathlib import Path; from subshort.queue import JobQueue; '
        'q=JobQueue(Path(sys.argv[1])); assert q.claim(q.pending()[0]); os._exit(17)',
        str(path)], env=env, capture_output=True, timeout=10)
    assert result.returncode == 17, result.stderr
    reopened = JobQueue(path)
    assert not reopened.load_error
    job = reopened.snapshot()[0]
    assert job.status == 'failed'
    assert job.stage == 'interrupted'
    assert json.loads(path.read_text(encoding='utf-8'))[0]['status'] == 'failed'
