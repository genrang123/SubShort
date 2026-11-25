"""Run the video translation pipeline independently of the desktop UI."""
import json
import os
import time
from pathlib import Path

from .asr import ASRSession
from .asr_worker import ExternalASRSession
from .diarization import (DiarizationSession, align_segments, resolve_overlap_cues, review_fingerprint,
                          stable_labels, validate_turns)
from .execution import Execution, Cancelled, check_cancelled
from .media import MediaAdapter
from .paths import artifact_directory
from .providers import (
    EdgeTTS, FatalProviderError, GeminiTranslator, OpenAICompatibleTranslator,
    OpenAITranslator, Segment, build_timeline, timeline_alignment,
    validate_segments, write_srt,
)
from .queue import QueueError
from .separation import separate_background
from .tts import TtsSession
from .voices import VoiceRegistry



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
        model, aligner, vad = settings.asr_assets()
        key = (engine, str(model.resolve()), settings.asr_device,
               str(aligner.resolve()) if aligner else None, str(vad.resolve()) if vad else None,
               settings.firered_type, settings.asr_worker_python())
        if key not in self._asr_sessions:
            args = dict(forced_aligner_path=key[3], vad_model_path=key[4], firered_type=key[5], diagnostic=diagnostic)
            if key[6] is None:
                self._asr_sessions[key] = ASRSession(*key[:3], **args)
            else:
                self._asr_sessions[key] = ExternalASRSession(key[6], *key[:3], **args)
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
                    return str(configured) if isinstance(configured, Path) else configured
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

            model, aligner, vad = settings.asr_assets()
            asr_dependencies = {
                "language": settings.source_language, "provider": settings.asr_provider,
                "model": model_fingerprint(model), "device": settings.asr_device,
                "aligner": model_fingerprint(aligner), "vad": model_fingerprint(vad),
                "firered_type": settings.firered_type,
            }
            python = settings.asr_worker_python()
            if python is not None:
                asr_dependencies["worker_python"] = str(python)
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
                    def resplit(segment):
                        children = []
                        for index, turn in enumerate(reviewed):
                            start, end = max(segment.start, turn.start), min(segment.end, turn.end)
                            if start >= end:
                                continue
                            window_audio = samples_dir / f"{segment.source_id}-{index}.wav"
                            media.extract_audio_window(video, window_audio, start, end)
                            rows = validate_segments(self._asr(settings, diagnostic).transcribe(
                                window_audio, settings.source_language or None), end - start)
                            if not rows:
                                raise FatalProviderError(f"Cue {segment.source_id}: không có lời trong lượt {turn.speaker_id}; sửa lượt nói rồi chạy lại")
                            for child in rows:
                                children.append(Segment(start + child.start, start + child.end, child.text,
                                                        f"{segment.source_id}.{len(children)}", turn.speaker_id))
                        return sorted(children, key=lambda child: (child.start, child.end))
                    segments = align_segments(segments, reviewed, resplit=resplit)
                except Cancelled:
                    raise
                except Exception as exc:
                    raise FatalProviderError(str(exc)) from None
                job.metadata["diarization"] = [turn.__dict__ for turn in reviewed]
                job.metadata["speaker_review"] = {"turns": job.metadata["diarization"], "voice_map": voice_map,
                                                  "fingerprint": expected_review_fingerprint}
                mixed = [cue for cue in segments if "." in cue.source_id and any(
                    turn.speaker_id != cue.speaker_id and turn.start < cue.end and cue.start < turn.end
                    for turn in reviewed)]
                if mixed:
                    overlap_key = review_fingerprint(expected_review_fingerprint, mixed, reviewed, [])
                    approval = review.get("overlap")
                    job.metadata["overlap_review"] = {"fingerprint": overlap_key,
                                                      "cues": [dict(cue.__dict__, status="needs_review") for cue in mixed]}
                    if not isinstance(approval, dict) or approval.get("fingerprint") != overlap_key:
                        raise FatalProviderError("Cần duyệt cue chồng tiếng trước khi dịch")
                    resolved = {cue.source_id: cue for cue in resolve_overlap_cues(mixed, reviewed, approval.get("cues"))}
                    mixed_ids = {cue.source_id for cue in mixed}
                    segments = [resolved.get(cue.source_id, cue) for cue in segments
                                if cue.source_id not in mixed_ids or cue.source_id in resolved]
                    if not segments:
                        raise FatalProviderError("Cần giữ ít nhất một cue để dịch")
                source_segments = segments
                speakers = {segment.speaker_id for segment in segments}
                if speakers - set(voice_map):
                    raise FatalProviderError("Ánh xạ speaker→voice chưa đầy đủ")
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
            translated = cache.load_segments("translate", translation_dependencies, allow_nested=settings.multi_speaker) if cache else None
            if translated is None:
                translated = translator.translate(segments, settings.target_language)
                if cache:
                    cache.save_segments("translate", translation_dependencies, translated, allow_nested=settings.multi_speaker)
                stages.append("translate")
                self.notify(job, f"Dịch xong: {len(translated)} cue sang {settings.target_language}")
            else:
                reused_stages.append("translate")
                self.notify(job, f"Dịch: dùng lại cache ({len(translated)} cue)")
            translation_edits = job_config.get("review_translation", {})
            saved_keys = job_config.get("review_translation_keys", {})
            source_hash = cache.source_hash if cache else str(video.resolve())
            keys = {cue.source_id: review_fingerprint(source_hash, [cue], [], settings.target_language)
                    for cue in source_segments}
            segments = []
            for item, cue in zip(translated, source_segments, strict=True):
                saved_key = saved_keys.get(cue.source_id)
                text = (translation_edits.get(cue.source_id, item.text)
                        if saved_key == keys[cue.source_id] or (saved_key is None and "." not in cue.source_id) else item.text)
                segments.append(Segment(item.start, item.end, text, cue.source_id, cue.speaker_id))
            job.metadata["review_translation_keys"] = keys
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
                    speaker_id = segment.speaker_id
                    selected_voice = speaker_voices.get(speaker_id, settings.tts_voice) if settings.multi_speaker else settings.tts_voice
                    clip_dir = work / "clips"
                    clip_dir.mkdir(parents=True, exist_ok=True)
                    dependencies = {**tts_dependencies, "source_id": segment.source_id, "speaker_id": speaker_id, "text": segment.text}
                    stage = f"tts_clip_{index}"
                    raw = cache.load_file(stage, dependencies) if cache else None
                    if raw is None:
                        raw = clip_dir / (f"{index:05}.mp3" if settings.tts_provider == "edge" else f"{index:05}.raw.wav")
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
                        reused_stages.append(stage)
                    clip = clip_dir / f"{index:05}.normalized.wav"
                    media.normalize_clip(raw, clip, speed=settings.tts_speed)
                    clips.append(clip)
                if tts_executed:
                    stages.append("tts")
                    self.notify(job, f"TTS xong: {len(clips)} clip · {len(segments)} cue · {settings.tts_provider}")
                else:
                    reused_stages.append("tts")
                    self.notify(job, f"TTS: dùng lại cache ({len(clips)} clip)")
                timeline = build_timeline(segments, clips, settings.slow_speed, source["duration"], multi_speaker=settings.multi_speaker)
                alignment = timeline_alignment(timeline, source["duration"], settings.slow_speed, settings.final_speed, allow_overlap=settings.multi_speaker)
                (work / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
                dubbed = work / "dubbed_slow.wav"
                mixer = media.mix_timeline_audio if settings.multi_speaker else media.timeline_audio
                mixer(clips, [row["actual_start_seconds"] for row in timeline], dubbed, alignment["target_duration_seconds"])
                captions = [Segment(row["actual_start_seconds"] / settings.final_speed, row["actual_end_seconds"] / settings.final_speed, row["text"], row.get("source_id", ""), row.get("speaker_id", "")) for row in timeline]
                captions.sort(key=lambda cue: cue.start)
            else:
                alignment = timeline_alignment([], source["duration"], settings.slow_speed, settings.final_speed)
                dubbed = work / "original_slow.wav"
                media.prepare_audio(video, dubbed, settings.slow_speed, alignment["target_duration_seconds"])
                captions = [Segment(s.start / (settings.slow_speed * settings.final_speed),
                                    s.end / (settings.slow_speed * settings.final_speed),
                                    s.text, s.source_id, s.speaker_id) for s in segments]
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
            timing = {"version": 4, "policy": "unbounded-dubbing-v1" if has_voice else "source-windows-v1", **alignment, "slow_speed": settings.slow_speed, "final_speed": settings.final_speed, "segments": len(segments), "subtitle_audio": final_audio.name}
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
