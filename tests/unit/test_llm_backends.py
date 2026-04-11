"""Unit tests for LLM backend factory and OllamaBackend HTTP mocking."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.cognition.llm_backends import OllamaBackend, make_backend


# ------------------------------------------------------------------
# make_backend factory
# ------------------------------------------------------------------

def test_make_backend_mock_returns_none():
    """backend='mock' must return None (AgentNode uses its built-in mock planner)."""
    assert make_backend({"backend": "mock"}) is None


def test_make_backend_default_is_mock():
    """Missing 'backend' key defaults to mock."""
    assert make_backend({}) is None


def test_make_backend_ollama_returns_correct_type():
    backend = make_backend({"backend": "ollama", "model": "llama3.1:8b"})
    assert isinstance(backend, OllamaBackend)


def test_make_backend_ollama_model_name():
    backend = make_backend({"backend": "ollama", "model": "qwen2.5:3b"})
    assert backend.model == "qwen2.5:3b"


def test_make_backend_ollama_custom_url():
    backend = make_backend({
        "backend": "ollama",
        "model": "llama3.1:8b",
        "ollama_url": "http://myserver:11434",
    })
    assert "myserver:11434" in backend._url


def test_make_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown backend"):
        make_backend({"backend": "totally_unknown_backend"})


# ------------------------------------------------------------------
# OllamaBackend HTTP mock
# ------------------------------------------------------------------

def _mock_ollama_response(text: str) -> MagicMock:
    """Build a fake urllib.request.urlopen context manager returning *text*."""
    data = json.dumps({"response": text}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_ollama_backend_returns_response_field():
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response("hello world")):
        backend = OllamaBackend(model="test-model")
        result = backend.call("some prompt")
    assert result == "hello world"


def test_ollama_backend_empty_response():
    with patch("urllib.request.urlopen", return_value=_mock_ollama_response("")):
        backend = OllamaBackend(model="test-model")
        result = backend.call("prompt")
    assert result == ""


def test_ollama_backend_posts_correct_model():
    """The HTTP payload must include the configured model name."""
    captured = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        captured.append(body)
        return _mock_ollama_response("ok")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        OllamaBackend(model="my-model").call("test prompt")

    assert captured[0]["model"] == "my-model"
    assert captured[0]["stream"] is False
    assert "test prompt" in captured[0]["prompt"]


def test_ollama_backend_default_url():
    backend = OllamaBackend()
    assert "localhost:11434" in backend._url
