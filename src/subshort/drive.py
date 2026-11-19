"""Opt-in Drive v3 publishing; credentials and resumable URLs stay DPAPI-protected."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import credentials
from .execution import Cancelled, check_cancelled
from .providers import _wait

_CHUNK_SIZE = 256 * 1024
_SCOPE = "https://www.googleapis.com/auth/drive.file"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_FILES = "https://www.googleapis.com/drive/v3/files/"
_ERROR = "Drive upload failed"
_CREDENTIAL_ERROR = "Invalid or unavailable protected Drive service account"
_STATE_ERROR = "Invalid or mismatched protected Drive upload state"
_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


class DriveError(RuntimeError):
    pass


class _Transient(Exception):
    pass


def _validate_account(info):
    if (not isinstance(info, dict) or info.get("type") != "service_account"
            or info.get("token_uri") != "https://oauth2.googleapis.com/token"
            or not all(isinstance(info.get(key), str) and info[key].strip()
                       for key in ("client_email", "private_key"))
            or ("project_id" in info and not isinstance(info["project_id"], str))):
        raise DriveError(_CREDENTIAL_ERROR)
    return info


def _save(path: Path, value: dict):
    try:
        encrypted = credentials._dpapi(json.dumps(value).encode("utf-8"), protect=True, error=_STATE_ERROR)
        credentials._write_atomic(path, encrypted)
    except Exception:
        raise DriveError(_STATE_ERROR) from None


def _load(path: Path, error: str):
    try:
        return json.loads(credentials._dpapi(path.read_bytes(), protect=False, error=error))
    except Exception:
        raise DriveError(error) from None


def import_service_account(root: Path, path: Path) -> None:
    """Validate and protect an explicitly selected JSON key; never copy plaintext."""
    try:
        info = _validate_account(json.loads(Path(path).read_text(encoding="utf-8")))
        protected = credentials._dpapi(json.dumps(info).encode("utf-8"), protect=True, error=_CREDENTIAL_ERROR)
        credentials._write_atomic(Path(root) / "config" / "drive.dpapi", protected)
    except Exception:
        raise DriveError(_CREDENTIAL_ERROR) from None


def _session_url(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        valid = (url.scheme == "https" and (host == "googleapis.com" or host.endswith(".googleapis.com"))
                 and not url.username and not url.password and url.port in (None, 443) and not url.fragment)
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise DriveError(_STATE_ERROR)
    return value


def _offset(response, size):
    value = response.headers.get("Range")
    if value is None:
        return 0
    match = re.fullmatch(r"bytes=0-([0-9]+)", value)
    if not match or not 0 < int(match[1]) + 1 <= size:
        raise DriveError(_ERROR)
    return int(match[1]) + 1


class DriveUploader:
    def __init__(self, root: Path, session=None):
        self.root = Path(root)
        self._session = session

    def _connect(self):
        if self._session is None:
            info = _validate_account(_load(self.root / "config" / "drive.dpapi", _CREDENTIAL_ERROR))
            try:
                from google.oauth2 import service_account
                from google.auth.transport.requests import AuthorizedSession
                account = service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
                self._session = AuthorizedSession(account)
            except Exception:
                raise DriveError(_CREDENTIAL_ERROR) from None
        return self._session

    def _request(self, method, url, *, retry=True, **kwargs):
        for attempt in range(3 if retry else 1):
            check_cancelled()
            try:
                response = self._connect().request(method, url, timeout=30, allow_redirects=False, **kwargs)
            except (Cancelled, DriveError):
                raise
            except Exception:
                response = None
            if response is not None and response.status_code not in (408, 429) and not 500 <= response.status_code <= 599:
                return response
            if not retry:
                raise _Transient() from None
            if attempt < 2:
                _wait(0.5 * (attempt + 1))
        raise DriveError(_ERROR) from None

    @staticmethod
    def _file_id(response):
        try:
            value = response.json()["id"]
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError()
            return value
        except Exception:
            raise DriveError(_ERROR) from None

    def upload(self, file: Path, folder_id: str, state_path: Path, progress=None) -> dict:
        """Upload/resume a file. progress receives (confirmed_bytes, total_bytes)."""
        check_cancelled()
        if not isinstance(folder_id, str) or not _ID.fullmatch(folder_id):
            raise DriveError("Invalid Drive folder ID")
        file, state_path = Path(file), Path(state_path)
        if file.resolve() == state_path.resolve():
            raise DriveError("Drive upload state cannot replace source")
        try:
            stat = file.stat()
            digest = hashlib.sha256()
            with file.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    check_cancelled()
                    digest.update(chunk)
            after_hash = file.stat()
            if (after_hash.st_size, after_hash.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                raise DriveError("Drive upload source changed")
            binding = {"sha256": digest.hexdigest(), "size": stat.st_size,
                       "mtime_ns": stat.st_mtime_ns, "folder_id": folder_id}
        except Cancelled:
            raise
        except OSError:
            raise DriveError("Cannot read Drive upload source") from None
        size = stat.st_size
        if state_path.exists():
            state = _load(state_path, _STATE_ERROR)
            if not isinstance(state, dict) or state.get("file") != binding:
                raise DriveError(_STATE_ERROR)
            url = _session_url(state.get("session_url"))
        else:
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            response = self._request("POST", _UPLOAD, params={"uploadType": "resumable", "fields": "id"},
                                     headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
                                     json={"name": file.name, "parents": [folder_id]})
            if response.status_code not in (200, 201):
                raise DriveError(_ERROR)
            url = _session_url(response.headers.get("Location"))
            state = {"file": binding, "session_url": url}
            _save(state_path, state)
        file_id = state.get("id")
        if file_id is not None and (not isinstance(file_id, str) or not _ID.fullmatch(file_id)):
            raise DriveError(_STATE_ERROR)
        offset, failures = 0, 0
        query = True
        with file.open("rb") as source:
            while file_id is None:
                check_cancelled()
                if file.stat().st_size != size or file.stat().st_mtime_ns != stat.st_mtime_ns:
                    raise DriveError("Drive upload source changed")
                if query:
                    response = self._request("PUT", url, headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"}, data=b"")
                else:
                    source.seek(offset)
                    chunk = source.read(min(_CHUNK_SIZE, size - offset))
                    content_range = f"bytes {offset}-{offset + len(chunk) - 1}/{size}" if chunk else f"bytes */{size}"
                    try:
                        response = self._request("PUT", url, retry=False,
                                                 headers={"Content-Length": str(len(chunk)), "Content-Range": content_range}, data=chunk)
                    except _Transient:
                        failures += 1
                        if failures >= 3:
                            raise DriveError(_ERROR) from None
                        _wait(0.5 * failures)
                        query = True
                        continue
                if response.status_code in (200, 201):
                    file_id = self._file_id(response)
                    state["id"] = file_id
                    _save(state_path, state)
                    break
                if response.status_code != 308:
                    raise DriveError(_ERROR)
                confirmed = _offset(response, size)
                if confirmed == size and size > 0 or (not query and confirmed <= offset):
                    raise DriveError(_ERROR)
                if confirmed > offset:
                    failures = 0
                offset = confirmed
                if progress:
                    progress(offset, size)
                query = False
        response = self._request("GET", _FILES + file_id, params={"fields": "id,webViewLink,size"})
        if response.status_code != 200:
            raise DriveError(_ERROR)
        try:
            result = response.json()
            if (file.stat().st_size, file.stat().st_mtime_ns) != (size, stat.st_mtime_ns):
                raise ValueError()
            if (result["id"] != file_id or str(result["size"]) != str(size)
                    or not isinstance(result.get("webViewLink"), str)):
                raise ValueError()
        except Exception:
            raise DriveError("Drive upload verification failed") from None
        if progress:
            progress(size, size)
        return {"id": file_id, "webViewLink": result["webViewLink"], "size": size}
