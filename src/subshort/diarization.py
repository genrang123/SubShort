"""Local-only speaker diarization and review artifacts.

Diarization is deliberately a separate configured worker.  It never downloads a
model, accepts a token, or sends source audio to a service.  Speaker labels are
opaque and only stable within one job.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .execution import check_cancelled, run_process
from .providers import FatalProviderError, ProviderError, Segment, validate_segments

@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker_id: str
    status: str = "resolved"


def validate_turns(turns, duration=None):
    if not isinstance(turns, list):
        raise ProviderError("Lượt người nói không hợp lệ")
    result = []
    labels = {}
    for turn in turns:
        if isinstance(turn, dict):
            try:
                turn = SpeakerTurn(**turn)
            except (TypeError, ValueError):
                raise ProviderError("Lượt người nói không hợp lệ") from None
        if not isinstance(turn, SpeakerTurn):
            raise ProviderError("Lượt người nói không hợp lệ")
        if not isinstance(turn.speaker_id, str) or not turn.speaker_id.strip():
            raise ProviderError("Nhãn người nói không hợp lệ")
        if (isinstance(turn.start, bool) or isinstance(turn.end, bool) or turn.start < 0
                or turn.end <= turn.start or not all(isinstance(x, (int, float)) for x in (turn.start, turn.end))):
            raise ProviderError("Mốc lượt người nói không hợp lệ")
        if duration is not None and turn.end > duration:
            raise ProviderError("Lượt người nói vượt thời lượng nguồn")
        if turn.status not in {"resolved", "needs_review"}:
            raise ProviderError("Trạng thái lượt người nói không hợp lệ")
        labels.setdefault(turn.speaker_id, len(labels))
        result.append(turn)
    # Diarization may contain overlapping turns; preserve source order and do
    # not apply Segment's non-overlap validation here.
    result.sort(key=lambda item: (item.start, item.end, labels[item.speaker_id]))
    return result


def stable_labels(turns):
    """Rename backend labels by first appearance, never by gender/voice guess."""
    mapping = {}
    result = []
    for turn in validate_turns(turns):
        if turn.speaker_id not in mapping:
            mapping[turn.speaker_id] = f"speaker-{len(mapping) + 1}"
        result.append(SpeakerTurn(turn.start, turn.end, mapping[turn.speaker_id], turn.status))
    return result




def align_segments(segments, turns, *, resplit=None):
    """Attach labels by time overlap; multi-label cues require caller re-ASR.

    ``resplit`` receives a Segment and must return ASR-derived child segments
    with their own text and bounds.  Text is never divided by time proportion.
    """
    segments = validate_segments(segments)
    turns = validate_turns(turns)
    output = []
    for segment in segments:
        overlaps = []
        for turn in turns:
            overlap = max(0.0, min(segment.end, turn.end) - max(segment.start, turn.start))
            if overlap > 0:
                overlaps.append((overlap, turn))
        labels = {turn.speaker_id for _, turn in overlaps}
        if not labels:
            raise ProviderError(f"alignment {segment.source_id or segment.start}: không xác định người nói")
        if len(labels) == 1:
            label = next(iter(labels))
            output.append(Segment(segment.start, segment.end, segment.text, segment.source_id, label))
            continue
        if resplit is None:
            raise ProviderError(f"alignment {segment.source_id or segment.start}: một ASR cue có nhiều người nói; cần nhận dạng lại")
        children = resplit(segment)
        children = validate_segments(children, segment.end)
        if not children or children[0].start < segment.start or children[-1].end > segment.end:
            raise ProviderError(f"alignment {segment.source_id or segment.start}: kết quả nhận dạng lại vượt cue")
        for child in children:
            child_labels = {turn.speaker_id for turn in turns
                            if min(child.end, turn.end) > max(child.start, turn.start)}
            if len(child_labels) != 1:
                raise ProviderError(f"alignment {segment.source_id or segment.start}: lượt con chưa xác định người nói")
            output.append(Segment(child.start, child.end, child.text,
                                  child.source_id or segment.source_id, child_labels.pop()))
    return validate_segments(sorted(output, key=lambda item: (item.start, item.end)))


class DiarizationSession:
    """One request/response worker using a pre-provisioned local pyannote model."""
    def __init__(self, python_executable, model_path, *, device="cpu", hf_cache=""):
        if not python_executable:
            raise FatalProviderError("Diarization cần một Python worker local rõ ràng")
        self.python_executable = str(python_executable)
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.hf_cache = Path(hf_cache).expanduser() if hf_cache else None
        self.device = device
        if self.model_path is None or not self.model_path.is_dir():
            raise FatalProviderError("Không tìm thấy model diarization local; không tự tải model")
        if self.hf_cache is not None and not self.hf_cache.is_dir():
            raise FatalProviderError("Không tìm thấy cache diarization local")
        if device not in {"cpu", "cuda"}:
            raise FatalProviderError("Thiết bị diarization phải là cpu hoặc cuda")

    def diarize(self, audio: Path, duration=None):
        audio = Path(audio)
        if not audio.is_file():
            raise ProviderError(f"Không tìm thấy audio diarization: {audio}")
        check_cancelled()
        request = {"audio": str(audio.resolve()), "model_path": str(self.model_path.resolve()), "device": self.device}
        env = os.environ.copy()
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        if self.hf_cache is not None:
            env["HF_HOME"] = str(self.hf_cache.resolve())
            env["HF_HUB_CACHE"] = str(self.hf_cache.resolve())
        command = [self.python_executable, "-m", "subshort.diarization_worker"]
        try:
            result = run_process(command, input=json.dumps(request), capture_output=True, text=True,
                                 cwd=str(Path(__file__).resolve().parents[1]), env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FatalProviderError(f"Không chạy được worker diarization local: {exc}") from None
        check_cancelled()
        if result.returncode:
            detail = (result.stderr or "").strip().splitlines()
            raise FatalProviderError(detail[-1][:500] if detail else "Worker diarization thất bại")
        try:
            answer = json.loads(result.stdout)
            if not isinstance(answer, dict) or answer.get("ok") is not True or not isinstance(answer.get("turns"), list):
                raise ValueError
        except (ValueError, TypeError):
            raise FatalProviderError("Worker diarization trả về dữ liệu không hợp lệ") from None
        return validate_turns(answer["turns"], duration)


def review_fingerprint(source_hash, segments, turns, transcript, translation=None, speaker_voice_map=None):
    canonical_turns = [{"start": turn.start, "end": turn.end, "speaker_id": turn.speaker_id}
                       for turn in turns]
    payload = {"source": str(source_hash), "segments": [asdict(item) for item in segments],
               "turns": canonical_turns, "transcript": transcript,
               "translation": translation or {}, "speaker_voice_map": speaker_voice_map or {}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
