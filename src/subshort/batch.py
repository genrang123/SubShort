import copy
import threading

from .execution import Cancelled


class Batch:
    def __init__(self, settings, queue, pipeline_factory, notify):
        self.settings, self.queue = settings, queue
        self.pipeline_factory, self.notify = pipeline_factory, notify
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._pipeline = None
        self.error = ""

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            jobs = self.queue.pending()
            if not jobs:
                return False
            self._stop.clear()
            self.error = ""
            settings = copy.deepcopy(self.settings)
            self._thread = threading.Thread(target=self._run, args=(jobs, settings), daemon=False)
            self._thread.start()
            return True

    def cancel(self):
        with self._lock:
            self._stop.set()
            if self._pipeline is not None:
                self._pipeline.cancel()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, jobs, settings):
        pipeline = None
        try:
            for job in jobs:
                with self._lock:
                    if self._stop.is_set():
                        break
                    if not self.queue.claim(job):
                        continue
                if pipeline is None:
                    try:
                        pipeline = self.pipeline_factory(settings, self.queue, self.notify)
                    except Exception as exc:
                        job.status = "cancelled" if isinstance(exc, Cancelled) else "failed"
                        job.error = str(exc)
                        self.queue.update(job)
                        self.notify(job)
                        raise
                    with self._lock:
                        self._pipeline = pipeline
                        if self._stop.is_set():
                            pipeline.cancel()
                pipeline.run(job, keep_open=True)
                if job.status == "cancelled":
                    break
        except Cancelled:
            pass
        except Exception as exc:
            self.error = str(exc)
        finally:
            try:
                if pipeline is not None:
                    pipeline.close()
            finally:
                with self._lock:
                    self._pipeline = None
                self.notify(None)
