import base64
import json

import pytest

from subshort import drive
from subshort.execution import Cancelled, Execution

URL = "https://www.googleapis.com/upload/drive/v3/files?upload_id=session-secret"


class Response:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = body

    def json(self):
        return self.body


class Session:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def protected_storage(monkeypatch):
    # A reversible fake isolates Windows DPAPI without storing plaintext on disk.
    def dpapi(data, *, protect, error):
        return base64.b64encode(data) if protect else base64.b64decode(data, validate=True)
    monkeypatch.setattr(drive.credentials, "_dpapi", dpapi)
    monkeypatch.setattr(drive, "_wait", lambda seconds: None)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"v" * (drive._CHUNK_SIZE + 3))
    return path


def init():
    return Response(200, {"Location": URL})


def done(size):
    return Response(200, body={"id": "file-id", "size": str(size), "webViewLink": "https://drive.google.com/file/d/file-id/view"})


def test_chunks_progress_and_protected_manifest(tmp_path, source):
    size = source.stat().st_size
    session = Session(init(), Response(308), Response(308, {"Range": f"bytes=0-{drive._CHUNK_SIZE - 1}"}),
                      Response(200, body={"id": "file-id"}), done(size))
    state = tmp_path / "upload.dpapi"
    progress = []
    result = drive.DriveUploader(tmp_path, session).upload(source, "folder", state, lambda *x: progress.append(x))
    assert result["id"] == "file-id" and result["size"] == size
    assert [len(call[2]["data"]) for call in session.calls if call[0] == "PUT"] == [0, drive._CHUNK_SIZE, 3]
    assert progress == [(0, size), (drive._CHUNK_SIZE, size), (size, size)]
    assert b"session-secret" not in state.read_bytes()
    assert b"session_url" not in state.read_bytes()
    assert source.exists()
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


def test_interruption_reopen_queries_server_offset(tmp_path, source):
    state = tmp_path / "upload.dpapi"
    first = Session(init(), Response(308), Response(403))
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, first).upload(source, "folder", state)
    second = Session(Response(308, {"Range": f"bytes=0-{drive._CHUNK_SIZE - 1}"}),
                     Response(200, body={"id": "file-id"}), done(source.stat().st_size))
    drive.DriveUploader(tmp_path, second).upload(source, "folder", state)
    assert second.calls[0][2]["headers"]["Content-Range"] == f"bytes */{source.stat().st_size}"
    assert second.calls[1][2]["data"] == b"vvv"


def test_uncertain_chunk_uses_status_query_not_blind_retry(tmp_path, source):
    session = Session(init(), Response(308), OSError("private-response-secret"),
                      Response(308, {"Range": f"bytes=0-{drive._CHUNK_SIZE - 1}"}),
                      Response(200, body={"id": "file-id"}), done(source.stat().st_size))
    drive.DriveUploader(tmp_path, session).upload(source, "folder", tmp_path / "state")
    assert session.calls[3][2]["headers"]["Content-Range"].startswith("bytes */")


def test_manifest_file_or_folder_mismatch_fails_before_network(tmp_path, source):
    state = tmp_path / "state"
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, Session(init(), Response(403))).upload(source, "folder", state)
    session = Session()
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, session).upload(source, "another-folder", state)
    source.write_bytes(b"changed")
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", state)
    assert session.calls == []


@pytest.mark.parametrize("reported", ["bytes=0-9999999", "bytes=1-2", "bytes=0--1", "secret-body"])
def test_bad_server_offsets_fail(tmp_path, source, reported):
    with pytest.raises(drive.DriveError, match="^Drive upload failed$"):
        drive.DriveUploader(tmp_path, Session(init(), Response(308, {"Range": reported}))).upload(source, "folder", tmp_path / "state")


@pytest.mark.parametrize("url", ["http://www.googleapis.com/u", "https://googleapis.com.evil.test/u", "https://user:password@www.googleapis.com/u", "https://www.googleapis.com:444/u", "https://www.googleapis.com/u#secret"])
def test_rejects_unsafe_session_urls(tmp_path, source, url):
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, Session(Response(200, {"Location": url}))).upload(source, "folder", tmp_path / "state")
    assert not (tmp_path / "state").exists()


def test_verification_mismatch_preserves_completed_id_for_retry(tmp_path, source):
    state = tmp_path / "state"
    session = Session(init(), Response(200, body={"id": "file-id"}), done(1))
    with pytest.raises(drive.DriveError, match="verification failed"):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", state)
    retry = Session(done(source.stat().st_size))
    drive.DriveUploader(tmp_path, retry).upload(source, "folder", state)
    assert [call[0] for call in retry.calls] == ["GET"]
    assert source.exists()


def test_cancel_before_network(tmp_path, source):
    execution = Execution()
    execution.cancel()
    session = Session()
    with execution.activate(), pytest.raises(Cancelled):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", tmp_path / "state")
    assert session.calls == []


def test_transient_attempts_are_finite_and_errors_hide_secrets(tmp_path, source):
    session = Session(*(OSError("private-key-secret") for _ in range(3)))
    with pytest.raises(drive.DriveError, match="^Drive upload failed$"):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", tmp_path / "state")
    assert len(session.calls) == 3


def account():
    return {"type": "service_account", "client_email": "service@example.test", "private_key": "private-key-secret",
            "token_uri": "https://oauth2.googleapis.com/token"}


def test_import_protects_key_and_preserves_source(tmp_path):
    path = tmp_path / "account.json"
    path.write_text(json.dumps(account()), encoding="utf-8")
    drive.import_service_account(tmp_path, path)
    protected = tmp_path / "config" / "drive.dpapi"
    assert b"private-key-secret" not in protected.read_bytes()
    assert drive._load(protected, "error") == account()
    assert path.exists()


@pytest.mark.parametrize("change", [{"type": "user"}, {"private_key": ""}, {"client_email": None},
                                     {"token_uri": "https://evil.test/token"}, {"project_id": 123}])
def test_import_rejects_invalid_credentials_without_overwrite(tmp_path, change):
    path = tmp_path / "account.json"
    path.write_text(json.dumps(account()), encoding="utf-8")
    drive.import_service_account(tmp_path, path)
    protected = tmp_path / "config" / "drive.dpapi"
    before = protected.read_bytes()
    path.write_text(json.dumps(account() | change), encoding="utf-8")
    with pytest.raises(drive.DriveError):
        drive.import_service_account(tmp_path, path)
    assert protected.read_bytes() == before


def test_state_cannot_overwrite_source(tmp_path, source):
    before = source.read_bytes()
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, Session()).upload(source, "folder", source)
    assert source.read_bytes() == before


def test_cancel_during_progress_preserves_resume_state(tmp_path, source):
    execution = Execution()
    state = tmp_path / "state"
    session = Session(init(), Response(308))
    with execution.activate(), pytest.raises(Cancelled):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", state,
                                                    lambda sent, total: execution.cancel())
    assert len(session.calls) == 2
    assert state.exists() and source.exists()


def test_corrupt_state_fails_closed(tmp_path, source):
    state = tmp_path / "state"
    state.write_bytes(b"not-protected-data")
    session = Session()
    with pytest.raises(drive.DriveError):
        drive.DriveUploader(tmp_path, session).upload(source, "folder", state)
    assert session.calls == []
