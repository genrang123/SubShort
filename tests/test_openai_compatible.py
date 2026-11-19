import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from subshort.models import Job, Settings
from subshort.providers import OpenAICompatibleTranslator, Segment


def test_real_http_worker_uses_custom_endpoint_and_preserves_ids():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, self.headers['Authorization'], payload))
            content = {'items': [{'id': '1', 'text': 'hai'}, {'id': '0', 'text': 'một'}]}
            body = json.dumps({'choices': [{'message': {'content': json.dumps(content)}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        translator = OpenAICompatibleTranslator('test-only-key', 'custom-model',
                                               f'http://127.0.0.1:{server.server_port}/v1/')
        assert translator.translate([Segment(0, 1, 'one'), Segment(2, 3, 'two')], 'vi') == [
            Segment(0, 1, 'một'), Segment(2, 3, 'hai')]
        path, auth, payload = requests[0]
        assert path == '/v1/chat/completions'
        assert auth == 'Bearer test-only-key'
        assert payload['model'] == 'custom-model'
        assert payload['response_format']['json_schema']['strict'] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('url', ['', 'file:///tmp/model', 'https://user:secret@host/v1',
                                 'https://host/v1?key=secret', 'https://host/v1#fragment'])
def test_rejects_invalid_or_secret_bearing_base_url(url):
    with pytest.raises(ValueError):
        OpenAICompatibleTranslator('key', 'model', url)


def test_job_snapshot_keeps_endpoint_but_not_compatible_key(tmp_path):
    settings = Settings(tmp_path, translation_provider='openai_compatible',
                        translation_model='model', translation_base_url='https://host/v1',
                        openai_compatible_api_key='private-secret')
    job = Job('test', 'source.mp4', 'vi', config=settings.snapshot())
    assert 'private-secret' not in str(job.config)
    effective = settings.for_job(job)
    assert effective.translation_base_url == 'https://host/v1'
    assert effective.openai_compatible_api_key == 'private-secret'
