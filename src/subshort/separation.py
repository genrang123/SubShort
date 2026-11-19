"""Offline HQ3 separation in a cancellable, CPU-only Python worker."""
from contextlib import redirect_stdout
from importlib import metadata
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .execution import run_process

MODEL_FILENAME = "UVR-MDX-NET-Inst_HQ_3.onnx"
CACHE_FILES = (MODEL_FILENAME, "download_checks.json", "vr_model_data.json", "mdx_model_data.json")


def _check_cache(model_dir: Path) -> str:
    missing = [name for name in CACHE_FILES if not (model_dir / name).is_file() or not (model_dir / name).stat().st_size]
    if missing:
        raise RuntimeError(f"HQ3 cache {model_dir}: missing/empty local assets: {', '.join(missing)}; downloads are disabled")
    for name in CACHE_FILES[1:]:
        try:
            with (model_dir / name).open(encoding="utf-8") as stream:
                if not isinstance(json.load(stream), dict):
                    raise ValueError("expected JSON object")
        except (ValueError, OSError) as exc:
            raise RuntimeError(f"HQ3 cache {model_dir}: invalid {name}: {exc}") from exc
    digest = hashlib.sha256()
    with (model_dir / MODEL_FILENAME).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _instrumental_result(files, output_dir: Path) -> Path:
    expected = output_dir.resolve() / "background.wav"
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], str):
        raise RuntimeError("HQ3 must return exactly one Instrumental output")
    result = Path(files[0])
    if not result.is_absolute():
        result = output_dir / result
    result = result.resolve()
    if result != expected or not result.is_file() or result.stat().st_size == 0:
        raise RuntimeError("HQ3 Instrumental output must be a nonempty background.wav inside the owned output directory")
    return result


def separate_background(source: Path, work: Path, model_dir: Path, python_executable=sys.executable) -> Path:
    """Return the raw instrumental WAV; the parent media stage normalizes stereo."""
    source, work, model_dir = Path(source).resolve(), Path(work).resolve(), Path(model_dir).resolve()
    if not source.is_file() or not source.stat().st_size:
        raise ValueError(f"Separation source is missing or empty: {source}")
    _check_cache(model_dir)
    work.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(prefix="hq3-", dir=work))
    env = os.environ.copy()
    env["AUDIO_SEPARATOR_MODEL_DIR"] = str(model_dir)
    env["CUDA_VISIBLE_DEVICES"] = "-1"
    # Make the application module discoverable in an independently provisioned Python.
    root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        frozen_self = (getattr(sys, "frozen", False)
                       and Path(python_executable).resolve() == Path(sys.executable).resolve())
        command = ([str(python_executable), "--separator-worker"] if frozen_self
                   else [str(python_executable), "-m", "subshort.separation", "--worker"])
        completed = run_process(
            command,
            input=json.dumps({"source": str(source), "output_dir": str(output_dir), "model_dir": str(model_dir)}),
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        if completed.returncode:
            raise RuntimeError(f"HQ3 separation failed: {completed.stderr.strip()}")
        try:
            response = json.loads(completed.stdout)
        except ValueError as exc:
            raise RuntimeError("HQ3 worker returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise RuntimeError("HQ3 worker returned invalid result")
        return _instrumental_result(response.get("files"), output_dir)
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def _worker(request: dict) -> dict:
    model_dir = Path(request["model_dir"]).resolve()
    output_dir = Path(request["output_dir"]).resolve()
    model_hash = _check_cache(model_dir)
    print(f"HQ3 cache={model_dir} model={MODEL_FILENAME} sha256={model_hash}", file=sys.stderr)
    os.environ["AUDIO_SEPARATOR_MODEL_DIR"] = str(model_dir)
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    # No library offline flag exists; contain its requests override in this worker.
    import requests

    def offline_request(*args, **kwargs):
        raise RuntimeError("HQ3 offline worker: network requests are disabled; provision the complete local cache")

    requests.sessions.Session.request = offline_request
    try:
        metadata.version("onnxruntime")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError("HQ3 requires audio-separator[cpu] with the onnxruntime CPU distribution") from exc
    with redirect_stdout(sys.stderr):
        from audio_separator.separator import Separator

        separator = Separator(
            model_file_dir=str(model_dir), output_dir=str(output_dir),
            output_format="WAV", output_single_stem="Instrumental", sample_rate=44100,
            mdx_params={"hop_length": 1024, "segment_size": 256, "overlap": 0.25, "batch_size": 1, "enable_denoise": False},
        )
        # Upstream has no CPU constructor flag: reject acceleration before loading.
        if separator.onnx_execution_provider != ["CPUExecutionProvider"] or str(separator.torch_device) != "cpu":
            raise RuntimeError("HQ3 requires CPU execution; use a CPU-only separator environment")
        separator.load_model(model_filename=MODEL_FILENAME)
        files = separator.separate(str(Path(request["source"]).resolve()), custom_output_names={"Instrumental": "background"})
    result = _instrumental_result(files, output_dir)
    return {"files": [str(result)], "model_sha256": model_hash}


def main() -> int:
    if sys.argv[1:] not in (["--worker"], ["--separator-worker"]):
        print("Use --worker with a JSON request on stdin", file=sys.stderr)
        return 2
    try:
        response = _worker(json.load(sys.stdin))
        print(json.dumps(response))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
