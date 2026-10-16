"""Local generative ASR must never receive vocabulary lists as spoken context."""
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import sys
import pytest


@pytest.fixture
def local_qwen(monkeypatch):
    # Inference is mocked: exercise real transports without MLX/NumPy in core CI.
    class Audio:
        def __init__(self, samples, dtype):
            self.samples = samples
            self.dtype = dtype

        def __truediv__(self, scale):
            return Audio([value / scale for value in self.samples], self.dtype)

    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(array=Audio, float32='float32', zeros=lambda count, dtype: Audio([0] * count, dtype)))
    spec = importlib.util.spec_from_file_location(
        'qwen_hotword_test_server',
        Path(__file__).parents[1] / 'macos/qwen3-asr-server/server.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingSession:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if kwargs.get('context'):
            return SimpleNamespace(text=kwargs['context'])
        return SimpleNamespace(text='Build the API with SwiftUI.')


@pytest.mark.parametrize('partial', [False, True])
def test_audio_only_and_preserve_spoken_technical_terms(local_qwen, partial):
    session = RecordingSession()
    text = local_qwen._transcribe_sync(session, [123] * 160, strip_punct=partial)
    assert text == ('Build the API with SwiftUI' if partial else 'Build the API with SwiftUI.')
    assert session.calls[0][1] == {}
    assert session.calls[0][0].dtype == 'float32'


@pytest.mark.parametrize('route', ['http', 'json', 'sse', 'websocket'])
def test_all_transports_omit_vocabulary_context(local_qwen, route, monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr(local_qwen, 'get_session', lambda: session)
    audio = b'\x01\x00' * 160
    with TestClient(local_qwen.app) as client:
        if route == 'websocket':
            with client.websocket_connect('/ws') as ws:
                ws.send_bytes(audio)
                ws.send_bytes(b'')
                assert ws.receive_json() == {
                    'type': 'transcript', 'text': 'Build the API with SwiftUI.', 'is_final': True,
                }
                assert ws.receive_json() == {'type': 'completed'}
        elif route == 'http':
            assert client.post('/transcribe', content=audio).json()['text'] == 'Build the API with SwiftUI.'
        else:
            response = client.post('/v1/chat/completions', json={
                'stream': route == 'sse',
                'messages': [{'content': [{'input_audio': {
                    'data': base64.b64encode(audio).decode(),
                }}]}],
            })
            assert response.status_code == 200
            if route == 'sse':
                import json
                parts = [json.loads(line[6:])['choices'][0]['delta']['content']
                         for line in response.text.splitlines()
                         if line.startswith('data: ') and line != 'data: [DONE]']
                assert ''.join(parts) == 'Build the API with SwiftUI.'
            else:
                assert response.json()['choices'][0]['message']['content'] == 'Build the API with SwiftUI.'
    assert session.calls and all(not kwargs for _, kwargs in session.calls)


@pytest.mark.parametrize('file_exists', [False, True])
def test_launcher_keeps_hotwords_argument_without_reading_file(local_qwen, monkeypatch, tmp_path, file_exists):
    from unittest.mock import Mock
    model = tmp_path / 'model'
    model.mkdir()
    words = tmp_path / 'words.txt'
    if file_exists:
        words.write_text('ExampleProduct\nAPI\nSwiftUI')
    session = RecordingSession()
    monkeypatch.setattr(local_qwen, 'get_session', lambda: session)
    run = Mock()
    monkeypatch.setattr(local_qwen.uvicorn, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['server.py', '--model-path', str(model),
                                    '--hotwords-file', str(words), '--port', '18769'])
    read = Mock(side_effect=AssertionError('Vocabulary file must not be read'))
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    local_qwen.main()
    read.assert_not_called()
    run.assert_called_once()
    assert all(not kwargs for _, kwargs in session.calls)
