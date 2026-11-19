"""Cooperative cancellation shared through the pipeline via a ContextVar."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import signal
import subprocess
import threading
import time

_current: ContextVar["Execution | None"] = ContextVar("subshort_execution", default=None)


class Cancelled(RuntimeError):
    """The active execution was cancelled."""


class Execution:
    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self):
        if self._cancelled.is_set():
            raise Cancelled("Đã hủy")

    @contextmanager
    def activate(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)


def check_cancelled():
    execution = _current.get()
    if execution is not None:
        execution.check()


def _kill_tree(process: subprocess.Popen):
    """Kill only the child's own process tree, drain its pipes and reap it."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            # start_new_session=True makes process.pid the process group id.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    for _ in range(2):
        try:
            process.communicate(timeout=5)
            return
        except subprocess.TimeoutExpired:
            process.kill()


def run_process(args, **kwargs):
    """subprocess.run for capture/text/check/input, polling cancellation every 100 ms."""
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    execution = _current.get()
    if execution is None:
        return subprocess.run(args, **kwargs)
    execution.check()
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    data = kwargs.pop("input", None)
    if kwargs.pop("capture_output", False):
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        kwargs["stdin"] = subprocess.PIPE
    if os.name == "posix":
        kwargs["start_new_session"] = True
    deadline = None if timeout is None else time.monotonic() + timeout
    process = subprocess.Popen(args, **kwargs)
    try:
        while True:
            try:
                stdout, stderr = process.communicate(input=data, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                data = None
                if deadline is not None and time.monotonic() > deadline:
                    raise
                execution.check()
    except BaseException:
        _kill_tree(process)
        raise
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, process.args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
