import copy
import threading
from types import SimpleNamespace

import pytest

from subshort import upload
from subshort.execution import check_cancelled


class Queue:
    def __init__(self, job):
        self.job = job
        self.updates = []
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return [copy.deepcopy(self.job)]

    def update_upload(self, job_id, data):
        with self.lock:
            assert self.job.id == job_id and self.job.status == "done"
            self.job.metadata.setdefault("upload", {}).update(data)
            self.updates.append(data)
            return copy.deepcopy(self.job)


@pytest.fixture
def setup(tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    job = SimpleNamespace(id="job-id", status="done", output=str(output), metadata={"render": "unchanged"})
    settings = SimpleNamespace(root=tmp_path, output_dir="output")
    queue = Queue(job)
    notifications = []
    return upload.UploadManager(settings, queue, notifications.append), queue, notifications, output


def test_success_is_independent_of_render(setup, monkeypatch):
    manager, queue, notifications, output = setup
    calls = []

    class Uploader:
        def __init__(self, root):
            self.root = root

        def upload(self, file, folder, state, progress):
            calls.append((file, folder, state))
            progress(2, 5)
            return {"id": "remote", "webViewLink": "https://drive.google.com/file/d/remote/view", "size": 5}

    monkeypatch.setattr(upload, "DriveUploader", Uploader)
    assert manager.start("job-id", "folder")
    manager.join(2)
    assert not manager.running
    assert queue.job.status == "done" and queue.job.metadata["render"] == "unchanged"
    assert queue.job.metadata["upload"]["status"] == "done"
    assert queue.job.metadata["upload"]["progress"] == 1.0
    assert calls[0][2] == manager.root / "output_artifacts" / "job-id" / "upload.dpapi"
    assert output.exists() and notifications[-1] is None


def test_failure_is_sanitized_and_keeps_video(setup, monkeypatch):
    manager, queue, _, output = setup

    class Uploader:
        def __init__(self, root):
            pass

        def upload(self, *args):
            raise RuntimeError("credential-secret")

    monkeypatch.setattr(upload, "DriveUploader", Uploader)
    manager.start("job-id", "folder")
    manager.join(2)
    assert queue.job.status == "done"
    assert queue.job.metadata["upload"]["status"] == "failed"
    assert "credential-secret" not in str(queue.job.metadata)
    assert output.exists()


def test_one_worker_and_cancel(setup, monkeypatch):
    manager, queue, _, output = setup
    entered, release = threading.Event(), threading.Event()

    class Uploader:
        def __init__(self, root):
            pass

        def upload(self, *args):
            entered.set()
            release.wait(2)
            check_cancelled()

    monkeypatch.setattr(upload, "DriveUploader", Uploader)
    manager.start("job-id", "folder")
    assert entered.wait(2)
    assert manager.start("job-id", "folder") is False
    manager.cancel()
    release.set()
    manager.join(2)
    assert queue.job.metadata["upload"]["status"] == "cancelled"
    assert queue.job.status == "done" and output.exists()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_only_existing_done_output_allowed(setup, status):
    manager, queue, _, _ = setup
    queue.job.status = status
    with pytest.raises(ValueError):
        manager.start("job-id", "folder")
    assert queue.updates == []


def test_missing_output_and_bad_folder_rejected(setup):
    manager, queue, _, output = setup
    with pytest.raises(ValueError):
        manager.start("job-id", "https://drive.google.com/folder")
    output.unlink()
    with pytest.raises(ValueError):
        manager.start("job-id", "folder")
    assert queue.updates == []
