"""Run Qwen and FireRed in a configured Python environment."""
import contextlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from .asr import ASRSession
from .execution import run_process
from .paths import resource_root
from .providers import FatalProviderError, Segment, validate_segments


class ExternalASRSession:
    def __init__(self, python, engine, model_path, device, *, forced_aligner_path,
                 vad_model_path, firered_type, diagnostic):
        self.python = python
        self.engine = engine
        self.options = dict(engine=engine, model_path=model_path, device=device,
                            forced_aligner_path=forced_aligner_path, vad_model_path=vad_model_path,
                            firered_type=firered_type)
        self.diagnostic = diagnostic

    def transcribe(self, audio, language=None):
        root = resource_root()
        if getattr(sys, "frozen", False):
            root /= "asr_runtime"
        # Use this build's worker code, not a stale SubShort installed in the venv.
        command = [str(self.python), "-I", "-X", "utf8", "-c",
                   "import sys; sys.path.insert(0, sys.argv[1]); from subshort.asr_worker import main; main()",
                   str(root)]
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        request = dict(options=self.options, audio=str(Path(audio).resolve()), language=language)
        try:
            result = run_process(command, input=json.dumps(request), encoding="utf-8",
                                 errors="replace", capture_output=True, env=env, cwd=root)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FatalProviderError(f"Không chạy được Python ASR {self.engine}: {exc}") from None
        if result.returncode:
            detail = result.stderr.strip().splitlines()
            reason = detail[-1][:500] if detail else f"mã thoát {result.returncode}"
            raise FatalProviderError(f"ASR {self.engine}: {reason}")
        try:
            response = json.loads(result.stdout)
            if not isinstance(response["segments"], list) or not isinstance(response["diagnostics"], list):
                raise ValueError
            segments = validate_segments([Segment(**row) for row in response["segments"]])
            if not all(isinstance(message, str) for message in response["diagnostics"]):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise FatalProviderError(f"Worker ASR {self.engine} trả dữ liệu không hợp lệ") from None
        if self.diagnostic is not None:
            for message in response["diagnostics"]:
                self.diagnostic(message)
        return segments


def main():
    request = json.load(sys.stdin)
    diagnostics = []
    # Third-party libraries print progress and model details to stdout.
    with contextlib.redirect_stdout(sys.stderr):
        with ASRSession(**request["options"], diagnostic=diagnostics.append) as session:
            segments = session.transcribe(Path(request["audio"]), request["language"])
    json.dump({"segments": [asdict(row) for row in segments], "diagnostics": diagnostics}, sys.stdout)
