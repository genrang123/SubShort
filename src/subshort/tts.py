"""Persistent, cancellable offline TTS subprocess (one instance per batch).

TtsSession(python_executable, *, provider, model_path, hf_cache, device='cpu',
           license_accepted=False, onnx_dir=None, timeout=300.0)
loads once, supports voices() and synthesize(text, output_path, *, voice=None,
ref_audio=None, ref_text=None) -> Path, and must be closed or used with `with`.
The selected interpreter must have the provider SDK and soundfile installed.
"""
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from .execution import check_cancelled
from .paths import worker_root


class TtsSession:
    def __init__(self, python_executable, *, provider, model_path, hf_cache,
                 device="cpu", license_accepted=False, onnx_dir=None, timeout=300.0,
                 diagnostic=None):
        if provider not in ("vieneu", "omnivoice"):
            raise ValueError("Unknown TTS provider")
        if provider == "omnivoice" and license_accepted is not True:
            raise ValueError("OmniVoice model and tokenizer licenses must be accepted")
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        if not python_executable:
            raise ValueError("An explicit TTS Python interpreter is required")
        if timeout <= 0:
            raise ValueError("TTS response timeout must be positive")
        for name, value in (("model_path", model_path), ("hf_cache", hf_cache)):
            try:
                path = Path(value).expanduser()
            except TypeError:
                path = None
            if path is None or not path.is_dir():
                raise ValueError(f"{name} must be an existing local directory")
        if onnx_dir is not None and not Path(onnx_dir).expanduser().is_dir():
            raise ValueError("onnx_dir must be an existing local directory")
        self.timeout = timeout
        self.diagnostic = diagnostic if diagnostic is not None else lambda message: None
        self.process = None
        self._responses = queue.Queue()
        self._errors = deque(maxlen=30)
        self._threads = []
        check_cancelled()
        executable = str(python_executable)
        frozen_self = getattr(sys, "frozen", False) and Path(executable).resolve() == Path(sys.executable).resolve()
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_CACHE=str(Path(hf_cache).expanduser().resolve()),
                   PYTHONIOENCODING="utf-8")
        if frozen_self:
            command = [executable, "--tts-worker"]
            cwd = None
        else:
            root = worker_root()
            command = [executable, "-m", "subshort.tts_worker"]
            env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            cwd = str(root)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                        bufsize=1, cwd=cwd, env=env,
                                        start_new_session=os.name == "posix",
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def stdout_reader():
            try:
                for line in self.process.stdout:
                    self._responses.put(line)
            finally:
                self._responses.put(None)
        def stderr_reader():
            # Read chunks, not lines: SDK progress output may never emit a newline.
            while True:
                chunk = self.process.stderr.read(4096)
                if not chunk:
                    break
                self._errors.append(chunk)
        for target in (stdout_reader, stderr_reader):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        try:
            result = self._request("load", provider=provider, model_path=str(Path(model_path).resolve()),
                                   hf_cache=str(Path(hf_cache).resolve()), device=device,
                                   license_accepted=license_accepted,
                                   onnx_dir=str(Path(onnx_dir).resolve()) if onnx_dir else None)
            if isinstance(result, dict) and result.get("diagnostic"):
                self.diagnostic(result["diagnostic"])
        except BaseException:
            self.close(force=True)
            raise

    def _request(self, command, **args):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("TTS worker is not running")
        try:
            check_cancelled()
            self.process.stdin.write(json.dumps({"command": command, **args}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + self.timeout
            while True:
                check_cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("TTS worker response timed out")
                try:
                    line = self._responses.get(timeout=min(0.1, remaining))
                    break
                except queue.Empty:
                    continue
            if line is None:
                raise RuntimeError("TTS worker exited: " + "".join(self._errors)[-4000:])
            try:
                response = json.loads(line)
            except (TypeError, ValueError):
                raise RuntimeError("TTS worker returned invalid JSON") from None
            if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
                raise RuntimeError("TTS worker returned an invalid reply")
            if not response["ok"]:
                error = response.get("error")
                raise RuntimeError(error if isinstance(error, str) and error else "TTS worker request failed")
            return response.get("result")
        except BaseException:
            self.close(force=True)
            raise

    def voices(self):
        result = self._request("voices")
        if not isinstance(result, list):
            raise RuntimeError("TTS worker returned invalid voices")
        return result

    def synthesize(self, text, output_path, *, voice=None, ref_audio=None, ref_text=None):
        result = self._request("synthesize", text=text, output_path=str(Path(output_path).resolve()),
                               voice=voice, ref_audio=str(Path(ref_audio).resolve()) if ref_audio else None,
                               ref_text=ref_text)
        if not isinstance(result, dict) or not isinstance(result.get("output_path"), str):
            raise RuntimeError("TTS worker returned invalid output")
        return Path(result["output_path"])

    def close(self, *, force=False):
        process = self.process
        if process is None:
            return
        try:
            if not force and process.poll() is None:
                try:
                    process.stdin.write('{"command":"close"}\n')
                    process.stdin.flush()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    force = True
            if process.poll() is None:
                # Reader threads own the pipes; avoid communicate racing their reads.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    import signal
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=5)
        finally:
            for thread in self._threads:
                thread.join(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
            self.process = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
