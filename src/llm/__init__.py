"""src/llm — local inference clients.

Exports
-------
HFLocalClient
    HuggingFace Transformers backend; satisfies call(prompt) -> str.
"""
from src.llm.hf_client import HFLocalClient

__all__ = ["HFLocalClient"]
