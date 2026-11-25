"""Lazy upstream ASR sessions; all model paths must already exist locally.

Native inference is in-process: cancellation is checked around, not inside, native
calls. close() drops session references; native allocators may retain memory.
FireRed timestamps are VAD speech intervals, not word alignments.
"""
from pathlib import Path
import argparse
import contextlib
import os
import site
import sys
import sysconfig
import tempfile
import wave
from .execution import check_cancelled
from .providers import FatalProviderError, ProviderError, Segment, validate_segments


QWEN_LANGUAGES = {
    "zh": "Chinese", "en": "English", "yue": "Cantonese", "ar": "Arabic", "de": "German",
    "fr": "French", "es": "Spanish", "pt": "Portuguese", "id": "Indonesian", "it": "Italian",
    "ko": "Korean", "ru": "Russian", "th": "Thai", "vi": "Vietnamese", "ja": "Japanese",
    "tr": "Turkish", "hi": "Hindi", "ms": "Malay", "nl": "Dutch", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "pl": "Polish", "cs": "Czech", "tl": "Filipino",
    "fa": "Persian", "el": "Greek", "ro": "Romanian", "hu": "Hungarian", "mk": "Macedonian",
}


def _read_wav_audio(path, sampling_rate):
    import torch
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if channels < 1 or width not in {1, 2, 3, 4} or rate < 1 or len(frames) % width:
        raise ProviderError("Unsupported WAV audio format")
    count = len(frames) // width
    if width == 1:
        values = torch.frombuffer(bytearray(frames), dtype=torch.uint8).to(torch.float32).sub_(128)
        scale = 128.0
    elif width == 2:
        values = torch.frombuffer(bytearray(frames), dtype=torch.int16).to(torch.float32)
        scale = 32768.0
    elif width == 3:
        values = []
        for offset in range(0, count * 3, 3):
            value = int.from_bytes(frames[offset:offset + 3], "little", signed=False)
            values.append(value - (1 << 24) if value & (1 << 23) else value)
        values = torch.tensor(values, dtype=torch.float32)
        scale = float(1 << 23)
    else:
        values = torch.frombuffer(bytearray(frames), dtype=torch.int32).to(torch.float32)
        scale = float(1 << 31)
    waveform = values.div_(scale).reshape(-1, channels).mean(dim=1)
    if rate != sampling_rate:
        from torchaudio.functional import resample
        waveform = resample(waveform, rate, sampling_rate)
    return waveform




def _save_wav_audio(path, waveform, sampling_rate):
    import torch
    values = waveform.detach().cpu().reshape(-1).clamp(-1, 1).mul(32767).round().to(torch.int16)
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sampling_rate)
        destination.writeframes(values.numpy().tobytes())


_CUDA_DLL_HANDLES = []
_CUDA_DLL_PATHS = set()

def _load_cuda_runtime_dlls():
    """Make pip-installed NVIDIA DLL directories visible to native CUDA consumers."""
    if sys.platform != "win32":
        return
    roots = []
    for package_root in (*site.getsitepackages(), site.getusersitepackages(), sysconfig.get_paths().get("purelib", "")):
        if package_root:
            roots.extend((Path(package_root) / "nvidia" / name / "bin" for name in ("cublas", "cudnn")))
    for directory in roots:
        if not directory.is_dir():
            continue
        directory_text = str(directory)
        if directory_text not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = directory_text + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory") and directory_text not in _CUDA_DLL_PATHS:
            _CUDA_DLL_HANDLES.append(os.add_dll_directory(directory_text))
            _CUDA_DLL_PATHS.add(directory_text)


def _validate_qwen_alignment(rows, duration):
    segments = []
    for index, row in enumerate(rows):
        try:
            start, end, text = row.start_time, row.end_time, row.text
        except AttributeError:
            raise ProviderError(f"Qwen transcript has invalid alignment at item {index}") from None
        segments.append(Segment(start, end, text))
    try:
        return validate_segments(segments, duration)
    except ProviderError as exc:
        for index, segment in enumerate(segments):
            if (isinstance(segment.start, (int, float)) and isinstance(segment.end, (int, float))
                    and 0 <= segment.start < segment.end <= duration):
                continue
            raise ProviderError(
                f"Qwen transcript has invalid alignment at item {index}: "
                f"start={segment.start!r}, end={segment.end!r}, duration={duration:.6f}"
            ) from None
        raise ProviderError(f"Qwen transcript has invalid alignment: {exc}") from None


class ASRSession:
    def __init__(self, engine: str, model_path, device: str = "cpu", *,
                 forced_aligner_path=None, vad_model_path=None, firered_type: str = "aed", diagnostic=None):
        if engine not in {"whisper", "qwen", "firered"}:
            raise ValueError("Unsupported ASR engine")
        if device not in {"cpu", "cuda"}:
            raise ValueError("ASR device must be cpu or cuda")
        if firered_type not in {"aed", "llm"}:
            raise ValueError("FireRed type must be aed or llm")
        self.engine, self.device = engine, device
        self.model_path = self._local(model_path, directory=True)
        self.aligner_path = self._local(forced_aligner_path, directory=True) if engine == "qwen" else None
        self.vad_path = self._local(vad_model_path, directory=False) if engine != "whisper" else None
        self.firered_type = firered_type
        self.diagnostic = diagnostic if diagnostic is not None else lambda message: None
        self._model = self._vad = None

    @staticmethod
    def _local(value, *, directory):
        if not value:
            raise FatalProviderError("ASR requires an explicit existing local model path")
        path = Path(value)
        if not (path.is_dir() if directory else path.is_file()):
            raise FatalProviderError(f"ASR local model path not found: {path}")
        return str(path.resolve())

    def _load_on(self, device):
        if self.engine == "whisper":
            if device == "cuda":
                _load_cuda_runtime_dlls()
            from faster_whisper import WhisperModel
            return WhisperModel(self.model_path, device=device,
                                compute_type="int8" if device == "cpu" else "float16", local_files_only=True)
        if self.engine == "qwen":
            import torch
            from qwen_asr import Qwen3ASRModel
            options = {"device_map": device, "dtype": torch.float32 if device == "cpu" else torch.float16,
                       "local_files_only": True}
            return Qwen3ASRModel.from_pretrained(self.model_path, forced_aligner=self.aligner_path,
                                                forced_aligner_kwargs=options, max_inference_batch_size=1, **options)
        import torch
        from fireredasr.models.fireredasr import FireRedAsr
        allowlist = torch.serialization.safe_globals([argparse.Namespace])
        with allowlist:
            model = FireRedAsr.from_pretrained(self.firered_type, self.model_path)
        model.model.cuda() if device == "cuda" else model.model.cpu()
        return model

    def _cuda_available(self):
        if self.engine == "whisper":
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        import torch
        return torch.cuda.is_available()

    def _load(self):
        if self._model is not None:
            return
        check_cancelled()
        try:
            if self.device == "cuda" and not self._cuda_available():
                self.diagnostic("CUDA unavailable; loading the same ASR model on CPU")
                self.device = "cpu"
            try:
                model = self._load_on(self.device)
            except RuntimeError as exc:
                # Do not hide corrupt models, missing libraries, or inference errors.
                if self.device != "cuda" or "cuda out of memory" not in str(exc).lower():
                    raise
                model = None
            if model is None:
                self.diagnostic("CUDA load out of memory; loading the same ASR model on CPU")
                self.device = "cpu"
                model = self._load_on("cpu")
            check_cancelled()
            self._model = model
            if self.engine != "whisper":
                from silero_vad.utils_vad import init_jit_model
                self._vad = init_jit_model(self.vad_path)
                check_cancelled()
        except ImportError as exc:
            self.close()
            raise FatalProviderError(f"Missing installed ASR dependency: {exc.name}") from None
        except BaseException:
            self.close()
            raise

    def transcribe(self, audio: Path, language: str | None = None) -> list[Segment]:
        check_cancelled()
        audio = Path(audio)
        if not audio.is_file():
            raise ProviderError(f"ASR audio not found: {audio}")
        self._load()
        if self.engine == "whisper":
            rows, _ = self._model.transcribe(str(audio), language=language, vad_filter=True, word_timestamps=False)
            segments = []
            for row in rows:
                check_cancelled()
                segments.append(Segment(row.start, row.end, row.text))
        else:
            segments = self._speech_transcribe(audio, language)
        check_cancelled()
        return validate_segments(segments)

    def transcribe_words(self, audio: Path, language: str | None = None) -> list[Segment]:
        """Return word timings from the configured Whisper session."""
        if self.engine != "whisper":
            raise ProviderError("ASR engine does not provide word timings")
        check_cancelled()
        audio = Path(audio)
        if not audio.is_file():
            raise ProviderError(f"ASR audio not found: {audio}")
        self._load()
        rows, _ = self._model.transcribe(
            str(audio), language=language, vad_filter=True, word_timestamps=True
        )
        result = []
        for row in rows:
            check_cancelled()
            for word in row.words or ():
                check_cancelled()
                result.append(Segment(word.start, word.end, word.word))
        check_cancelled()
        return validate_segments(result)



    def _speech_transcribe(self, audio, language):
        from silero_vad import get_speech_timestamps, read_audio
        if self.engine == "qwen" and language:
            # Settings use ISO codes; Qwen requires canonical language names.
            language = QWEN_LANGUAGES.get(language.strip().lower(), language)
        try:
            waveform = read_audio(str(audio), sampling_rate=16000)
        except RuntimeError as exc:
            if "torchcodec" not in str(exc).lower():
                raise
            waveform = _read_wav_audio(audio, 16000)
        check_cancelled()
        intervals = get_speech_timestamps(waveform, self._vad, sampling_rate=16000)
        segments = []
        previous = 0
        for interval in intervals:
            check_cancelled()
            if not isinstance(interval, dict) or "start" not in interval or "end" not in interval:
                raise ProviderError("Invalid VAD speech interval")
            start, end = interval["start"], interval["end"]
            if type(start) is not int or type(end) is not int or not previous <= start < end <= len(waveform):
                raise ProviderError("Invalid VAD speech interval")
            previous = end
            clip = waveform[start:end]
            if self.engine == "qwen":
                results = self._model.transcribe(audio=(clip.numpy(), 16000), language=language, return_time_stamps=True)
                check_cancelled()
                if not isinstance(results, list) or len(results) != 1:
                    raise ProviderError("Invalid Qwen result")
                result = results[0]
                if not isinstance(result.text, str):
                    raise ProviderError("Invalid Qwen transcript")
                if not result.text.strip():
                    continue
                if result.time_stamps is None or not len(result.time_stamps):
                    raise ProviderError("Qwen transcript has no alignment")
                aligned = _validate_qwen_alignment(result.time_stamps, (end - start) / 16000)
                segments.extend(Segment(row.start + start / 16000, row.end + start / 16000, row.text) for row in aligned)
            else:
                with tempfile.TemporaryDirectory(prefix="subshort-asr-") as temp:
                    wav = str(Path(temp) / "speech.wav")
                    _save_wav_audio(wav, clip, sampling_rate=16000)
                    results = self._model.transcribe(["speech"], [wav], {"use_gpu": self.device == "cuda"})
                check_cancelled()
                if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict) or not isinstance(results[0].get("text"), str):
                    raise ProviderError("Invalid FireRed transcript")
                text = results[0]["text"].strip()
                if text:
                    segments.append(Segment(start / 16000, end / 16000, text))
        return segments

    def close(self):
        self._model = self._vad = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
