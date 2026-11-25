"""OpenAI HTTP policy: retries, cancellation and secret-safe errors.

All HTTP traffic is stubbed or served by a loopback server in this process.
"""
import json
import pathlib
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from subshort import providers
from subshort.execution import Cancelled, Execution
from subshort.providers import FatalProviderError, OpenAITranslator, ProviderError

ROOT = pathlib.Path(__file__).resolve().parents[1] / "src"
OK_BODY = json.dumps({"choices": [{"message": {"content": json.dumps({"items": [{"id": "0", "text": "một"}]})}}]})
KEY = "sk-secret"
SEGMENTS = [providers.Segment(0, 1, "one")]


def reply(ok, **fields):
    return json.dumps({"ok": ok, **fields})


class Runner:
    """Deterministic stand-in for run_process: replays scripted answers."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.timeouts = []

    def __call__(self, args, **kwargs):
        self.requests.append(kwargs["input"])
        self.timeouts.append(kwargs["timeout"])
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(args, 0, answer, "")


@pytest.fixture
def install(monkeypatch):
    def install(*answers):
        runner = Runner(*answers)
        monkeypatch.setattr(providers, "run_process", runner)
        return runner

    return install


@pytest.fixture
def waits(monkeypatch):
    recorded = []
    monkeypatch.setattr(providers, "_wait", recorded.append)
    return recorded


def test_retries_twice_then_succeeds_with_exponential_backoff(install, waits):
    runner = install(reply(False, status=500, body=""), reply(False, status=502, body=""), reply(True, body=OK_BODY))
    segments = OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert [s.text for s in segments] == ["một"]
    assert waits == [0.5, 1.0]
    assert len(runner.requests) == 3 and runner.timeouts == [120, 120, 120]


def test_exhausted_retries_report_attempt_count_only(install, waits):
    runner = install(*[reply(False, status=503, body="quota details sk-secret") for _ in range(3)])
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    message = str(info.value)
    assert "3" in message and "HTTP 503" in message
    assert "sk-secret" not in message and "quota details" not in message
    assert len(runner.requests) == 3 and waits == [0.5, 1.0]


def test_retry_after_is_honoured_and_capped_for_429(install, waits):
    install(reply(False, status=429, body="", retry_after="120"), reply(True, body=OK_BODY))
    assert [s.text for s in OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")] == ["một"]
    assert waits == [30.0]


def test_retry_after_absent_falls_back_to_backoff(install, waits):
    install(reply(False, status=429, body="", retry_after="later"), reply(True, body=OK_BODY))
    OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert waits == [0.5]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_fatal_without_retry(install, waits, status):
    runner = install(reply(False, status=status, body='{"error":"bad key sk-secret"}'))
    with pytest.raises(FatalProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert f"HTTP {status}" in str(info.value) and "sk-secret" not in str(info.value)
    assert len(runner.requests) == 1 and waits == []


def test_insufficient_quota_is_fatal_without_retry(install, waits):
    runner = install(reply(False, status=429, body='{"error":{"code":"insufficient_quota"}}'))
    with pytest.raises(FatalProviderError):
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert len(runner.requests) == 1 and waits == []


def test_other_4xx_is_not_retried(install, waits):
    runner = install(reply(False, status=400, body='{"error":"bad request sk-secret"}'))
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert "HTTP 400" in str(info.value) and "sk-secret" not in str(info.value)
    assert len(runner.requests) == 1 and waits == []


def test_worker_timeout_and_network_categories_are_retried(install, waits):
    runner = install(reply(False, error="timeout"), reply(False, error="network"), reply(True, body=OK_BODY))
    OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert len(runner.requests) == 3 and waits == [0.5, 1.0]


@pytest.mark.parametrize("status", [408, 500, 503])
def test_transient_statuses_retry(install, waits, status):
    install(reply(False, status=status, body=""), reply(True, body=OK_BODY))
    OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert waits == [0.5]


def test_process_timeout_is_retried(install, waits):
    install(subprocess.TimeoutExpired(["worker"], 30), reply(True, body=OK_BODY))
    OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert waits == [0.5]


def test_worker_start_failure_is_fatal(monkeypatch, waits):
    def run_process(args, **kwargs):
        raise OSError("no interpreter sk-secret")

    monkeypatch.setattr(providers, "run_process", run_process)
    with pytest.raises(FatalProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert "sk-secret" not in str(info.value) and waits == []


def test_worker_nonzero_exit_hides_stderr(monkeypatch, waits):
    calls = []

    def run_process(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "Traceback sk-secret body")

    monkeypatch.setattr(providers, "run_process", run_process)
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert "sk-secret" not in str(info.value) and "Traceback" not in str(info.value)
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("answer", [OSError(f"{KEY} in path"), f"{KEY} not json",
                                    reply(True, body='{"choices":[{"message":{"content":null}}]}')])
def test_traceback_never_carries_the_secret(install, waits, answer):
    install(answer)
    translator = OpenAITranslator(KEY)
    with pytest.raises(ProviderError) as info:
        translator.translate(SEGMENTS, "vi")
    rendered = "".join(traceback.format_exception(type(info.value), info.value, info.value.__traceback__))
    assert KEY not in rendered


@pytest.mark.parametrize("stdout", [
    "not json",
    "[]",
    '{"ok": "yes", "body": "{}"}',
    '{"ok": false}',
    '{"ok": false, "error": "boom sk-secret"}',
    '{"ok": false, "status": "500", "body": ""}',
    '{"ok": false, "status": true, "body": ""}',
    '{"ok": false, "status": 500}',
    '{"ok": true}',
    '{"ok": true, "body": 3}',
])
def test_invalid_worker_reply_is_a_nonretry_error(install, waits, stdout):
    runner = install(stdout)
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert not isinstance(info.value, FatalProviderError)
    assert "sk-secret" not in str(info.value)
    assert len(runner.requests) == 1 and waits == []


@pytest.mark.parametrize("body", ["not json", '{"choices": []}', '{"choices":[{"message":{}}]}'])
def test_malformed_openai_body_is_a_nonretry_error(install, waits, body):
    install(reply(True, body=body))
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert not isinstance(info.value, FatalProviderError) and waits == []


def test_missing_key_is_fatal_before_spawning(install):
    runner = install()
    with pytest.raises(FatalProviderError):
        OpenAITranslator("").translate(SEGMENTS, "vi")
    assert not runner.requests


def test_key_stays_out_of_argv_and_errors(install):
    runner = install(*[reply(False, status=500, body="") for _ in range(3)])
    with pytest.raises(ProviderError) as info:
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert "sk-secret" not in str(info.value)
    assert "sk-secret" in runner.requests[0]


def test_cancelled_before_request_sends_nothing(install):
    runner = install(reply(True, body=OK_BODY))
    execution = Execution()
    execution.cancel()
    with execution.activate(), pytest.raises(Cancelled):
        OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
    assert not runner.requests


def test_cancellation_interrupts_backoff(install):
    # Real _wait: a 429 asks for 30 s, the cancel arrives long before that.
    install(reply(False, status=429, body="", retry_after="30"))
    execution = Execution()
    outcome = {}

    def run():
        try:
            with execution.activate():
                OpenAITranslator("sk-secret").translate(SEGMENTS, "vi")
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    execution.cancel()
    thread.join(10)
    assert not thread.is_alive() and isinstance(outcome.get("error"), Cancelled)


def _worker(url, timeout=5):
    request = json.dumps({"url": url, "headers": {"Content-Type": "application/json",
                                                  "Authorization": "Bearer sk-secret"},
                          "payload": "{}", "timeout": timeout})
    result = subprocess.run([sys.executable, "-m", "subshort.http_worker"], cwd=ROOT, input=request,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def serve():
    servers = []

    def serve(handler_body):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                handler_body(self)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/v1/chat/completions"

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_worker_reports_status_body_and_retry_after_only(serve):
    def respond(handler):
        body = b'{"error":{"code":"rate_limit"}}'
        handler.send_response(429)
        handler.send_header("Retry-After", "7")
        handler.send_header("Set-Cookie", "session=leak")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    answer = _worker(serve(respond))
    assert set(answer) <= {"ok", "status", "body", "retry_after"}
    assert answer["ok"] is False and answer["status"] == 429 and answer["retry_after"] == "7"
    assert answer["body"] == '{"error":{"code":"rate_limit"}}'
    assert "leak" not in json.dumps(answer) and "sk-secret" not in json.dumps(answer)


def test_worker_returns_body_on_success(serve):
    def respond(handler):
        body = OK_BODY.encode()
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    answer = _worker(serve(respond))
    assert answer == {"ok": True, "body": OK_BODY}


def test_worker_returns_redirect_status_unchanged(serve):
    def respond(handler):
        handler.send_response(302)
        handler.send_header('Location', '/alternate-endpoint')
        handler.end_headers()

    answer = _worker(serve(respond))
    assert answer == {'ok': False, 'status': 302, 'body': ''}


def test_worker_uses_safe_network_category(serve):
    def respond(handler):
        handler.close_connection = True

    assert _worker(serve(respond))["error"] == "network"
