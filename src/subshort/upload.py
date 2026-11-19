"""Explicit, single-worker upload lifecycle, separate from render lifecycle."""
import re
import threading
from pathlib import Path

from .drive import DriveUploader
from .execution import Cancelled, Execution, check_cancelled
from .paths import artifact_directory


class UploadManager:
    def __init__(self, settings, queue, notify):
        self.settings, self.queue, self.notify = settings, queue, notify
        self.root = Path(settings.root)
        self._lock = threading.Lock()
        self._thread = None
        self._execution = None
        self.error = ""

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _job(self, job_id):
        return next((job for job in self.queue.snapshot() if job.id == job_id), None)

    def _update(self, job_id, data):
        job = self.queue.update_upload(job_id, data)
        self.notify(job)
        return job

    def start(self, job_id, folder_id):
        """Explicit caller action only; returns False when another upload is active."""
        if not isinstance(folder_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
            raise ValueError("Drive folder ID không hợp lệ")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            job = self._job(job_id)
            if job is None or job.status != "done" or not job.output or not Path(job.output).is_file():
                raise ValueError("Chỉ tải lên video đã render xong và còn trên máy")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", job.id):
                raise ValueError("Job ID không hợp lệ")
            self.error = ""
            self._execution = Execution()
            self._update(job_id, {"status": "uploading", "progress": 0.0, "folder_id": folder_id,
                                  "error": "", "webViewLink": ""})
            self._thread = threading.Thread(target=self._run, args=(job, folder_id, self._execution), daemon=False)
            self._thread.start()
            return True

    def cancel(self):
        with self._lock:
            if self._execution is not None:
                self._execution.cancel()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, job, folder_id, execution):
        def progress(sent, total):
            check_cancelled()
            current = self._job(job.id)
            if current is None or current.status != "done" or current.output != job.output:
                raise Cancelled("Đầu ra đã thay đổi")
            self._update(job.id, {"status": "uploading", "progress": sent / total if total else 0.0,
                                  "bytes": sent, "size": total})
        try:
            with execution.activate():
                check_cancelled()
                state = artifact_directory(self.root, self.settings.output_dir, job.id) / "upload.dpapi"
                result = DriveUploader(self.root).upload(Path(job.output), folder_id, state, progress)
                check_cancelled()
                self._update(job.id, {"status": "done", "progress": 1.0, "error": "", **result})
        except Cancelled:
            self._terminal(job.id, "cancelled", "Đã hủy tải lên; video trên máy được giữ nguyên")
        except Exception:
            self.error = "Không tải lên được Drive; video trên máy và phiên tiếp tục được giữ nguyên"
            self._terminal(job.id, "failed", self.error)
        finally:
            with self._lock:
                self._execution = None
            self.notify(None)

    def _terminal(self, job_id, status, error):
        try:
            self._update(job_id, {"status": status, "error": error})
        except Exception:
            self.error = "Không lưu được trạng thái tải lên"
