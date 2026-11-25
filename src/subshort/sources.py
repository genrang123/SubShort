"""Resolve local files and public, authorized HTTPS video sources."""
from pathlib import Path
import math
import re
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

from .execution import Cancelled, check_cancelled
from .providers import FatalProviderError, ProviderError


_HOSTS = {
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"},
    "douyin": {"douyin.com", "www.douyin.com", "v.douyin.com"},
    "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com", "fb.watch"},
    "drive": {"drive.google.com"},
}
_PREFIXES = {"yt": "youtube", "douyin": "douyin", "drive": "drive"}
_ID_PATTERNS = {"youtube": r"[A-Za-z0-9_-]{11}", "douyin": r"[0-9]+", "drive": r"[A-Za-z0-9_-]{10,}"}
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts", ".mpeg", ".mpg", ".3gp"}


def _source(value):
    if not isinstance(value, (str, Path)):
        raise ProviderError("Nguồn video không hợp lệ")
    text = str(value)
    if not text or text != text.strip() or any(ord(char) < 32 for char in text):
        raise ProviderError("Nguồn video không hợp lệ")
    prefix, separator, rest = text.partition(":")
    expected = _PREFIXES.get(prefix.lower()) if separator else None
    if expected:
        if re.fullmatch(_ID_PATTERNS[expected], rest):
            templates = {"youtube": "https://www.youtube.com/watch?v={}", "douyin": "https://www.douyin.com/video/{}", "drive": "https://drive.google.com/file/d/{}/view"}
            return expected, templates[expected].format(rest)
        text = rest
    # A Windows drive letter is a local path, not a URL scheme.
    if not expected and (re.match(r"^[A-Za-z]:[\\/]", text) or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text)):
        if text.startswith("//") or text.startswith("\\\\"):
            raise ProviderError("Nguồn mạng phải dùng URL HTTPS được hỗ trợ")
        return "local", text
    try:
        url = urlsplit(text)
        if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None or url.port not in (None, 443) or "\\" in text:
            raise ValueError
        kind = next((kind for kind, hosts in _HOSTS.items() if url.hostname in hosts), None)
        if kind is None or (expected and expected != kind):
            raise ValueError
    except ValueError:
        raise ProviderError("Chỉ hỗ trợ URL HTTPS công khai từ YouTube, Douyin, Facebook hoặc Google Drive") from None
    return kind, text


def youtube_video_id(source: str) -> str:
    try:
        kind, value = _source(source)
    except ProviderError:
        return ''
    if kind != 'youtube':
        return ''
    url = urlsplit(value)
    path = url.path.rstrip('/')
    segments = path.split('/')
    if url.hostname == 'youtu.be':
        candidate = unquote(segments[1]) if len(segments) == 2 else ''
    elif path == '/watch':
        ids = parse_qs(url.query, keep_blank_values=True).get('v', [])
        candidate = ids[0] if len(ids) == 1 else ''
    elif len(segments) == 3 and segments[1] in ('shorts', 'live', 'embed'):
        candidate = unquote(segments[2])
    else:
        return ''
    return candidate if re.fullmatch(_ID_PATTERNS['youtube'], candidate) else ''


def classify_source(value) -> str:
    """Classify without checking local existence; bare IDs remain local paths."""
    return _source(value)[0]


class _QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def resolve_source(source, work: Path, media, notify=None) -> Path:
    """Download public sources into owned scratch space, probe, then publish."""
    kind, value = _source(source)
    check_cancelled()
    if kind == "local":
        path = source if isinstance(source, Path) else Path(value)
        if not path.is_file():
            raise ProviderError("Không tìm thấy tệp video nguồn")
        return path
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise FatalProviderError("Cài yt-dlp để tải video nguồn trực tuyến (pip install yt-dlp)") from None

    def progress(_status):
        check_cancelled()

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    if notify is not None:
        notify("Đang tải video nguồn công khai")
    with tempfile.TemporaryDirectory(prefix="source-", dir=work) as directory:
        scratch = Path(directory)
        options = {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": str(scratch / "download.%(ext)s"),
            "noplaylist": True,
            "restrictfilenames": True,
            "progress_hooks": [progress],
            "postprocessor_hooks": [progress],
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            # A user yt-dlp config must not inject cookies, credentials or proxies silently.
            "ignoreconfig": True,
            "cachedir": False,
        }
        try:
            with YoutubeDL(options) as downloader:
                result = downloader.extract_info(value, download=True)
            check_cancelled()
            if not isinstance(result, dict) or result.get("_type") in {"playlist", "multi_video"}:
                raise ValueError
            files = [path for path in scratch.iterdir() if path.stem == "download" and path.suffix.lower() in _VIDEO_EXTENSIONS and path.is_file() and not path.is_symlink()]
            if len(files) != 1:
                raise ValueError
            downloaded = files[0]
            if downloaded.stat().st_size == 0:
                raise ValueError
            probe = media.probe(downloaded)
            duration = probe["duration"]
            if isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0 or probe.get("has_video") is False:
                raise ValueError
            check_cancelled()
            destination = work / ("source" + downloaded.suffix.lower())
            downloaded.replace(destination)
            return destination
        except Cancelled:
            raise
        except Exception:
            check_cancelled()
            # Do not expose provider messages, URL query tokens or response bodies.
            raise ProviderError("Không tải hoặc xác minh được video công khai; kiểm tra liên kết, quyền truy cập và FFmpeg") from None
