"""Opt-in source-space subtitle detection; no network work in off/manual mode."""
import base64
import json
import math
import struct
import zlib

from . import providers
from .execution import check_cancelled, run_process
from .providers import FatalProviderError, ProviderError


def validate_region(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("Vùng phụ đề phải là [x,y,w,h] chuẩn hóa theo nguồn")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError("Tọa độ vùng phụ đề phải là số hữu hạn")
    x, y, w, h = value
    if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1 and y + h <= 1):
        raise ValueError("Vùng phụ đề nằm ngoài nguồn")
    return tuple(value)


def parse_detection(content):
    try:
        answer = json.loads(content)
        if not isinstance(answer, dict) or set(answer) != {"has_subtitles", "box"} or type(answer["has_subtitles"]) is not bool:
            raise ValueError
        if not answer["has_subtitles"]:
            if answer["box"] is not None:
                raise ValueError
            return None
        return validate_region(answer["box"])
    except (TypeError, ValueError):
        raise ProviderError("Vision trả về vùng phụ đề không hợp lệ") from None


def _png(width, height, pixels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def sample_frames(media, video, source, work):
    scale = min(1, 640 / source["width"], 360 / source["height"])
    width, height = max(2, round(source["width"] * scale)), max(2, round(source["height"] * scale))
    frames = []
    for index, fraction in enumerate((.2, .5, .8)):
        check_cancelled()
        timestamp = source["duration"] * fraction
        result = run_process([media.ffmpeg, "-v", "error", "-ss", str(timestamp), "-i", str(video),
                              "-frames:v", "1", "-vf", f"scale={width}:{height}", "-pix_fmt", "gray",
                              "-f", "rawvideo", "pipe:1"], capture_output=True)
        if result.returncode or len(result.stdout) != width * height:
            raise ProviderError("Không lấy được khung hình cho vision")
        path = work / f"subtitle_frame_{index}.png"
        path.write_bytes(_png(width, height, result.stdout))
        frames.append({"timestamp": timestamp, "path": path, "width": width, "height": height, "pixels": result.stdout})
    return frames


def detect(frames, settings):
    provider = providers.GeminiTranslator if settings.vision_provider == "gemini" else providers.OpenAITranslator
    key = settings.gemini_api_key if settings.vision_provider == "gemini" else settings.openai_api_key
    if not key:
        raise FatalProviderError(f"Thiếu {provider.key_name}")
    content = [{"type": "text", "text": 'Locate burned-in dialogue subtitles (not logos or scene text) across these frames. Return ONLY JSON {"has_subtitles":false,"box":null} if absent, otherwise {"has_subtitles":true,"box":[x,y,w,h]} with one tight union box in normalized source coordinates. Never guess a default bottom band.'}]
    for frame in frames:
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(frame["path"].read_bytes()).decode("ascii")}})
    payload = {"model": settings.vision_model, "messages": [{"role": "user", "content": content}],
               "response_format": {"type": "json_object"}, "temperature": 0}
    request = json.dumps({"url": provider.endpoint, "headers": {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          "payload": json.dumps(payload), "timeout": providers._HTTP_TIMEOUT})
    for attempt in range(providers._HTTP_ATTEMPTS):
        check_cancelled()
        try:
            body = providers._read_answer(providers._run_worker(request), provider.provider)
            break
        except providers._Transient as exc:
            if attempt + 1 == providers._HTTP_ATTEMPTS:
                raise ProviderError(f"Vision thất bại sau {providers._HTTP_ATTEMPTS} lần thử: {exc.detail}") from None
            providers._wait(exc.retry_after if exc.retry_after is not None else providers._HTTP_BACKOFF[attempt])
    check_cancelled()
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, TypeError, KeyError, IndexError):
        raise ProviderError("Vision phản hồi không hợp lệ") from None
    return parse_detection(content)


def refine_region(frames, proposed):
    """Tighten to measured high-contrast edges inside the vision proposal."""
    boxes = []
    for frame in frames:
        check_cancelled()
        width, height, pixels = frame["width"], frame["height"], frame["pixels"]
        x, y, w, h = proposed
        left, top = max(1, math.floor(x * width)), max(1, math.floor(y * height))
        right, bottom = min(width - 1, math.ceil((x + w) * width)), min(height - 1, math.ceil((y + h) * height))
        points = []
        # shortcut: contrast edges are not OCR; keep diagnostics for human visual acceptance.
        for row in range(top, bottom):
            edges = [col for col in range(left, right)
                     if max(abs(pixels[row * width + col] - pixels[row * width + col - 1]),
                            abs(pixels[row * width + col] - pixels[(row - 1) * width + col])) >= 48]
            if len(edges) >= 4:
                points.extend((col, row) for col in edges)
        if points:
            xs, ys = zip(*points)
            boxes.append((max(0, min(xs) - 2) / width, max(0, min(ys) - 2) / height,
                          min(width, max(xs) + 3) / width, min(height, max(ys) + 3) / height))
    if not boxes:
        raise ProviderError("Không có biên pixel để tinh chỉnh vùng phụ đề; cần chọn vùng thủ công")
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return validate_region((left, top, right - left, bottom - top))


def resolve_region(settings, media, video, source, work):
    mode = settings.old_subtitle_mode
    info = {"mode": mode, "sampled_frames": [], "proposed_box": None, "refined_box": None, "region": None}
    if mode == "off":
        return None, info
    if mode == "manual":
        region = validate_region(json.loads(settings.old_subtitle_region))
    else:
        frames = sample_frames(media, video, source, work)
        info.update(provider=settings.vision_provider, model=settings.vision_model,
                    sampled_frames=[{"timestamp": f["timestamp"], "path": f["path"].name, "width": f["width"], "height": f["height"]} for f in frames])
        region = None
        try:
            proposed = detect(frames, settings)
            info["proposed_box"] = proposed
            if proposed is not None:
                region = refine_region(frames, proposed)
                info["refined_box"] = region
            info["result"] = "no_subtitles" if proposed is None else "detected"
        except ProviderError:
            info["result"] = "failed"
            (work / "subtitle_region.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            raise
    info["region"] = region
    info["diagnostic"] = "subtitle_region.json"
    (work / "subtitle_region.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return region, info
