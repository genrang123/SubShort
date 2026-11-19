from pathlib import Path
from contextlib import suppress
import copy
import json
import os
import tempfile
import threading
from .models import Job

PENDING_STATUSES = {"queued", "failed"}
CLAIMABLE_STATUSES = {"queued", "failed", "cancelled"}
RETRYABLE_STATUSES = {"failed", "cancelled"}
REQUIRED_FIELDS = {"id", "source", "target_language"}
STRING_FIELDS = ("id", "source", "target_language", "mode", "status", "output", "error", "stage")


class QueueError(RuntimeError):
    pass


class JobQueue:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.jobs: list[Job] = []
        self.load_error = ""
        self.load()

    def load(self):
        with self._lock:
            self.jobs, self.load_error = [], ""
            try:
                text = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return
            except (OSError, UnicodeDecodeError) as exc:
                self.load_error = f"Không đọc được lịch sử hàng đợi: {exc}"
                return
            try:
                self.jobs = self._parse(text)
            except ValueError as exc:
                self.load_error = f"Lịch sử hàng đợi bị hỏng: {exc}"
                return
            if self._recover():
                try:
                    self._save_locked()
                except QueueError as exc:
                    self.load_error = str(exc)

    def _parse(self, text: str) -> list[Job]:
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("dữ liệu không phải danh sách")
        fields = set(Job.__dataclass_fields__)
        jobs: list[Job] = []
        seen: set[str] = set()
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"mục {index} không phải object")
            if unknown := set(item) - fields:
                raise ValueError(f"mục {index} có trường lạ: {', '.join(sorted(unknown))}")
            if missing := REQUIRED_FIELDS - set(item):
                raise ValueError(f"mục {index} thiếu trường: {', '.join(sorted(missing))}")
            try:
                job = Job(**item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"mục {index}: {exc}") from exc
            for name in STRING_FIELDS:
                value = getattr(job, name)
                if not isinstance(value, str) or (name in REQUIRED_FIELDS and not value.strip()):
                    raise ValueError(f"mục {index}: trường {name} phải là chuỗi không rỗng")
            if type(job.progress) not in (int, float):
                raise ValueError(f"mục {index}: progress phải là số")
            for name in ("metadata", "config"):
                if not isinstance(getattr(job, name), dict):
                    raise ValueError(f"mục {index}: {name} phải là object")
            if job.id in seen:
                raise ValueError(f"id trùng: {job.id}")
            seen.add(job.id)
            jobs.append(job)
        return jobs

    def _recover(self) -> bool:
        changed = False
        for job in self.jobs:
            if job.status == "running":
                job.status, job.stage = "failed", "interrupted"
                job.error = "Job bị gián đoạn khi ứng dụng đóng"
                changed = True
            elif job.status == "done" and not (job.output and Path(job.output).is_file()):
                job.status, job.output = "failed", ""
                job.error = "Kết quả không còn tồn tại"
                changed = True
            upload = job.metadata.get("upload")
            if job.status == "done" and isinstance(upload, dict) and upload.get("status") == "uploading":
                upload = copy.deepcopy(upload)
                upload["status"], upload["error"] = "failed", "Tải lên bị gián đoạn khi ứng dụng đóng"
                job.metadata["upload"] = upload
                changed = True
        return changed

    @staticmethod
    def _upload_active(job):
        upload = job.metadata.get("upload")
        return isinstance(upload, dict) and upload.get("status") == "uploading"

    def update_upload(self, job_id: str, data: dict) -> Job:
        """Merge upload state without changing the render job lifecycle."""
        if not isinstance(data, dict):
            raise TypeError("upload metadata must be an object")
        with self._lock:
            self._require_writable()
            index = self._find(job_id)
            if index is None:
                raise KeyError(job_id)
            job = self.jobs[index]
            if job.status != "done":
                raise ValueError("Chỉ tải lên job đã render xong")
            previous = copy.deepcopy(job)
            upload = job.metadata.setdefault("upload", {})
            if not isinstance(upload, dict):
                job.metadata = previous.metadata
                raise ValueError("upload metadata phải là object")
            upload.update(copy.deepcopy(data))
            try:
                self._save_locked()
            except BaseException:
                self.jobs[index] = previous
                raise
            return copy.deepcopy(job)

    def _require_writable(self):
        if self.load_error:
            raise QueueError(f"Hàng đợi đang ở chế độ chỉ đọc: {self.load_error}")

    def _save_locked(self):
        text = json.dumps([job.__dict__ for job in self.jobs], ensure_ascii=False, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        except OSError as exc:
            raise QueueError(f"Không tạo được tệp tạm cho {self.path.name}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            with suppress(OSError):
                os.unlink(temp)
            raise QueueError(f"Không ghi được lịch sử hàng đợi {self.path.name}: {exc}") from exc

    def save(self):
        with self._lock:
            self._require_writable()
            self._save_locked()

    def snapshot(self) -> list[Job]:
        with self._lock:
            return copy.deepcopy(self.jobs)

    def pending(self) -> list[Job]:
        with self._lock:
            return copy.deepcopy([job for job in self.jobs if job.status in PENDING_STATUSES])

    def _find(self, job_id: str):
        return next((index for index, job in enumerate(self.jobs) if job.id == job_id), None)

    def add(self, job: Job):
        with self._lock:
            self._require_writable()
            if self._find(job.id) is not None:
                raise ValueError(f"Job đã tồn tại: {job.id}")
            self.jobs.append(copy.deepcopy(job))
            try:
                self._save_locked()
            except BaseException:
                self.jobs.pop()
                raise

    def update(self, job: Job):
        with self._lock:
            self._require_writable()
            index = self._find(job.id)
            if index is None:
                raise KeyError(job.id)
            previous, self.jobs[index] = self.jobs[index], copy.deepcopy(job)
            try:
                self._save_locked()
            except BaseException:
                self.jobs[index] = previous
                raise

    def claim(self, job: Job) -> bool:
        with self._lock:
            self._require_writable()
            index = self._find(job.id)
            if index is None or self.jobs[index].status not in CLAIMABLE_STATUSES:
                return False
            if self._upload_active(self.jobs[index]):
                return False
            stored, previous = self.jobs[index], self.jobs[index].status
            stored.status = "running"
            try:
                self._save_locked()
            except BaseException:
                stored.status = previous
                raise
            return True

    def retry(self, job_id: str) -> bool:
        with self._lock:
            self._require_writable()
            index = self._find(job_id)
            if index is None or self.jobs[index].status not in RETRYABLE_STATUSES:
                return False
            if self._upload_active(self.jobs[index]):
                return False
            job = self.jobs[index]
            previous = copy.deepcopy(job)
            job.status, job.output, job.error = "queued", "", ""
            job.stage, job.progress, job.metadata = "", 0.0, {}
            try:
                self._save_locked()
            except BaseException:
                self.jobs[index] = previous
                raise
            return True

    def remove(self, job_ids):
        with self._lock:
            self._require_writable()
            selected = set(job_ids)
            if any(job.id in selected and (job.status == "running" or self._upload_active(job))
                   for job in self.jobs):
                raise QueueError("Không thể xóa job đang xử lý hoặc tải lên")
            previous = self.jobs
            self.jobs = [job for job in previous if job.id not in selected]
            try:
                self._save_locked()
            except BaseException:
                self.jobs = previous
                raise
