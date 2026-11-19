from pathlib import Path
from uuid import uuid4
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from .models import Settings, Job
from .queue import JobQueue, QueueError
from .media import MediaAdapter
from .providers import OpenAITranslator, EdgeTTS, build_timeline, source_windows, write_srt
from .asr import ASRSession
from .tts import TtsSession
from .voices import VoiceRegistry
from .separation import separate_background
from .diarization import DiarizationSession, align_segments, stable_labels, validate_turns, speaker_windows, review_fingerprint
import json
import os
from .providers import validate_segments, Segment, timeline_alignment
from .batch import Batch
import queue as messages
import time
from collections import deque
from .execution import Execution, Cancelled, check_cancelled
from .providers import FatalProviderError
from .credentials import CredentialError
from .providers import GeminiTranslator, OpenAICompatibleTranslator
from .paths import artifact_directory
from .ui import apply_theme, FG


class Pipeline:
    def __init__(self, settings, queue, notify):
        self.settings, self.queue, self.notify = settings, queue, notify
        self.execution = Execution()
        self._asr_sessions = {}
        self._tts_sessions = {}
    def _diarization(self, settings):
        key = (self._path(settings.root, settings.diarization_python),
               self._path(settings.root, settings.diarization_model),
               self._path(settings.root, settings.diarization_hf_cache), settings.diarization_device)
        session = getattr(self, "_diarization_session", None)
        if session is None or getattr(self, "_diarization_key", None) != key:
            session = DiarizationSession(key[0], key[1], device=key[3], hf_cache=key[2] or "")
            self._diarization_session, self._diarization_key = session, key
        return session

    def close(self):
        """Release cached models and workers."""
        sessions = [*self._asr_sessions.values(), *self._tts_sessions.values()]
        diarization = getattr(self, "_diarization_session", None)
        if diarization is not None:
            sessions.append(diarization)
        self._asr_sessions.clear(); self._tts_sessions.clear(); self._diarization_session = None
        for session in sessions:
            close = getattr(session, "close", None)
            if close is not None:
                close()

    def _stage(self, job, name, progress):
        job.stage, job.progress = name, max(0.0, min(1.0, float(progress)))
        self.queue.update(job)
        self.notify(job)

    @staticmethod
    def _path(root, value):
        if not value:
            return None
        path = Path(value)
        return str((path if path.is_absolute() else root / path).resolve())

    def _asr(self, settings, diagnostic=None):
        engine = "whisper" if settings.asr_provider == "local" else settings.asr_provider
        key = (engine, self._path(settings.root, settings.asr_model), settings.asr_device,
               self._path(settings.root, settings.asr_aligner), self._path(settings.root, settings.asr_vad_model), settings.firered_type)
        if key not in self._asr_sessions:
            self._asr_sessions[key] = ASRSession(key[0], key[1], key[2], forced_aligner_path=key[3],
                                                 vad_model_path=key[4], firered_type=key[5], diagnostic=diagnostic)
        elif diagnostic is not None:
            self._asr_sessions[key].diagnostic = diagnostic
        return self._asr_sessions[key]

    def _tts(self, settings, diagnostic=None):
        key = (settings.tts_provider, self._path(settings.root, settings.tts_python),
               self._path(settings.root, settings.tts_model), self._path(settings.root, settings.tts_hf_cache),
               settings.tts_device, settings.consent_omnivoice_license)
        if key not in self._tts_sessions:
            self._tts_sessions[key] = TtsSession(key[1], provider=key[0], model_path=key[2], hf_cache=key[3],
                                                 device=key[4], license_accepted=key[5], diagnostic=diagnostic)
        elif diagnostic is not None:
            self._tts_sessions[key].diagnostic = diagnostic
        return self._tts_sessions[key]


    def cancel(self):
        self.execution.cancel()

    def run(self, job, *, keep_open=False):
        try:
            with self.execution.activate():
                return self._run(job)
        finally:
            if not keep_open:
                self.close()

    def _run(self, job):
        pending = None
        stages = []
        try:
            check_cancelled()
            settings = job.prepare(self.settings)
            self.queue.update(job); self.notify(job)
            media = MediaAdapter(settings.ffmpeg, settings.ffprobe)
            root = settings.root
            output_dir = root / settings.output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            work = artifact_directory(root, settings.output_dir, job.id)
            work.mkdir(parents=True, exist_ok=True)
            def diagnostic(message):
                with (work / "diagnostics.log").open("a", encoding="utf-8") as log:
                    log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
                self.notify(job, message)
            from .sources import classify_source, resolve_source
            if classify_source(job.source) != "local":
                self._stage(job, "download", 0.03)
                video = resolve_source(job.source, work, media, lambda text: self.notify(job, text))
                stages.append("download")
            else:
                video = Path(job.source)
            audio = work / "audio_recognition.wav"
            self._stage(job, "probe", 0.08); check_cancelled()
            source = media.probe(video)
            stages.append("probe")
            if not source["has_audio"]:
                self._preserve_source(job, video, output_dir, work, "no_audio")
                return
            from .artifacts import ArtifactCache
            cache = ArtifactCache(work, video, {}) if video.is_file() else None
            reused_stages = []
            model_hashes = {}

            def model_fingerprint(configured):
                if not configured or cache is None:
                    return configured
                path = Path(configured)
                if not path.is_absolute():
                    path = root / path
                path = path.resolve()
                if path not in model_hashes:
                    files = sorted(path.rglob("*")) if path.is_dir() else [path]
                    model_hashes[path] = {
                        str(item.relative_to(path) if path.is_dir() else item): cache._hash_file(item)
                        for item in files if item.is_file()
                    }
                return {"path": str(path), "files": model_hashes[path]}

            asr_dependencies = {
                "language": settings.source_language, "provider": settings.asr_provider,
                "model": model_fingerprint(settings.asr_model), "device": settings.asr_device,
                "aligner": model_fingerprint(settings.asr_aligner), "vad": model_fingerprint(settings.asr_vad_model),
                "firered_type": settings.firered_type,
            }
            self._stage(job, "asr", 0.20)
            segments = cache.load_segments("asr", asr_dependencies) if cache else None
            if segments is None:
                media.extract_audio(video, audio)
                self.notify(job, f"ASR {settings.asr_provider} · {settings.asr_device} · đang nhận dạng {source['duration']:.1f}s")
                segments = validate_segments(self._asr(settings, diagnostic).transcribe(audio, settings.source_language or None), source["duration"])
                if cache:
                    cache.save_segments("asr", asr_dependencies, segments)
                stages.extend(["extract_audio", "asr"])
                self.notify(job, f"Nhận dạng xong: {len(segments)} cue · {source['duration']:.1f}s nguồn")
            else:
                segments = validate_segments(segments, source["duration"])
                reused_stages.append("asr")
                self.notify(job, f"Nhận dạng: dùng lại cache ({len(segments)} cue)")
            check_cancelled()
            if not segments:
                self._preserve_source(job, video, output_dir, work, "no_speech")
                return
            source_segments = [Segment(s.start, s.end, s.text, str(i), s.speaker_id) for i, s in enumerate(segments)]
            segments = list(source_segments)
            job.metadata["review_transcript"] = [segment.__dict__ for segment in source_segments]
            write_srt(source_segments, work / "transcript_raw.srt")
            job_config = getattr(job, "config", {})
            transcript_edits = job_config.get("review_transcript", {}) if isinstance(job_config, dict) else {}
            if isinstance(transcript_edits, dict):
                segments = [Segment(s.start, s.end, str(transcript_edits.get(s.source_id, s.text)), s.source_id, s.speaker_id) for s in segments]
            if settings.multi_speaker:
                if not audio.exists():
                    media.extract_audio(video, audio)
                turns_path = work / "diarization.json"
                diarization_dependencies = {"policy": "pyannote-community-1", "audio": model_fingerprint(audio),
                                            "model": model_fingerprint(settings.diarization_model), "device": settings.diarization_device,
                                            "python": settings.diarization_python}
                turns = None
                try:
                    meta_path = work / "diarization.dependencies.json"
                    if turns_path.is_file() and meta_path.is_file() and json.loads(meta_path.read_text(encoding="utf-8")) == diarization_dependencies:
                        turns = validate_turns(json.loads(turns_path.read_text(encoding="utf-8")), source["duration"])
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    turns = None
                if turns is None:
                    self._stage(job, "diarization", 0.29); check_cancelled()
                    turns = stable_labels(self._diarization(settings).diarize(audio, source["duration"])); turns = [type(turn)(turn.start, turn.end, turn.speaker_id, "needs_review") for turn in turns]
                    turns_path.write_text(json.dumps([turn.__dict__ for turn in turns], ensure_ascii=False, indent=2), encoding="utf-8")
                    (work / "diarization.dependencies.json").write_text(json.dumps(diarization_dependencies, sort_keys=True), encoding="utf-8")
                samples_dir = work / "review"; samples_dir.mkdir(parents=True, exist_ok=True)
                sample_paths = {}
                for turn in turns:
                    sample = samples_dir / f"{turn.speaker_id}.wav"
                    if not sample.exists():
                        media.extract_audio_window(video, sample, turn.start, turn.end)
                    sample_paths.setdefault(turn.speaker_id, str(sample))
                stages.append("speaker_samples")
                review_source_hash = cache.source_hash if cache else str(video.resolve())
                job.metadata["speaker_samples"] = sample_paths
                job.metadata["review_source_hash"] = review_source_hash
                job.metadata["review_transcript"] = [segment.__dict__ for segment in segments]
                review = job_config.get("speaker_review") if isinstance(job_config, dict) else None
                expected_review_fingerprint = review_fingerprint(review_source_hash, segments, turns, [segment.text for segment in segments])
                if not isinstance(review, dict) or review.get("approved") is not True:
                    job.metadata = {"stages": stages, "speaker_review_required": True, "review_fingerprint": expected_review_fingerprint,
                                    "review_source_hash": review_source_hash,
                                    "review_transcript": [segment.__dict__ for segment in segments],
                                    "transcript": [segment.__dict__ for segment in segments], "speaker_samples": sample_paths,
                                    "diarization": [turn.__dict__ for turn in turns]}
                    raise FatalProviderError("Cần duyệt nhãn lượt nói và ánh xạ speaker→voice trước khi chạy nhiều giọng")
                reviewed = validate_turns(review.get("turns", []), source["duration"])
                if any(turn.status != "resolved" for turn in reviewed):
                    raise FatalProviderError("Còn lượt người nói chưa được duyệt")
                expected_review_fingerprint = review_fingerprint(review_source_hash, segments, reviewed, [segment.text for segment in segments])
                if review.get("fingerprint") != expected_review_fingerprint:
                    raise FatalProviderError("Bản duyệt speaker đã cũ; cần duyệt lại transcript/turns hiện tại")
                voice_map = review.get("voice_map")
                if not isinstance(voice_map, dict) or not voice_map or any(not isinstance(key, str) or not isinstance(value, str) or not value.strip() for key, value in voice_map.items()):
                    raise FatalProviderError("Ánh xạ speaker→voice chưa đầy đủ")
                try:
                    asr_session = self._asr(settings, diagnostic)
                    def resplit(segment):
                        children = []
                        turn_windows = [(max(segment.start, turn.start), min(segment.end, turn.end), turn)
                                        for turn in reviewed if min(segment.end, turn.end) > max(segment.start, turn.start)]
                        for window_start, window_end, turn in turn_windows:
                            window_audio = work / "review" / f"{segment.source_id}-{turn.speaker_id}.wav"
                            media.extract_audio_window(video, window_audio, window_start, window_end)
                            for child in asr_session.transcribe(window_audio, settings.source_language or None):
                                if child.end > child.start:
                                    children.append(Segment(window_start + child.start, window_start + child.end, child.text,
                                                            f"{segment.source_id}.{len(children)}"))
                        return children
                    segments = align_segments(segments, reviewed, resplit=resplit)
                    source_segments = list(segments)
                except Exception as exc:
                    raise FatalProviderError(str(exc)) from None
                speakers = {segment.speaker_id for segment in segments}
                if speakers - set(voice_map):
                    raise FatalProviderError("Ánh xạ speaker→voice chưa đầy đủ")
                job.metadata["speaker_review"] = {"turns": [turn.__dict__ for turn in reviewed], "voice_map": voice_map, "fingerprint": expected_review_fingerprint}
            self._stage(job, "translate", 0.36); check_cancelled()
            from .providers import TRANSLATION_POLICY
            translation_dependencies = {
                "policy": TRANSLATION_POLICY,
                "segments": [[s.source_id, s.start, s.end, s.text, s.speaker_id] for s in segments],
                "provider": settings.translation_provider, "model": settings.translation_model,
                "language": settings.target_language,
                "base_url": settings.translation_base_url if settings.translation_provider == "openai_compatible" else "",
            }
            translator_class = GeminiTranslator if settings.translation_provider == "gemini" else OpenAITranslator
            key = settings.gemini_api_key if settings.translation_provider == "gemini" else settings.openai_api_key
            options = {"model": settings.translation_model} if settings.translation_model else {}
            if settings.translation_provider == "openai_compatible":
                translator_class, key = OpenAICompatibleTranslator, settings.openai_compatible_api_key
                options["base_url"] = settings.translation_base_url
            translator = translator_class(key, **options)
            translator.diagnostic = diagnostic
            translated = cache.load_segments("translate", translation_dependencies) if cache else None
            if translated is None:
                translated = translator.translate(segments, settings.target_language)
                if cache:
                    cache.save_segments("translate", translation_dependencies, translated)
                stages.append("translate")
                self.notify(job, f"Dịch xong: {len(translated)} cue sang {settings.target_language}")
            else:
                reused_stages.append("translate")
                self.notify(job, f"Dịch: dùng lại cache ({len(translated)} cue)")
            translation_edits = job_config.get("review_translation", {}) if isinstance(job_config, dict) else {}
            if isinstance(translation_edits, dict):
                translated = [Segment(item.start, item.end, str(translation_edits.get(item.source_id or source_segments[index].source_id, item.text)), item.source_id, item.speaker_id)
                              for index, item in enumerate(translated)]
            segments = [Segment(item.start, item.end, item.text, item.source_id or source_segments[index].source_id,
                                item.speaker_id or source_segments[index].speaker_id) for index, item in enumerate(translated)]
            job.metadata["review_translation"] = [segment.__dict__ for segment in segments]
            job.metadata["review_source_hash"] = cache.source_hash if cache else str(video.resolve())
            check_cancelled()
            write_srt(segments, work / "translation_full.srt")
            has_voice = settings.mode != "subtitle" and settings.tts_provider in ("edge", "vieneu", "omnivoice")
            if has_voice:
                self._stage(job, "tts", 0.52); check_cancelled()
                import asyncio
                voice = None
                if settings.tts_custom_voice and settings.tts_provider != "edge":
                    voice = VoiceRegistry(root, media).resolve(settings.tts_custom_voice)
                    if voice["provider"] != settings.tts_provider:
                        raise FatalProviderError("Giọng tùy chỉnh không thuộc provider TTS đã chọn")
                multi_voice_map = job.metadata.get("speaker_review", {}).get("voice_map", {}) if settings.multi_speaker else {}
                if settings.multi_speaker and not isinstance(multi_voice_map, dict):
                    raise FatalProviderError("Ánh xạ speaker→voice không hợp lệ")
                speaker_voices = {}
                if settings.multi_speaker:
                    registry = VoiceRegistry(root, media)
                    for speaker, voice_id in multi_voice_map.items():
                        if settings.tts_provider == "edge":
                            if not isinstance(voice_id, str) or not voice_id.strip():
                                raise FatalProviderError("Tên Edge voice không hợp lệ")
                            speaker_voices[speaker] = voice_id
                        else:
                            try:
                                selected = registry.resolve(voice_id)
                            except (KeyError, ValueError, OSError):
                                if settings.tts_provider == "vieneu" and voice_id in {item["id"] for item in self._tts(settings, diagnostic).voices()}:
                                    speaker_voices[speaker] = voice_id
                                    continue
                                raise FatalProviderError(f"Không tìm thấy voice cho {speaker}") from None
                            if selected["provider"] != settings.tts_provider:
                                raise FatalProviderError(f"Voice {speaker} không thuộc provider TTS đã chọn")
                            speaker_voices[speaker] = selected
                clips = []
                windows = speaker_windows(source_segments, source["duration"], settings.slow_speed) if settings.multi_speaker else source_windows(source_segments, source["duration"], settings.slow_speed)
                fit_info = []
                tts_executed = False
                tts_dependencies = {
                    "provider": settings.tts_provider, "voice": settings.tts_voice,
                    "speaker_voice_map": multi_voice_map,
                    "model": model_fingerprint(settings.tts_model) if settings.tts_provider != "edge" else "",
                    "device": settings.tts_device,
                    "custom_voice": {"audio": model_fingerprint(voice["path"]), "ref_text": voice["ref_text"]} if voice else None,
                }
                for index, segment in enumerate(segments, 1):
                    self._stage(job, "tts", 0.52 + 0.18 * index / len(segments))
                    check_cancelled()
                    identity = source_segments[index - 1].source_id
                    speaker_id = source_segments[index - 1].speaker_id
                    segment = Segment(segment.start, segment.end, segment.text, identity, speaker_id)
                    selected_voice = speaker_voices.get(speaker_id, settings.tts_voice) if settings.multi_speaker else settings.tts_voice
                    start, limit = windows[index - 1]
                    window_frames = limit - start
                    shortening_dependencies = {
                        "policy": "source-windows-v1", "translation": translation_dependencies,
                        "tts": tts_dependencies, "speaker_id": speaker_id,
                        "window_start_frame": start, "window_end_frame": limit,
                    }
                    shortened = cache.shortening(shortening_dependencies, identity) if cache else None
                    if shortened is not None:
                        segment = Segment(segment.start, segment.end, shortened, identity, speaker_id)
                        segments[index - 1] = segment
                    clip_dir = work / "clips"
                    clip_dir.mkdir(parents=True, exist_ok=True)
                    clip = clip_dir / f"{index:05}.fit.wav"
                    for attempt in range(2):
                        dependencies = {**tts_dependencies, "source_id": identity, "speaker_id": speaker_id, "text": segment.text}
                        stage = f"tts_clip_{index}" + ("_short" if shortened is not None else "")
                        reused = cache.load_file(stage, dependencies) if cache else None
                        raw = clip_dir / (f"{index:05}{'_short' if shortened is not None else ''}.mp3" if settings.tts_provider == "edge" else f"{index:05}{'_short' if shortened is not None else ''}.raw.wav")
                        normalized = clip_dir / f"{index:05}.normalized.wav"
                        if reused is None:
                            if settings.tts_provider == "edge":
                                asyncio.run(EdgeTTS().synthesize(segment.text, raw, selected_voice))
                            else:
                                reference = selected_voice if isinstance(selected_voice, dict) else (voice if not settings.multi_speaker else None)
                                options = {"ref_audio": reference["path"]} if reference else {"voice": selected_voice or None}
                                if reference and settings.tts_provider == "omnivoice":
                                    options["ref_text"] = reference["ref_text"]
                                self._tts(settings, diagnostic).synthesize(segment.text, raw, **options)
                            if cache:
                                cache.save_file(stage, dependencies, raw)
                            tts_executed = True
                        else:
                            raw = reused
                            reused_stages.append(stage)
                        media.normalize_clip(raw, normalized)
                        fitted = media.fit_clip(normalized, clip, window_frames)
                        if fitted is not None:
                            break
                        raw_duration = media.clip_frames(normalized) / 24000
                        if shortened is not None or attempt:
                            raise ValueError(f"alignment {identity}: source={segment.start:.6f}-{segment.end:.6f}s clip={raw_duration:.6f}s window={window_frames / 24000:.6f}s shortening=1")
                        if cache:
                            cache.shortening(shortening_dependencies, identity, begin=True)
                        diagnostic(f"Rút gọn clip {index}/{len(segments)} · source_id={identity} · nguồn={segment.start:.3f}-{segment.end:.3f}s · audio={raw_duration:.3f}s · cửa sổ={window_frames / 24000:.3f}s")
                        segment = translator.shorten(segment, source_segments[index - 1], source_segments, settings.target_language, window_frames / 24000)
                        if cache:
                            cache.shortening(shortening_dependencies, identity, result=segment)
                            cache.save_segments(f"shortened_{identity}", shortening_dependencies, [segment])
                        segments[index - 1] = segment
                        write_srt(segments, work / "translated_source.srt")
                        if cache:
                            cache.save_segments("translation_used", translation_dependencies, segments)
                        shortened = segment.text
                    segments[index - 1] = segment
                    fit_info.append({**fitted, "shortening_count": int(shortened is not None)})
                    clips.append(clip)
                if tts_executed:
                    stages.append("tts")
                    self.notify(job, f"TTS xong: {len(clips)} clip · {len(segments)} cue · {settings.tts_provider}")
                else:
                    reused_stages.append("tts")
                    self.notify(job, f"TTS: dùng lại cache ({len(clips)} clip)")
                write_srt(segments, work / "translation_shortened.srt")
                if cache:
                    cache.save_segments("translation_used", translation_dependencies, segments)
                timeline = build_timeline(segments, clips, settings.slow_speed, source_duration=source["duration"], windows=windows)
                for row, info in zip(timeline, fit_info):
                    row.update(info)
                alignment = timeline_alignment(timeline, source["duration"], settings.slow_speed, settings.final_speed, allow_overlap=settings.multi_speaker)
                (work / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
                dubbed = work / "dubbed_slow.wav"
                mixer = media.mix_timeline_audio if settings.multi_speaker else media.timeline_audio
                mixer(clips, [row["actual_start_seconds"] for row in timeline], dubbed, alignment["target_duration_seconds"])
                captions = [Segment(row["actual_start_seconds"] / settings.final_speed, row["actual_end_seconds"] / settings.final_speed, row["text"], row.get("source_id", ""), row.get("speaker_id", "")) for row in timeline]
            else:
                alignment = timeline_alignment([], source["duration"], settings.slow_speed, settings.final_speed)
                dubbed = work / "original_slow.wav"
                media.prepare_audio(video, dubbed, settings.slow_speed, alignment["target_duration_seconds"])
                captions = [Segment(s.start / (settings.slow_speed * settings.final_speed), s.end / (settings.slow_speed * settings.final_speed), s.text) for s in segments]
            caption_info = {}
            subtitles = None
            from .subtitles import write_ass
            dimensions = media.output_dimensions(source.get("width", 1920), source.get("height", 1080), settings.output_layout)
            hook_info = None
            if settings.mode != "dub":
                if has_voice:
                    from .captions import build_captions
                    self._stage(job, "caption_asr", 0.72); check_cancelled()
                    voice_final = work / "voice_final.wav"
                    media.prepare_audio(dubbed, voice_final, settings.final_speed, alignment["final_duration_seconds"])
                    duration = alignment["final_duration_seconds"]
                    # Recognize the dubbed voice only, never the final mix. ModelProviders must
                    # expose ASRSession.transcribe_words(voice, language) -> list[Segment] for the
                    # configured backend; without it the default recognizer runs on this voice.
                    caption_asr = self._asr(settings, diagnostic)
                    words = getattr(caption_asr, "transcribe_words", None)
                    if getattr(caption_asr, "engine", "whisper") != "whisper":
                        words = None
                    if words is None:
                        captions, caption_info = build_captions(voice_final, captions, duration)
                    else:
                        captions, caption_info = build_captions(
                            voice_final, captions, duration,
                            lambda _: words(voice_final, settings.target_language or None))
                    stages.append("caption_asr")
                write_srt(captions, work / "translated.srt")
                subtitles = work / "translated.ass"
                write_ass(captions, subtitles, *dimensions, language=settings.target_language)
                stages.append("caption")
            if settings.hook_enabled:
                from .hooks import build_hook, append_hook_ass, write_hook_ass
                hook_info = build_hook(segments, settings.hook_duration, settings.hook_theme, settings.hook_seed)
                (work / "hook.json").write_text(json.dumps(hook_info, ensure_ascii=False, indent=2), encoding="utf-8")
                if hook_info is not None:
                    if subtitles is None:
                        subtitles = work / "translated.ass"
                        write_hook_ass(hook_info, subtitles, *dimensions, language=settings.target_language)
                    else:
                        append_hook_ass(subtitles, hook_info, *dimensions, language=settings.target_language)
                    stages.append("hook")
            self._stage(job, "mix", 0.75); check_cancelled()
            mix = dubbed
            if settings.keep_original_audio and has_voice and settings.background_volume > 0:
                background = work / "background_slow.wav"
                background_source = video
                if settings.separate_background:
                    self._stage(job, "separate_background", 0.80); check_cancelled()
                    separator_dir = self._path(root, settings.separator_model_dir)
                    if not separator_dir:
                        raise FatalProviderError("Cần cấu hình thư mục model tách âm nền")
                    separator_audio = work / "separation_source.wav"
                    media.run([settings.ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(separator_audio)])
                    background_source = separate_background(separator_audio, work / "separation", Path(separator_dir), self._path(root, settings.separator_python))
                    stages.append("separate_background")
                media.prepare_background(background_source, background, settings.slow_speed * alignment["video_speed"], alignment["target_duration_seconds"])
                mix = work / "mix_slow.wav"
                media.mix_background(background, dubbed, mix, settings.background_volume)
                stages.append("mix_background")
            final_audio = work / "final_mix.wav"
            media.prepare_audio(mix, final_audio, settings.final_speed, alignment["final_duration_seconds"])
            stages.append("prepare_final_audio")
            from .subtitle_region import resolve_region
            self._stage(job, "subtitle_region", 0.86); check_cancelled()
            blur_region, region_info = resolve_region(settings, media, video, source, work)
            if settings.old_subtitle_mode != "off":
                stages.append("subtitle_region")
            output = output_dir / f"{video.stem}_{job.id}_translated.mp4"
            pending = work / f"{video.stem}_translated.pending.mp4"
            self._stage(job, "render", 0.92); check_cancelled()
            media.render(video, final_audio, subtitles, pending, alignment["picture_speed"], alignment["final_duration_seconds"],
                         layout=settings.output_layout, encoder=settings.video_encoder, bitrate_mbps=settings.video_bitrate_mbps,
                         **({"blur_region": blur_region} if blur_region is not None else {}))
            check_cancelled()
            media.validate_output(pending, alignment["final_duration_seconds"])
            stages.extend(["render", "validate_output"])
            check_cancelled()
            timing = {"version": 3, "policy": "source-windows-v1", **alignment, "slow_speed": settings.slow_speed, "final_speed": settings.final_speed, "segments": len(segments), "subtitle_audio": final_audio.name}
            (work / "pipeline_timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
            review_metadata = dict(job.metadata) if isinstance(job.metadata, dict) else {}
            review_metadata.update({"stages": stages, "reused_stages": reused_stages, "segments": len(segments), "timing": timing, "captions": caption_info,
                                   "hook": hook_info, "encoding": getattr(media, "last_encoding", {}),
                                   "output_dimensions": list(dimensions), "subtitle_region": region_info})
            job.metadata = review_metadata
            check_cancelled()
            os.replace(pending, output)
            job.output = str(output); job.status = "done"
            encoding = getattr(media, "last_encoding", {}) or {}
            self.notify(job, f"Hoàn tất · {len(segments)} cue · {alignment['final_duration_seconds']:.1f}s"
                             + (f" · {encoding.get('selected')}/{encoding.get('codec')}" if encoding else ""))
            self._stage(job, "done", 1.0)
        except QueueError:
            raise
        except Cancelled:
            job.status, job.error, job.output = "cancelled", "Đã hủy", ""
        except FatalProviderError as exc:
            job.status, job.error, job.output = "failed", str(exc), ""
            self.queue.update(job); self.notify(job)
            raise
        except Exception as exc:
            job.status, job.error, job.output = "failed", str(exc), ""
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
        self.queue.update(job); self.notify(job)

    def _preserve_source(self, job, video, output_dir, work, reason):
        output = output_dir / f"{video.stem}_{job.id}_preserved{video.suffix}"
        pending = work / (output.name + ".part")
        try:
            check_cancelled()
            with video.open("rb") as source, pending.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    check_cancelled()
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            check_cancelled()
            os.replace(pending, output)
            job.metadata = {"reason": reason, "segments": 0, "preserved_source": True}
            job.output, job.status, job.stage, job.progress = str(output), "done", reason, 1.0
            self.queue.update(job); self.notify(job)
        finally:
            pending.unlink(missing_ok=True)



class App(tk.Tk):
    def __init__(self, root: Path):
        super().__init__()
        self.title("SubShort · Video Studio")
        self.geometry("1280x800")
        self.minsize(1100, 680)
        apply_theme(self)
        try:
            self.settings = Settings.load(root)
        except CredentialError:
            self.destroy()
            raise
        self.queue = JobQueue(root / "queue_history.json")
        self.events = messages.SimpleQueue()
        self.log_events = messages.SimpleQueue()
        self.log_lines = deque(maxlen=1000)
        self.log_last = ""
        self.log_visible = tk.BooleanVar(master=self, value=False)
        self.batch = Batch(self.settings, self.queue, Pipeline, self.notify)
        from .upload import UploadManager
        self.upload = UploadManager(self.settings, self.queue, self.notify)
        self.closing = False
        sidebar = ttk.Frame(self, width=220, padding=(20, 28))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="SubShort", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="VIDEO TRANSLATION STUDIO", style="Muted.TLabel",
                  font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 36))
        ttk.Label(sidebar, text="KHÔNG GIAN LÀM VIỆC", style="Muted.TLabel",
                  font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 12))
        ttk.Label(sidebar, text="▸  Hàng đợi video", style="Heading.TLabel").pack(anchor="w", pady=(0, 20))
        for text, command in (("Giọng nói", self.voices), ("Duyệt speaker", self.review_speaker),
                              ("API & dịch vụ", self.api), ("Cấu hình pipeline", self.configure_pipeline)):
            ttk.Button(sidebar, text=text, command=command).pack(fill="x", pady=4)
        ttk.Label(sidebar, text="ASR  →  Dịch  →  Giọng đọc\nPhụ đề & xuất video",
                  style="Muted.TLabel", justify="left").pack(side="bottom", anchor="w", pady=12)
        main = ttk.Frame(self, padding=(24, 28, 28, 20))
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x")
        ttk.Label(header, text="Hàng đợi video", style="Title.TLabel").pack(side="left")
        self.add_button = ttk.Button(header, text="＋ Thêm video", command=self.add, style="Primary.TButton")
        self.add_button.pack(side="right")
        ttk.Button(header, text="Thêm URL / ID", command=self.add_urls).pack(side="right", padx=8)
        ttk.Label(main, text="Quản lý nguồn, theo dõi xử lý và mở thành phẩm tại một nơi.",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 22))
        summary = ttk.Frame(main, style="Card.TFrame", padding=18)
        summary.pack(fill="x", pady=(0, 20))
        self.queue_summary = tk.StringVar(master=self)
        ttk.Label(summary, textvariable=self.queue_summary, style="Card.TLabel",
                  font=("Segoe UI Semibold", 12)).pack(anchor="w")
        toolbar = ttk.Frame(main)
        toolbar.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(toolbar, text="▶  Chạy hàng đợi", command=self.run, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(toolbar, text="Dừng xử lý / tải lên", command=self.cancel_workers, style="Danger.TButton")
        self.cancel_button.pack(side="left", padx=8)
        self.retry_button = ttk.Button(toolbar, text="Thử lại đã chọn", command=self.retry)
        self.retry_button.pack(side="right")
        self.remove_button = ttk.Button(toolbar, text="Xóa đã chọn", command=self.remove_jobs, style="Danger.TButton")
        self.remove_button.pack(side="right", padx=8)
        table = ttk.Frame(main, style="Card.TFrame")
        self.jobs = ttk.Treeview(table, columns=("source", "status", "progress", "output", "upload"), show="headings")
        for col, text, width in (("source", "VIDEO / NGUỒN", 240), ("status", "TRẠNG THÁI", 150),
                                 ("progress", "TIẾN ĐỘ", 85), ("output", "KẾT QUẢ / CHI TIẾT", 240),
                                 ("upload", "DRIVE", 110)):
            self.jobs.heading(col, text=text)
            self.jobs.column(col, width=width, minwidth=70, stretch=col in ("source", "output"))
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.jobs.yview)
        scroll.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.jobs.xview)
        horizontal.pack(side="bottom", fill="x")
        self.jobs.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.jobs.pack(fill="both", expand=True)
        self.jobs.bind("<Double-1>", lambda event: self.open_output())
        self.table = table
        footer = ttk.Frame(main)
        footer.pack(side="bottom", fill="x", pady=(14, 12))
        self.open_button = ttk.Button(footer, text="Mở thư mục kết quả", command=self.open_output)
        self.open_button.pack(side="left")
        self.upload_button = ttk.Button(footer, text="Tải lên Google Drive", command=self.drive)
        self.upload_button.pack(side="left", padx=8)
        self.log_toggle = ttk.Checkbutton(footer, text="Hiện log xử lý", variable=self.log_visible,
                                          command=self.toggle_log)
        self.log_toggle.pack(side="left", padx=(16, 0))
        ttk.Label(footer, text="Nhấp đúp video để mở kết quả", style="Muted.TLabel").pack(side="right")
        self.log_frame = ttk.Frame(main, style="Card.TFrame", padding=12)
        self.log_text = tk.Text(self.log_frame, height=9, state="disabled", background="#0b0f18",
                                foreground=FG, insertbackground=FG, relief="flat",
                                font=("Consolas", 9), padx=8, pady=6)
        log_scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.status_text = tk.StringVar(master=self, value=self.queue.load_error or "Sẵn sàng · Thêm video hoặc URL để bắt đầu")
        ttk.Label(main, textvariable=self.status_text, wraplength=820, style="Muted.TLabel").pack(side="bottom", fill="x")
        table.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)
        self.refresh()

    def refresh(self):
        selected = self.jobs.selection()
        snapshot = self.queue.snapshot()
        self.queue_summary.set(f"{len(snapshot)} video    ·    "
                               f"{sum(j.status == 'running' for j in snapshot)} đang xử lý    ·    "
                               f"{sum(j.status == 'done' for j in snapshot)} hoàn tất    ·    "
                               f"{sum(j.status == 'failed' for j in snapshot)} lỗi")
        self.jobs.delete(*self.jobs.get_children())
        for j in snapshot:
            upload = j.metadata.get("upload", {})
            if not isinstance(upload, dict):
                upload = {}
            state = upload.get("status", "")
            if state:
                state = f"{state} {float(upload.get('progress', 0.0)):.0%}"
                if upload.get("webViewLink"):
                    state += f" {upload['webViewLink']}"
            detail = j.output or j.error
            self.jobs.insert("", "end", iid=j.id,
                             values=(j.source, f"{j.status}: {j.stage}", f"{j.progress:.0%}", detail, state))
        self.jobs.selection_set([item for item in selected if self.jobs.exists(item)])
        busy = self.batch.running or self.upload.running or self.closing
        readonly = bool(self.queue.load_error)
        for button in (self.add_button, self.start_button, self.retry_button, self.remove_button):
            button.configure(state="disabled" if busy or readonly else "normal")
        self.upload_button.configure(state="disabled" if self.closing or readonly else "normal")
        self.cancel_button.configure(state="normal" if self.batch.running or self.upload.running else "disabled")
        self.open_button.configure(state="disabled" if readonly else "normal")

    def open_output(self):
        selected = self.jobs.selection()
        if not selected:
            return
        job = next((item for item in self.queue.snapshot() if item.id == selected[0]), None)
        target = Path(job.output) if job and job.output else None
        if target is None or not target.exists():
            messagebox.showinfo("Chưa có kết quả", "Job chưa có output hoặc file đã bị xóa.", parent=self)
            return
        try:
            if target.is_dir():
                os.startfile(target)
            else:
                os.startfile(target.parent)
        except OSError as exc:
            messagebox.showerror("Không mở được thư mục", str(exc), parent=self)

    def configure_pipeline(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self, self.settings, on_saved=self.refresh)

    def drive(self):
        from .drive_dialog import DriveDialog
        selected = self.jobs.selection()
        DriveDialog(self, self.upload, self.settings.root, selected[0] if selected else None)

    def voices(self):
        from .voices_dialog import VoicesDialog
        VoicesDialog(self, self.settings)

    def add_urls(self):
        text = simpledialog.askstring("Thêm nguồn", "URL HTTPS hoặc yt:ID / douyin:ID / drive:ID; mỗi dòng một nguồn", parent=self)
        if not text:
            return
        from .sources import classify_source
        from .providers import ProviderError
        try:
            self.settings.validate()
            sources = [line.strip() for line in text.splitlines() if line.strip()]
            for source in sources:
                if classify_source(source) == "local":
                    raise ValueError("Dùng Thêm video cho đường dẫn local")
            for source in sources:
                self.queue.add(Job(uuid4().hex[:12], source, self.settings.target_language,
                                   mode=self.settings.mode, config=self.settings.snapshot()))
        except (OSError, ValueError, QueueError, ProviderError) as exc:
            messagebox.showerror("Không thêm được nguồn", str(exc))
        self.refresh()
    def review_speaker(self):
        selected = self.jobs.selection()
        if not selected:
            return
        job = next((item for item in self.queue.snapshot() if item.id == selected[0]), None)
        if job is None:
            return
        data = job.metadata if isinstance(job.metadata, dict) else {}
        turns = data.get("diarization", [])
        if self.batch.running:
            return
        transcript_rows = data.get("review_transcript", [])
        if not isinstance(transcript_rows, list) or not transcript_rows:
            messagebox.showinfo("Chưa có transcript", "Chạy job trước để tạo transcript cần duyệt.", parent=self)
            return
        dialog = tk.Toplevel(self); dialog.title("Duyệt transcript, bản dịch và speaker"); dialog.geometry("800x720")
        ttk.Label(dialog, text="Sửa JSON lượt nói (start/end/speaker_id/status=resolved), rồi nhập voice map JSON.").pack(anchor="w", padx=8, pady=6)
        turns_text = tk.Text(dialog, height=5, width=90); turns_text.pack(fill="both", expand=True, padx=8)
        turns_text.insert("1.0", json.dumps(turns, ensure_ascii=False, indent=2))
        ttk.Label(dialog, text="Transcript JSON: giữ start/end/source_id; sửa text để dịch lại.").pack(anchor="w", padx=8, pady=4)
        transcript_text = tk.Text(dialog, height=5, width=90); transcript_text.pack(fill="both", expand=True, padx=8)
        transcript_text.insert("1.0", json.dumps(data.get("review_transcript", []), ensure_ascii=False, indent=2))
        ttk.Label(dialog, text="Bản dịch đầy đủ JSON: giữ start/end/source_id/speaker_id; sửa text để TTS chạy lại.").pack(anchor="w", padx=8, pady=4)
        translation_text = tk.Text(dialog, height=5, width=90); translation_text.pack(fill="both", expand=True, padx=8)
        translation_text.insert("1.0", json.dumps(data.get("review_translation", []), ensure_ascii=False, indent=2))
        sample_frame = ttk.Frame(dialog); sample_frame.pack(fill="x", padx=8, pady=4)
        for speaker, sample in data.get("speaker_samples", {}).items():
            ttk.Button(sample_frame, text=f"Nghe {speaker}", command=lambda path=sample: os.startfile(path) if Path(path).exists() else None).pack(side="left", padx=2)
        ttk.Label(dialog, text="Voice map JSON, ví dụ {\"speaker-1\": \"<voice-id>\"}:").pack(anchor="w", padx=8, pady=4)
        voices_text = tk.Text(dialog, height=3, width=90); voices_text.pack(fill="x", padx=8)
        voices_text.insert("1.0", json.dumps(data.get("speaker_review", {}).get("voice_map", {}), ensure_ascii=False))
        def save_review():
            try:
                edited_turns = validate_turns(json.loads(turns_text.get("1.0", "end")), None)
                voice_map = json.loads(voices_text.get("1.0", "end"))
                transcript_rows = json.loads(transcript_text.get("1.0", "end"))
                original_rows = data.get("review_transcript", [])
                if not isinstance(transcript_rows, list) or len(transcript_rows) != len(original_rows):
                    raise ValueError("Transcript phải giữ nguyên số cue")
                edited_text = {}
                for original, row in zip(original_rows, transcript_rows):
                    if any(row.get(key) != original.get(key) for key in ("start", "end", "source_id")) or not isinstance(row.get("text"), str) or not row["text"].strip():
                        raise ValueError("Chỉ được sửa text; start/end/source_id phải giữ nguyên")
                    edited_text[str(original["source_id"])] = row["text"]
                translation_rows = json.loads(translation_text.get("1.0", "end"))
                original_translations = data.get("review_translation", [])
                if not isinstance(translation_rows, list) or len(translation_rows) != len(original_translations):
                    raise ValueError("Bản dịch phải giữ nguyên số cue")
                edited_translation = {}
                for original, row in zip(original_translations, translation_rows):
                    if any(row.get(key) != original.get(key) for key in ("start", "end", "source_id", "speaker_id")) or not isinstance(row.get("text"), str) or not row["text"].strip():
                        raise ValueError("Chỉ được sửa text bản dịch; thời điểm/ID phải giữ nguyên")
                    if row["text"] != original["text"]:
                        edited_translation[str(original.get("source_id", ""))] = row["text"]
                if any(turn.status != "resolved" for turn in edited_turns) or not isinstance(voice_map, dict):
                    raise ValueError("Mọi lượt nói phải resolved và voice map phải là object")
                review_segments = [Segment(float(row["start"]), float(row["end"]), edited_text[str(row["source_id"])], str(row["source_id"]), str(row.get("speaker_id", ""))) for row in original_rows]
                review_key = review_fingerprint(data.get("review_source_hash", ""), review_segments, edited_turns, [row.text for row in review_segments])
                config = dict(job.config)
                if edited_text != config.get("review_transcript", {}):
                    config["review_translation"] = edited_translation
                else:
                    config["review_translation"] = {**config.get("review_translation", {}), **edited_translation}
                config["review_transcript"] = edited_text
                config["speaker_review"] = {"approved": True, "turns": [turn.__dict__ for turn in edited_turns], "voice_map": voice_map, "fingerprint": review_key}
                job.config = config; job.status, job.error = "queued", ""; self.queue.update(job); dialog.destroy(); self.refresh()
            except (ValueError, TypeError, KeyError, AttributeError, QueueError, ProviderError) as exc:
                messagebox.showerror("Review không hợp lệ", str(exc), parent=dialog)
        ttk.Button(dialog, text="Lưu và chạy lại", command=save_review).pack(pady=8)

    def add(self):
        try:
            self.settings.validate()
        except ValueError as exc:
            messagebox.showerror("Cấu hình không hợp lệ", str(exc)); return
        try:
            for source in filedialog.askopenfilenames(filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi"), ("All", "*.*")]):
                self.queue.add(Job(uuid4().hex[:12], source, self.settings.target_language,
                                   mode=self.settings.mode, config=self.settings.snapshot()))
        except (OSError, ValueError, QueueError) as exc:
            messagebox.showerror("Không lưu được queue", str(exc))
        self.refresh()

    def run(self):
        try:
            self.batch.start()
        except (OSError, ValueError, QueueError) as exc:
            messagebox.showerror("Không chạy được queue", str(exc))
        self.refresh()

    def notify(self, job, message=""):
        self.events.put(message)
        if job is not None:
            detail = message or " · ".join(part for part in (job.stage, getattr(job, "error", "")) if part) or job.status
            self.log_events.put(f"{time.strftime('%H:%M:%S')}  {job.id[:6]}  {job.status:<9} {detail}")
        elif message:
            self.log_events.put(f"{time.strftime('%H:%M:%S')}  {message}")

    def retry(self):
        if self.batch.running:
            return
        try:
            for job_id in self.jobs.selection():
                self.queue.retry(job_id)
        except (OSError, ValueError, QueueError) as exc:
            messagebox.showerror("Không thử lại được", str(exc))
        self.refresh()

    def remove_jobs(self):
        if self.batch.running or self.upload.running or self.closing or self.queue.load_error:
            return
        selected = self.jobs.selection()
        if not selected:
            return
        if not messagebox.askyesno("Xóa job", f"Xóa {len(selected)} job đã chọn khỏi hàng đợi?\n"
                                   "Video nguồn và thư mục kết quả vẫn được giữ nguyên.", parent=self):
            return
        try:
            self.queue.remove(selected)
        except (OSError, ValueError, QueueError) as exc:
            messagebox.showerror("Không xóa được job", str(exc), parent=self)
        self.refresh()

    def poll(self):
        lines = []
        while not self.log_events.empty():
            lines.append(self.log_events.get())
        if lines:
            self.log_lines.extend(lines)
            if self.log_visible.get():
                self.show_log_lines(lines)
        while not self.events.empty():
            message = self.events.get()
            if message:
                self.status_text.set(message)
        error = self.batch.error or self.upload.error or self.queue.load_error
        if error:
            self.status_text.set(error)
        self.refresh()
        if self.closing and not self.batch.running and not self.upload.running:
            self.upload.join()
            # quit() ends mainloop even when another Tk root keeps the process's window count nonzero
            self.quit()
            self.destroy()
            return
        self.after(100, self.poll)

    def show_log_lines(self, lines):
        fresh = []
        for line in lines:
            if line != self.log_last:
                fresh.append(line)
                self.log_last = line
        if not fresh:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n".join(fresh) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def toggle_log(self):
        if not self.log_visible.get():
            self.log_frame.pack_forget()
            return
        self.log_frame.pack(side="bottom", fill="x", pady=(0, 12), before=self.table)
        self.log_last = ""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.show_log_lines(list(self.log_lines))

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.batch.cancel()
        self.upload.cancel()
        self.status_text.set("Đang hủy và chờ worker dừng…")
        self.refresh()

    def cancel_workers(self):
        self.batch.cancel()
        self.upload.cancel()

    def api(self):
        CredentialDialog(self, self.settings)


class CredentialDialog(simpledialog.Dialog):
    def __init__(self, parent, settings):
        self.settings = settings
        super().__init__(parent, "Cài đặt API và dịch văn bản")

    def body(self, master):
        translation = ttk.LabelFrame(master, text="1. Dịch vụ dịch văn bản", padding=12)
        translation.pack(fill="x", padx=8, pady=8)
        self.api_variables = {}
        for row, (name, label) in enumerate((("translation_provider", "Dịch vụ"),
                                            ("translation_base_url", "Base URL"),
                                            ("translation_model", "Model ID"))):
            ttk.Label(translation, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            variable = tk.StringVar(master=self, value=getattr(self.settings, name))
            if name == "translation_provider":
                widget = ttk.Combobox(translation, textvariable=variable, state="readonly", width=56,
                                      values=("openai", "gemini", "openai_compatible"))
            else:
                widget = ttk.Entry(translation, textvariable=variable, width=59)
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            self.api_variables[name] = variable
        ttk.Label(translation, wraplength=580, justify="left", text=(
            "OpenAI-compatible: nhập Base URL do nhà cung cấp cấp, ví dụ https://api.example.com/v1 "
            "hoặc http://127.0.0.1:1234/v1 cho server local. Không thêm /chat/completions.\n"
            "Model ID: chép đúng tên model từ nhà cung cấp; bắt buộc với OpenAI-compatible. "
            "OpenAI/Gemini có thể để model trống dùng mặc định; Base URL chỉ dùng cho OpenAI-compatible.\n"
            "HTTP không mã hóa key/nội dung: chỉ dùng với server local hoặc mạng tin cậy."
        )).grid(row=3, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(translation, text="Chạy dịch sẽ gửi văn bản ASR tới dịch vụ đã chọn.").grid(
            row=4, column=0, columnspan=2, sticky="w")
        keys = ttk.LabelFrame(master, text="2. API key — nhập vào đúng dịch vụ", padding=12)
        keys.pack(fill="x", padx=8, pady=8)
        self.fields = {}
        for row, (field, label) in enumerate((("openai_api_key", "OpenAI API key"), ("gemini_api_key", "Gemini API key"), ("openai_compatible_api_key", "OpenAI-compatible API key"))):
            saved = bool(getattr(self.settings, field))
            ttk.Label(keys, text=f"{label}\n{'Đã lưu' if saved else 'Chưa có'}").grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(keys, show="*", width=44)
            entry.grid(row=row, column=1, padx=8, pady=6)
            delete = tk.BooleanVar(master=self, value=False)
            ttk.Checkbutton(keys, text="Xóa key", variable=delete).grid(row=row, column=2)
            self.fields[field] = (entry, delete)
        ttk.Label(master, wraplength=620, justify="left", text=(
            "Key trống: giữ key đã lưu. Nhập key mới để thay, hoặc tích Xóa key. "
            "Server local không cần xác thực: nhập local vào key OpenAI-compatible.\n"
            "Key được mã hóa bằng Windows DPAPI, không đưa vào job. "
            "Cấu hình dịch mới chỉ áp dụng cho job thêm sau khi lưu."
        )).pack(fill="x", padx=20, pady=8)
        return self.fields["openai_api_key"][0]

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="Lưu cấu hình", command=self.ok).pack(side="left", padx=5, pady=8)
        ttk.Button(box, text="Hủy", command=self.cancel).pack(side="left", padx=5, pady=8)
        box.pack()
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

    def validate(self):
        previous = {field: getattr(self.settings, field) for field in self.fields}
        previous.update({name: getattr(self.settings, name) for name in self.api_variables})
        for field, (entry, delete) in self.fields.items():
            value = "" if delete.get() else entry.get().strip() or previous[field]
            setattr(self.settings, field, value)
        for name, variable in self.api_variables.items():
            value = variable.get()
            setattr(self.settings, name, value.strip() if isinstance(value, str) else value)
        try:
            if self.settings.translation_provider == "openai_compatible":
                from .providers import OpenAICompatibleTranslator
                OpenAICompatibleTranslator(self.settings.openai_compatible_api_key,
                                           self.settings.translation_model, self.settings.translation_base_url)
            self.settings.save()
        except ValueError as exc:
            for field, value in previous.items():
                setattr(self.settings, field, value)
            messagebox.showerror("Cấu hình API không hợp lệ", str(exc), parent=self)
            return False
        except (CredentialError, OSError):
            for field, value in previous.items():
                setattr(self.settings, field, value)
            messagebox.showerror("Không hoàn tất lưu credential", "Không hoàn tất lưu cấu hình an toàn. Bản credential được bảo vệ có thể đã được cập nhật; hãy mở lại ứng dụng để đọc trạng thái đã lưu trước khi thử lại.", parent=self)
            return False
        return True


def launch(root: Path):
    try:
        App(root).mainloop()
    except CredentialError:
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("Không đọc được credential", "Không đọc hoặc migrate được credential an toàn. Dữ liệu hiện có được giữ nguyên; kiểm tra tài khoản Windows và quyền truy cập cấu hình.", parent=window)
        window.destroy()
