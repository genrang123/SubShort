from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
import copy
import json
import math
import shlex
import shutil
import sys
import uuid

from .credentials import CREDENTIAL_FIELDS, CredentialError, CredentialStore, _MIGRATE_ERROR, _write_atomic

ASR_MODELS = {"local": "faster-whisper-small", "qwen": "Qwen3-ASR-0.6B", "firered": "FireRedASR-AED-L"}


_SECRET_NAMES = frozenset(
    name.replace("_", "") for name in CREDENTIAL_FIELDS
) | {"apikey", "secret", "password", "credential", "credentials", "authorization"}


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub_secrets(v) for k, v in value.items()
                if not isinstance(k, str) or k.strip().lower().replace("_", "").replace("-", "") not in _SECRET_NAMES}
    if isinstance(value, list):
        return [_scrub_secrets(item) for item in value]
    return value


@dataclass
class Settings:
    root: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    asr_provider: str = "local"
    qwen_python: str = ""
    firered_python: str = ""
    asr_model: str = ""
    asr_device: str = "cpu"
    asr_aligner: str = ""
    asr_vad_model: str = ""
    firered_type: str = "aed"
    multi_speaker: bool = False
    diarization_model: str = ""
    diarization_python: str = sys.executable
    diarization_hf_cache: str = ""
    diarization_device: str = "cpu"
    translation_provider: str = "openai"
    translation_model: str = ""
    translation_base_url: str = ""
    tts_provider: str = "edge"
    tts_model: str = ""
    tts_python: str = sys.executable
    tts_hf_cache: str = ""
    tts_device: str = "cpu"
    tts_custom_voice: str = ""
    consent_omnivoice_license: bool = False
    openai_api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)
    openai_compatible_api_key: str = field(default="", repr=False)
    source_language: str = ""
    mode: str = "subtitle_dub"
    target_language: str = "vi"
    keep_original_audio: bool = False
    separate_background: bool = False
    output_dir: str = "output"
    slow_speed: float = 0.7
    final_speed: float = 1.0
    tts_voice: str = "vi-VN-HoaiMyNeural"
    tts_speed: float = 1.0
    background_volume: float = 0.25
    separator_model_dir: str = ""
    separator_python: str = sys.executable
    drive_folder_id: str = ""
    output_layout: str = "original"
    video_encoder: str = "cpu"
    video_bitrate_mbps: float = 8.0
    old_subtitle_mode: str = "off"
    old_subtitle_region: str = ""
    vision_provider: str = "openai"
    vision_model: str = "gpt-4o-mini"
    hook_enabled: bool = False
    hook_theme: str = "auto"
    hook_seed: int = 0
    hook_duration: float = 3.0
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    _OBSOLETE_FIELDS = frozenset({
        "firered_command", "firered_model", "qwen_runtime", "qwen_model_dir", "qwen_device",
        "vienneu_tts_command", "vienneu_model",
        "merge_words", "merge_segments",
        "consent_translation", "consent_edge_tts", "consent_vision_frames",
    })

    @classmethod
    def load(cls, root: Path) -> "Settings":
        path = root / "config" / "settings.json"
        data: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("tts_provider") == "vienneu":
            data["tts_provider"] = "vieneu"
        if data.get("tts_python") == "":
            data.pop("tts_python")
        if data.get("separator_python") == "":
            data.pop("separator_python")
        store = CredentialStore(root)
        protected = store.load()
        legacy = {}
        for name in CREDENTIAL_FIELDS:
            value = data.get(name, "")
            if value in (None, ""):
                continue
            if not isinstance(value, str):
                raise CredentialError(_MIGRATE_ERROR)
            legacy[name] = value
        credentials = {name: legacy.get(name) or protected[name] for name in CREDENTIAL_FIELDS}
        if legacy:
            try:
                store.save(credentials)
            except CredentialError:
                raise CredentialError(_MIGRATE_ERROR) from None
            clean = _scrub_secrets({k: v for k, v in data.items() if k not in CREDENTIAL_FIELDS})
            try:
                _write_atomic(path, json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8"))
            except Exception:
                raise CredentialError(_MIGRATE_ERROR) from None
        data = _scrub_secrets({k: v for k, v in data.items() if k not in CREDENTIAL_FIELDS})
        return cls(root=root, **credentials,
                   **{k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "root"})

    def validate_llm(self) -> None:
        if self.translation_provider not in ("openai", "gemini", "openai_compatible"):
            raise ValueError("Chọn dịch vụ dịch văn bản được hỗ trợ.")
        if self.vision_provider not in ("openai", "gemini"):
            raise ValueError("Chọn OpenAI hoặc Gemini để nhận diện phụ đề cũ.")
        if not isinstance(self.translation_model, str):
            raise ValueError("translation_model phải là chuỗi")
        if not isinstance(self.translation_base_url, str):
            raise ValueError("translation_base_url phải là chuỗi")
        if self.translation_provider == "openai_compatible":
            from .providers import OpenAICompatibleTranslator
            OpenAICompatibleTranslator(self.openai_compatible_api_key, self.translation_model, self.translation_base_url)
        if not isinstance(self.vision_model, str) or not self.vision_model.strip():
            raise ValueError("Nhập Model ID hỗ trợ hình ảnh để nhận diện phụ đề cũ.")

    def validate(self, mode: str | None = None) -> None:
        for name, supported in (
            ("asr_provider", ASR_MODELS),
            ("asr_device", {"cpu", "cuda"}),
            ("firered_type", {"aed", "llm"}),
            ("old_subtitle_mode", {"off", "manual", "auto"}),
            ("tts_provider", {"edge", "vieneu", "omnivoice", "none"}),
            ("tts_device", {"cpu", "cuda"}),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or value not in supported:
                raise ValueError(f"{name} chưa được hỗ trợ: {value}")
        active_mode = self.mode if mode is None else mode
        if active_mode not in {"subtitle_dub", "subtitle", "dub"}:
            raise ValueError("mode chưa được hỗ trợ; chọn subtitle_dub, subtitle hoặc dub")
        for name in ("slow_speed", "final_speed"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} phải là số hữu hạn lớn hơn 0")
        if type(self.tts_speed) not in (int, float) or not 0.5 <= self.tts_speed <= 2.0:
            raise ValueError("Tốc độ đọc phải là số từ 0.5 đến 2.0")
        if type(self.background_volume) not in (int, float) or not math.isfinite(self.background_volume) or not 0 <= self.background_volume <= 1:
            raise ValueError("background_volume dùng đơn vị 0–1; không tự chuyển từ phần trăm")
        if not isinstance(self.source_language, str) or not isinstance(self.target_language, str) or not self.target_language.strip():
            raise ValueError("Ngôn ngữ nguồn phải là chuỗi; ngôn ngữ đích không được rỗng")
        self.validate_llm()
        if not isinstance(self.old_subtitle_region, str):
            raise ValueError("old_subtitle_region phải là JSON [x,y,w,h]")
        if self.old_subtitle_mode == "manual":
            from .subtitle_region import validate_region
            try:
                validate_region(json.loads(self.old_subtitle_region))
            except (ValueError, TypeError):
                raise ValueError("Vùng phụ đề thủ công phải là JSON [x,y,w,h] chuẩn hóa hợp lệ") from None
        for name in ("keep_original_audio", "separate_background", "consent_omnivoice_license"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} phải là boolean")
        if type(self.multi_speaker) is not bool:
            raise ValueError("multi_speaker phải là boolean")
        if self.diarization_device not in {"cpu", "cuda"}:
            raise ValueError("diarization_device phải là cpu hoặc cuda")
        if self.multi_speaker:
            self._check_path(self.diarization_model, directory=True, required="diarization_model")
            self._check_command(self.diarization_python)
            if self.diarization_hf_cache:
                self._check_path(self.diarization_hf_cache, directory=True, required="diarization_hf_cache")
        if type(self.hook_enabled) is not bool:
            raise ValueError("hook_enabled phải là boolean")
        if self.output_layout not in {"original", "portrait", "landscape"}:
            raise ValueError("output_layout không hợp lệ")
        if self.video_encoder not in {"cpu", "auto", "nvenc"}:
            raise ValueError("video_encoder không hợp lệ")
        if type(self.video_bitrate_mbps) not in (int, float) or not math.isfinite(self.video_bitrate_mbps) or not .1 <= self.video_bitrate_mbps <= 100:
            raise ValueError("video_bitrate_mbps phải trong 0.1–100")
        if self.hook_theme not in {"auto", "bold", "minimal"} or type(self.hook_seed) is not int:
            raise ValueError("Theme/seed hook không hợp lệ")
        if type(self.hook_duration) not in (int, float) or not math.isfinite(self.hook_duration) or not 1 <= self.hook_duration <= 8:
            raise ValueError("hook_duration phải trong 1–8 giây")
        if not isinstance(self.output_dir, str) or not self.output_dir.strip():
            raise ValueError("output_dir không được rỗng")
        output = self.root / self.output_dir
        if output.exists() and not output.is_dir():
            raise ValueError(f"output_dir không phải thư mục: {output}")
        for command in (self.ffmpeg, self.ffprobe):
            if not (Path(command).is_file() or shutil.which(command)):
                raise ValueError(f"Không tìm thấy executable: {command}")

        self.asr_worker_python()
        self.asr_assets()

        speaking = active_mode != "subtitle" and self.tts_provider != "none"
        if self.tts_provider == "edge":
            if speaking and not self.tts_voice.strip():
                raise ValueError("tts_voice không được rỗng")
        elif self.tts_provider in {"vieneu", "omnivoice"}:
            self._check_path(self.tts_model, directory=True, required="tts_model")
            self._check_path(self.tts_hf_cache, directory=True, required="tts_hf_cache")
            self._check_command(self.tts_python)
            if speaking and self.tts_provider == "omnivoice" and not self.consent_omnivoice_license:
                raise ValueError("Cần chấp nhận giấy phép OmniVoice trước khi tạo giọng nói")
            if self.tts_custom_voice:
                try:
                    uuid.UUID(self.tts_custom_voice)
                except (ValueError, AttributeError):
                    raise ValueError("tts_custom_voice phải là UUID") from None

        if self.separate_background and speaking and self.background_volume > 0:
            self._check_path(self.separator_model_dir, directory=True, required="separator_model_dir")
            self._check_command(self.separator_python)

    def asr_assets(self) -> tuple[Path, Path | None, Path | None]:
        install = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else self.root
        models = install / "ModelASR"
        if self.asr_provider not in ASR_MODELS:
            raise ValueError(f"asr_provider chưa được hỗ trợ: {self.asr_provider}")
        name = ASR_MODELS[self.asr_provider]
        model = models / name
        if self.asr_provider == "local" and self.asr_model == "" and not model.is_dir():
            cache = models / "models--Systran--faster-whisper-small"
            ref = cache / "refs" / "main"
            if ref.is_file():
                revision = ref.read_text(encoding="utf-8").strip()
                if len(revision) == 40 and all(c in "0123456789abcdef" for c in revision):
                    model = cache / "snapshots" / revision
        if self.asr_model != "":
            self._check_path(self.asr_model, directory=True, required="asr_model")
            model = self.root / self.asr_model
        else:
            self._check_path(model, directory=True, required="asr_model")
        if self.asr_provider == "local":
            return model, None, None
        aligner = None
        if self.asr_provider == "qwen":
            if self.asr_aligner != "":
                self._check_path(self.asr_aligner, directory=True, required="asr_aligner")
                aligner = self.root / self.asr_aligner
            else:
                aligner = models / "Qwen3-ForcedAligner-0.6B"
                self._check_path(aligner, directory=True, required="asr_aligner")
        vad = models / "silero_vad.jit"
        if self.asr_vad_model != "":
            self._check_path(self.asr_vad_model, directory=False, required="asr_vad_model")
            vad = self.root / self.asr_vad_model
        elif not vad.is_file():
            python = self.asr_worker_python() or Path(sys.executable)
            prefix = python.parent.parent if python.parent.name.lower() == "scripts" else python.parent
            installed_vad = prefix / "Lib/site-packages/silero_vad/data/silero_vad.jit"
            if installed_vad.is_file():
                vad = installed_vad
        self._check_path(vad, directory=False, required="asr_vad_model")
        return model, aligner, vad

    def asr_worker_python(self) -> Path | None:
        if self.asr_provider not in {"qwen", "firered"}:
            return None
        frozen = getattr(sys, "frozen", False)
        if frozen:
            bundled = Path(sys.executable).resolve().parent / "runtimes" / self.asr_provider / "Scripts" / "python.exe"
            if bundled.is_file():
                return bundled.resolve()
        name = f"{self.asr_provider}_python"
        value = getattr(self, name)
        if not isinstance(value, str):
            raise ValueError(f"{name} phải là đường dẫn đến Python")
        if not value.strip():
            if frozen:
                raise ValueError(f"Chưa cài môi trường Python cho {ASR_MODELS[self.asr_provider]}. Hãy cài lại bằng bộ cài SubShort.")
            local = self.root / f".venv-{self.asr_provider}" / "Scripts" / "python.exe"
            return local.resolve() if local.is_file() else None
        self._check_path(value, directory=False, required=name)
        path = (self.root / value).resolve()
        if frozen and path == Path(sys.executable).resolve():
            raise ValueError(f"{name} phải trỏ đến Python của môi trường ASR, không phải SubShort.exe")
        return path

    def _check_path(self, value: str | Path, *, directory: bool | None, required: str | None = None) -> None:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError(f"{required or 'path'} không được rỗng")
        path = self.root / value
        exists = path.exists() if directory is None else (path.is_dir() if directory else path.is_file())
        if not exists:
            raise ValueError(f"Không tìm thấy đường dẫn ({required or 'path'}): {path}")

    def _check_command(self, command: str) -> None:
        parts = shlex.split(command, posix=False) if isinstance(command, str) else []
        executable = parts[0].strip('"') if parts else ""
        if not executable or not (Path(executable).is_file() or shutil.which(executable)):
            raise ValueError(f"Không tìm thấy executable của command: {executable}")


    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy({f.name: getattr(self, f.name) for f in fields(self)
                              if f.name not in {"root", "extra", *CREDENTIAL_FIELDS}})

    def for_job(self, job: "Job") -> "Settings":
        data = self.snapshot()
        job_data = copy.deepcopy(job.config) if job.config else {}
        data.update(job_data)
        if data.get("tts_provider") == "vienneu":
            data["tts_provider"] = "vieneu"
        # Historical wrapper/runtime keys must not activate a replacement model.
        data = {k: v for k, v in data.items() if k not in self._OBSOLETE_FIELDS}
        data["target_language"], data["mode"] = job.target_language, job.mode
        allowed = self.snapshot().keys()
        return Settings(root=self.root, openai_api_key=self.openai_api_key,
                        gemini_api_key=self.gemini_api_key,
                        openai_compatible_api_key=self.openai_compatible_api_key,
                        **{k: v for k, v in data.items() if k in allowed})

    def save(self) -> None:
        CredentialStore(self.root).save({name: getattr(self, name) for name in CREDENTIAL_FIELDS})
        path = self.root / "config" / "settings.json"
        try:
            data = _scrub_secrets({f.name: copy.deepcopy(getattr(self, f.name)) for f in fields(self)
                                   if f.name != "root" and f.name not in CREDENTIAL_FIELDS})
            _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        except Exception:
            raise CredentialError("Không lưu được cấu hình an toàn; dữ liệu hiện có được giữ nguyên") from None


@dataclass
class Job:
    id: str
    source: str
    target_language: str
    mode: str = "subtitle_dub"
    status: str = "queued"
    output: str = ""
    error: str = ""
    progress: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    stage: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"queued", "running", "done", "failed", "cancelled"}:
            raise ValueError(f"Trạng thái job không hợp lệ: {self.status}")

    def prepare(self, settings: Settings) -> Settings:
        self.output, self.error, self.stage = "", "", ""
        self.progress, self.metadata = 0.0, {}
        effective = settings.for_job(self)
        effective.validate(self.mode)
        review = {key: copy.deepcopy(self.config[key]) for key in
                  ("speaker_review", "review_transcript", "review_translation", "review_translation_keys") if key in self.config}
        self.config = {**effective.snapshot(), **review}
        self.status = "running"
        return effective
