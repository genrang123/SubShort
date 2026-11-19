import json

import pytest

from subshort.models import Job
from subshort.queue import JobQueue


def test_update_upload_merges_atomically_and_returns_copy(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    queue.add(Job("job", "source", "vi", status="done", output=str(output),
                  metadata={"render": {"stage": "done"}}))

    updated = queue.update_upload("job", {"status": "uploading", "progress": .4})
    assert updated.metadata == {"render": {"stage": "done"},
                                "upload": {"status": "uploading", "progress": .4}}
    updated.metadata["upload"]["progress"] = 1
    assert queue.snapshot()[0].metadata["upload"]["progress"] == .4
    queue.update_upload("job", {"progress": 1, "webViewLink": "https://drive"})
    assert queue.snapshot()[0].metadata["upload"]["status"] == "uploading"


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_update_upload_rejects_non_done(tmp_path, status):
    queue = JobQueue(tmp_path / "queue.json")
    queue.add(Job("job", "source", "vi", status=status))
    with pytest.raises(ValueError):
        queue.update_upload("job", {"status": "uploading"})


def test_retry_does_not_race_active_upload(tmp_path):
    queue = JobQueue(tmp_path / "queue.json")
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    queue.add(Job("job", "source", "vi", status="done", output=str(output)))
    queue.update_upload("job", {"status": "uploading", "progress": .2})
    job = queue.snapshot()[0]
    job.status = "failed"
    # The persisted render status remains done while upload metadata is active.
    assert not queue.retry("job")
    assert queue.snapshot()[0].metadata["upload"]["status"] == "uploading"
    assert json.loads((tmp_path / "queue.json").read_text())[0]["status"] == "done"
