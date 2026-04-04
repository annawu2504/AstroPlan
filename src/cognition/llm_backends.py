"""LLM backend abstraction layer for AstroPlan.

Defines a minimal LLMBackend protocol (one method: call(prompt) -> str) and
provides three concrete backends plus a factory function driven by a config dict.

Backends
--------
OllamaBackend
    HTTP REST calls to a local Ollama daemon (stdlib urllib — zero extra deps).
    Supports any model pulled via ``ollama pull <model>``.
    e.g. llama3.1:8b, qwen2.5:3b, mistral, phi3

HuggingFaceBackend
    Direct local inference via ``transformers.pipeline("text-generation")``.
    Supports any model on HuggingFace Hub or a local path.
    Optional 8-bit quantisation (requires bitsandbytes).
    e.g. "Qwen/Qwen2.5-3B-Instruct", "meta-llama/Llama-3.1-8B-Instruct"

AnthropicBackend
    Wraps the existing Anthropic SDK client used by main.py.

All three satisfy the LLMBackend Protocol so they are drop-in replacements
for AgentNode's llm_client parameter — no AgentNode changes needed.

Usage::

    from src.cognition.llm_backends import make_backend

    # Ollama (no extra install beyond ollama itself)
    backend = make_backend({"backend": "ollama", "model": "llama3.1:8b"})

    # Local HuggingFace
    backend = make_backend({"backend": "huggingface",
                            "model": "Qwen/Qwen2.5-3B-Instruct",
                            "load_in_8bit": False})

    # Anthropic
    backend = make_backend({"backend": "anthropic",
                            "model": "claude-sonnet-4-6",
                            "api_key": "sk-ant-..."})

    # Mock (returns None → AgentNode uses its built-in mock planner)
    backend = make_backend({"backend": "mock"})

    agent = AgentNode(node_id="root", llm_client=backend)

Comparison with ReAcTree
------------------------
ReAcTree uses the ``guidance`` library for constrained generation
(guidance.select, guidance.gen) which forces the model to produce
valid tokens from a predefined set.  AstroPlan uses JSON parsing +
graceful degradation (_parse_llm_response → Think fallback), which
works with any text-generation backend without constrained decoding.
This is intentional: guidance is a heavy dependency and its constrained
generation is not needed when the model is instructed to output JSON.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class LLMBackend:
    """Structural protocol — any object with call(prompt: str) -> str qualifies.

    Not a formal typing.Protocol subclass so that runtime isinstance checks
    work without explicit registration.  AgentNode checks duck-typing only.
    """

    def call(self, prompt: str) -> str:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OllamaBackend
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """Call a locally-running Ollama daemon via its REST API.

    No dependencies beyond Python stdlib (urllib).

    Parameters
    ----------
    model:
        Ollama model tag, e.g. ``"llama3.1:8b"``, ``"qwen2.5:3b"``.
        Must be pulled first: ``ollama pull <model>``.
    base_url:
        Ollama server address (default: localhost 11434).
    timeout_s:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        base_url: str = "http://localhost:11434",
        timeout_s: int = 120,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/api/generate"
        self._timeout = timeout_s

    def call(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "")


# ---------------------------------------------------------------------------
# HuggingFaceBackend
# ---------------------------------------------------------------------------

class HuggingFaceBackend(LLMBackend):
    """Local HuggingFace transformers text-generation pipeline.

    Supports any causal-LM checkpoint on HF Hub or a local path.
    Optional 8-bit quantisation via bitsandbytes.

    Parameters
    ----------
    model_name:
        HF Hub model ID or local path, e.g. ``"Qwen/Qwen2.5-3B-Instruct"``,
        ``"meta-llama/Llama-3.1-8B-Instruct"``.
    device_map:
        Passed to ``transformers.pipeline``.  ``"auto"`` spreads across
        all available GPUs/CPU automatically.
    load_in_8bit:
        Enable 8-bit quantisation (requires ``bitsandbytes``).
        Reduces VRAM usage roughly by half.
    max_new_tokens:
        Maximum tokens to generate per call.
    """

    def __init__(
        self,
        model_name: str,
        device_map: str = "auto",
        load_in_8bit: bool = False,
        max_new_tokens: int = 512,
    ) -> None:
        try:
            import torch
            from transformers import pipeline, BitsAndBytesConfig
        except ImportError as exc:
            raise ImportError(
                "HuggingFaceBackend requires: pip install transformers accelerate"
                + (" bitsandbytes" if load_in_8bit else "")
            ) from exc

        model_kwargs: Dict[str, Any] = {"torch_dtype": torch.float16}
        if load_in_8bit:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)

        self._pipe = pipeline(
            "text-generation",
            model=model_name,
            device_map=device_map,
            model_kwargs=model_kwargs,
        )
        self.max_new_tokens = max_new_tokens

    def call(self, prompt: str) -> str:
        result = self._pipe(
            prompt,
            max_new_tokens=self.max_new_tokens,
            do_sample=False,
            return_full_text=False,
        )
        return result[0]["generated_text"]


# ---------------------------------------------------------------------------
# AnthropicBackend
# ---------------------------------------------------------------------------

class AnthropicBackend(LLMBackend):
    """Wraps the Anthropic SDK client (same as main.py uses).

    Parameters
    ----------
    client:
        ``anthropic.Anthropic`` instance.
    model:
        Claude model ID, e.g. ``"claude-sonnet-4-6"``.
    max_tokens:
        Maximum tokens in the response.
    temperature:
        Sampling temperature (0.0 = deterministic).
    """

    def __init__(
        self,
        client: Any,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> None:
        self._client = client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def call(self, prompt: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_backend(cfg: Dict[str, Any]) -> Optional[LLMBackend]:
    """Create an LLMBackend from a config dict.

    Parameters
    ----------
    cfg:
        Dict with at minimum a ``backend`` key.  Remaining keys are
        forwarded to the selected backend constructor.

        backend: "ollama" | "huggingface" | "anthropic" | "mock"
        model:   model name / HF path (required for all except "mock")

        Ollama-specific:
            ollama_url:     server URL (default http://localhost:11434)
            timeout_s:      request timeout seconds (default 120)

        HuggingFace-specific:
            device_map:     "auto" | "cpu" | "cuda" (default "auto")
            load_in_8bit:   bool (default False)
            max_new_tokens: int (default 512)

        Anthropic-specific:
            api_key:        Anthropic API key (or set ANTHROPIC_API_KEY env var)

    Returns
    -------
    An LLMBackend instance, or None for "mock" (AgentNode uses its built-in
    rule-based mock planner when llm_client is None).
    """
    backend = cfg.get("backend", "mock")

    if backend == "ollama":
        return OllamaBackend(
            model=cfg.get("model", "llama3.1:8b"),
            base_url=cfg.get("ollama_url", "http://localhost:11434"),
            timeout_s=cfg.get("timeout_s", 120),
        )

    if backend == "huggingface":
        return HuggingFaceBackend(
            model_name=cfg["model"],
            device_map=cfg.get("device_map", "auto"),
            load_in_8bit=cfg.get("load_in_8bit", False),
            max_new_tokens=cfg.get("max_new_tokens", 512),
        )

    if backend == "anthropic":
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("AnthropicBackend requires: pip install anthropic") from exc
        import os
        api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=api_key)
        return AnthropicBackend(
            client=client,
            model=cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=cfg.get("max_tokens", 2048),
            temperature=cfg.get("temperature", 0.2),
        )

    if backend == "mock":
        return None  # AgentNode._mock_plan() is used when llm_client is None

    raise ValueError(
        f"Unknown backend '{backend}'. "
        f"Choose: 'ollama', 'huggingface', 'anthropic', 'mock'."
    )
