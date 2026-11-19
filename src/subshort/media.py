from pathlib import Path
import shutil
import json
import math
import tempfile
import wave

from .execution import check_cancelled, run_process


class MediaError(RuntimeError):
    pass


class MediaAdapter:
    def __init__(self, ffmpeg="ffmpeg", ffprobe="ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.last_encoding = {}

    def check(self):
        if not shutil.which(self.ffmpeg) and not Path(self.ffmpeg).exists():
            raise MediaError(f"Không tìm thấy FFmpeg: {self.ffmpeg}")


    def probe(self, video: Path) -> dict:
        result = run_process([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)], capture_output=True, text=True)
        try:
            if result.returncode:
                raise ValueError
            data = json.loads(result.stdout)
            raw_duration = data["format"]["duration"]
            if isinstance(raw_duration, bool):
                raise ValueError
            duration = float(raw_duration)
            streams = data["streams"]
            if not math.isfinite(duration) or duration <= 0 or not isinstance(streams, list):
                raise ValueError
            if not any(stream.get("codec_type") == "video" for stream in streams):
                raise ValueError
            picture = next(stream for stream in streams if stream.get("codec_type") == "video")
            width, height = picture["width"], picture["height"]
            self.output_dimensions(width, height)
            return {"duration": duration, "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
                    "width": width, "height": height}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise MediaError("Không probe được duration/video stream hợp lệ của nguồn") from None

    def extract_audio(self, video: Path, audio: Path):
        self.check()
        audio.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    def extract_audio_window(self, video: Path, audio: Path, start: float, end: float):
        self.check()
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start < 0 or end <= start:
            raise MediaError("Khoảng audio không hợp lệ")
        audio.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-ss", str(start), "-t", str(end - start), "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])


    def change_speed(self, source: Path, output: Path, speed: float):
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(source), "-filter_complex", f"[0:v:0]setpts=PTS/{speed}[v];[0:a:0]atempo={speed}[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "pcm_s16le", str(output)])

    def mix_background(self, original: Path, voice: Path, output: Path, volume: float):
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(original), "-i", str(voice), "-filter_complex", f"[0:a]volume={volume}[bg];[bg][1:a]amix=inputs=2:duration=longest[a]", "-map", "[a]", "-ar", "48000", "-c:a", "pcm_s16le", str(output)])

    def change_audio_speed(self, source: Path, output: Path, speed: float):
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(source), "-af", f"atempo={speed}", "-ar", "48000", "-c:a", "pcm_s16le", str(output)])

    @staticmethod
    def _positive(value, name):
        if isinstance(value, bool):
            raise MediaError(f"{name} phải là số hữu hạn dương")
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            raise MediaError(f"{name} phải là số hữu hạn dương") from None
        if not math.isfinite(value) or value <= 0:
            raise MediaError(f"{name} phải là số hữu hạn dương")
        return value

    def normalize_clip(self, source: Path, output: Path):
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output)])

    @staticmethod
    def clip_frames(path):
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 24000, "NONE"):
                raise MediaError("Clip phải là mono PCM16 24000 Hz")
            frames = audio.getnframes()
            if frames <= 0:
                raise MediaError("Clip PCM rỗng")
            remaining = frames
            while remaining:
                check_cancelled()
                data = audio.readframes(min(remaining, 65536))
                if not data or len(data) % 2:
                    raise MediaError("Clip PCM bị thiếu dữ liệu")
                remaining -= len(data) // 2
            return frames

    def fit_clip(self, source, output, window_frames):
        if not isinstance(window_frames, int) or window_frames <= 0:
            raise MediaError("Cửa sổ alignment không hợp lệ")
        frames = self.clip_frames(source)
        tempo = max(1.0, frames / window_frames)
        if tempo > 1.4:
            return None
        if tempo == 1.0:
            shutil.copyfile(source, output)
        else:
            self.run([self.ffmpeg, "-y", "-i", str(source), "-vn", "-af", f"atempo={tempo:.17g}",
                      "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output)])
        fitted = self.clip_frames(output)
        if fitted > window_frames and tempo < 1.4:
            # atempo output is measured; retry at the ceiling without trimming speech.
            tempo = 1.4
            self.run([self.ffmpeg, "-y", "-i", str(source), "-vn", "-af", f"atempo={tempo:.17g}",
                      "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output)])
            fitted = self.clip_frames(output)
        if fitted > window_frames:
            return None
        return {"raw_duration_seconds": frames / 24000, "fitted_duration_seconds": fitted / 24000, "tempo": tempo}


    def prepare_audio(self, source: Path, output: Path, speed: float, duration: float):
        speed = self._positive(speed, "Speed")
        duration = self._positive(duration, "Duration")
        factors = []
        while speed > 2:
            factors.append("atempo=2")
            speed /= 2
        while speed < 0.5:
            factors.append("atempo=0.5")
            speed *= 2
        factors.append(f"atempo={speed:.17g}")
        # Trim by samples after resampling so the PCM duration is exact.
        frames = round(duration * 48000)
        filters = ",".join(factors + ["aresample=48000", "apad", f"atrim=end_sample={frames}"])
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-i", str(source), "-vn", "-af", filters, "-ar", "48000", "-c:a", "pcm_s16le", str(output)])

    def prepare_background(self, original: Path, output: Path, speed: float, duration: float):
        self.prepare_audio(original, output, speed, duration)

    def concat_audio(self, clips: list[Path], output: Path):
        self.check()
        if not clips:
            raise MediaError("Không có audio lồng tiếng")
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = output.with_suffix(".concat.txt")
        manifest.write_text("".join(f"file '{clip.resolve().as_posix()}'\n" for clip in clips), encoding="utf-8")
        try:
            self.run([self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c:a", "pcm_s16le", str(output)])
        finally:
            manifest.unlink(missing_ok=True)

    def timeline_audio(self, clips: list[Path], starts: list[float], output: Path, target_duration: float | None = None):
        if len(clips) != len(starts) or not clips:
            raise MediaError("Timeline audio không hợp lệ")
        positions = []
        for start in starts:
            if isinstance(start, bool):
                raise MediaError("Timeline start không hợp lệ")
            try:
                start = float(start)
                if not math.isfinite(start) or start < 0:
                    raise ValueError
                positions.append(round(start * 24000))
            except (TypeError, ValueError, OverflowError):
                raise MediaError("Timeline start không hợp lệ") from None
        target_frames = 0 if target_duration is None else round(self._positive(target_duration, "Duration") * 24000)
        check_cancelled()
        output.parent.mkdir(parents=True, exist_ok=True)
        block_frames = 16384
        silence = bytes(block_frames * 2)
        try:
            with wave.open(str(output), "wb") as destination:
                destination.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                cursor = 0

                def pad(end):
                    nonlocal cursor
                    while cursor < end:
                        check_cancelled()
                        count = min(block_frames, end - cursor)
                        destination.writeframesraw(silence[:count * 2])
                        cursor += count

                for clip, position in zip(clips, positions):
                    check_cancelled()
                    if position < cursor:
                        raise MediaError("Timeline audio bị chồng lấn")
                    with wave.open(str(clip), "rb") as source:
                        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 24000, "NONE"):
                            raise MediaError("Clip phải là mono PCM16 24000 Hz")
                        pad(position)
                        remaining = source.getnframes()
                        while remaining:
                            check_cancelled()
                            data = source.readframes(min(block_frames, remaining))
                            frames = len(data) // 2
                            if not frames or len(data) % 2:
                                raise MediaError("Clip PCM bị thiếu dữ liệu")
                            destination.writeframesraw(data)
                            cursor += frames
                            remaining -= frames
                pad(target_frames)
        except (wave.Error, EOFError) as exc:
            raise MediaError(f"Clip PCM không hợp lệ: {exc}") from exc
    def mix_timeline_audio(self, clips: list[Path], starts: list[float], output: Path, target_duration: float | None = None):
        """Mix PCM clips at exact starts; unlike timeline_audio, overlap is valid."""
        if len(clips) != len(starts) or not clips:
            raise MediaError("Timeline audio không hợp lệ")
        import array
        sources = []
        max_end = 0
        try:
            for clip, start in zip(clips, starts):
                try:
                    position = round(float(start) * 24000)
                except (TypeError, ValueError, OverflowError):
                    raise MediaError("Timeline start không hợp lệ") from None
                if position < 0:
                    raise MediaError("Timeline start không hợp lệ")
                source = wave.open(str(clip), "rb")
                if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 24000, "NONE"):
                    raise MediaError("Clip phải là mono PCM 24 kHz 16-bit")
                frames = source.getnframes()
                sources.append((source, position, frames)); max_end = max(max_end, position + frames)
            target = max_end if target_duration is None else round(self._positive(target_duration, "Duration") * 24000)
            if max_end > target:
                raise MediaError("Timeline clip vượt thời lượng")
            mixed = array.array("i", [0]) * target
            for source, position, frames in sources:
                remaining, offset = frames, position
                while remaining:
                    check_cancelled()
                    data = source.readframes(min(remaining, 65536))
                    if not data or len(data) % 2:
                        raise MediaError("Clip PCM bị thiếu dữ liệu")
                    values = array.array("h"); values.frombytes(data)
                    for index, value in enumerate(values):
                        mixed[offset + index] += value
                    offset += len(values); remaining -= len(values)
            output.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(output), "wb") as destination:
                destination.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                for index in range(0, target, 65536):
                    check_cancelled()
                    block = array.array("h", (max(-32768, min(32767, value)) for value in mixed[index:index + 65536]))
                    destination.writeframes(block.tobytes())
        except (wave.Error, EOFError) as exc:
            raise MediaError(f"Clip PCM không hợp lệ: {exc}") from exc
        finally:
            for source, _, _ in sources:
                source.close()

    @staticmethod
    def output_dimensions(source_width, source_height, layout="original"):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 2
               for value in (source_width, source_height)):
            raise MediaError("Kích thước nguồn phải là số nguyên ít nhất 2 pixel")
        if layout == "portrait":
            return 1080, 1920
        if layout == "landscape":
            return 1920, 1080
        if layout != "original":
            raise MediaError("Layout phải là original, portrait hoặc landscape")
        return source_width // 2 * 2, source_height // 2 * 2

    def _encoding(self, encoder, bitrate_mbps):
        if encoder not in ("cpu", "auto", "nvenc"):
            raise MediaError("Encoder phải là cpu, auto hoặc nvenc")
        bitrate_mbps = self._positive(bitrate_mbps, "Bitrate Mbps")
        if not 0.1 <= bitrate_mbps <= 100:
            raise MediaError("Bitrate Mbps phải trong khoảng 0.1..100")
        selected, reason = "cpu", None
        if encoder != "cpu":
            try:
                probe = run_process([
                    self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "color=size=256x144:rate=1", "-frames:v", "1", "-an",
                    "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-f", "null", "-",
                ], capture_output=True, text=True)
                if probe.returncode == 0:
                    selected = "nvenc"
                else:
                    detail = (probe.stderr or "").strip()
                    reason = "NVENC không khả dụng" + (": " + detail[-1000:] if detail else "")
            except OSError as exc:
                reason = f"NVENC không khả dụng: {exc}"
        codec = "h264_nvenc" if selected == "nvenc" else "libx264"
        self.last_encoding = {"requested": encoder, "selected": selected, "codec": codec,
                              "reason": reason, "bitrate_mbps": bitrate_mbps}
        if encoder == "nvenc" and selected != "nvenc":
            raise MediaError(reason)
        return codec, bitrate_mbps

    def render(self, video: Path, audio: Path, subtitles: Path | None, output: Path, speed: float = 1.0, duration: float | None = None,
               *, layout="original", encoder="cpu", bitrate_mbps=8.0, blur_region=None):
        speed = self._positive(speed, "Speed")
        duration = self._positive(duration, "Duration")
        dimensions = self.output_dimensions(2, 2, layout)
        if blur_region is not None:
            if (not isinstance(blur_region, (tuple, list)) or len(blur_region) != 4
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in blur_region)):
                raise MediaError("Blur region phải là (x, y, w, h) chuẩn hóa 0..1")
            x, y, width, height = blur_region
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
                raise MediaError("Blur region phải nằm trong nguồn, chuẩn hóa 0..1")
        self.check()
        codec, bitrate_mbps = self._encoding(encoder, bitrate_mbps)
        output.parent.mkdir(parents=True, exist_ok=True)
        # A fixed relative filter filename avoids both FFmpeg escaping layers,
        # including Windows drive colons, quotes, commas and brackets.
        with tempfile.TemporaryDirectory(prefix="subshort-render-") as directory:
            caption_name = "captions.ass" if subtitles is not None and subtitles.suffix.lower() == ".ass" else "captions.srt"
            if subtitles is not None:
                shutil.copyfile(subtitles, Path(directory) / caption_name)
            executable = str(Path(self.ffmpeg).resolve()) if Path(self.ffmpeg).exists() else self.ffmpeg
            filters = []
            source = "0:v:0"
            if blur_region is not None:
                filters += [f"[{source}]split[clean][region]",
                            f"[region]crop=w=iw*{width:.17g}:h=ih*{height:.17g}:x=iw*{x:.17g}:y=ih*{y:.17g},boxblur=luma_radius='min(10,min(w,h)/2)':luma_power=1[blurred]",
                            f"[clean][blurred]overlay=x=main_w*{x:.17g}:y=main_h*{y:.17g}[redacted]"]
                source = "redacted"
            filters.append(f"[{source}]setpts=(PTS-STARTPTS)/{speed:.17g},tpad=stop_mode=clone:stop_duration={duration:.17g},trim=duration={duration:.17g}[timed]")
            if layout == "original":
                filters.append("[timed]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[picture]")
            else:
                width, height = dimensions
                filters += ["[timed]split[background][foreground]",
                            f"[background]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=20:1,setsar=1[padding]",
                            f"[foreground]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1[fit]",
                            "[padding][fit]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[picture]"]
            filters.append(f"[picture]subtitles=filename={caption_name}[v]" if subtitles is not None else "[picture]null[v]")
            filters.append(f"[1:a:0]apad,atrim=duration={duration:.17g},asetpts=PTS-STARTPTS[a]")
            self.run([executable, "-y", "-i", str(video.resolve()), "-i", str(audio.resolve()),
                      "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
                      "-map", "[v]", "-map", "[a]", "-t", f"{duration:.17g}",
                      "-c:v", codec, "-b:v", f"{bitrate_mbps:.17g}M", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-movflags", "+faststart", str(output.resolve())], cwd=directory)

    def validate_output(self, output: Path, expected_duration: float, has_audio: bool = True):
        expected_duration = self._positive(expected_duration, "Duration")
        if not output.is_file() or output.stat().st_size == 0:
            raise MediaError("Output rỗng hoặc không tồn tại")
        result = run_process([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)], capture_output=True, text=True)
        try:
            if result.returncode:
                raise ValueError
            data = json.loads(result.stdout)
            duration = self._positive(data["format"]["duration"], "Output duration")
            if abs(duration - expected_duration) > 0.2:
                raise ValueError
            streams = data["streams"]
            for kind in (["video", "audio"] if has_audio else ["video"]):
                selected = [stream for stream in streams if stream.get("codec_type") == kind]
                if not selected:
                    raise ValueError
                for stream in selected:
                    actual = self._positive(stream["duration"], f"{kind} duration")
                    if abs(actual - expected_duration) > 0.2:
                        raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, MediaError):
            raise MediaError("Output không có stream/duration hợp lệ") from None

    def subtitle_video(self, video: Path, audio: Path, subtitles: Path, output: Path):
        self.mux(video, audio, subtitles, output)
    def mux(self, video: Path, audio: Path, subtitles: Path | None, output: Path):
        self.check()
        output.parent.mkdir(parents=True, exist_ok=True)
        args = [self.ffmpeg, "-y", "-i", str(video), "-i", str(audio)]
        if subtitles:
            args += ["-i", str(subtitles)]
        args += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac"]
        if subtitles:
            args += ["-c:s", "mov_text"]
        self.run(args + [str(output)])

    @staticmethod
    def run(args, **kwargs):
        result = run_process(args, capture_output=True, text=True, **kwargs)
        if result.returncode:
            raise MediaError(result.stderr[-4000:])
