"""HuggingFace Transformers local LLM client.

Implements the same ``call(prompt: str) -> str`` interface expected by
``AgentNode`` (and passed to ``AstroPlan`` as ``llm_client=``), so any
chat-capable model on HuggingFace Hub — including Gemma and Qwen families —
can replace the default Anthropic backend without any other code changes.

Requirements (installed separately from base AstroPlan deps)::

    pip install transformers>=4.40.0 accelerate>=0.30.0 torch>=2.2.0
    pip install bitsandbytes>=0.43.0   # only needed for 4-bit / 8-bit quant

Example::

    from src.llm import HFLocalClient
    client = HFLocalClient("Qwen/Qwen3-4B", load_in_4bit=True)
    print(client.call("Say hello in JSON"))
"""
from __future__ import annotations

from typing import Optional


class HFLocalClient:
    """Local inference via HuggingFace Transformers text-generation pipeline.

    Parameters
    ----------
    model_name_or_path:
        HuggingFace model ID (e.g. ``"Qwen/Qwen3-4B"``) or an absolute path
        to a locally cached model directory.
    max_new_tokens:
        Upper bound on tokens generated per ``call()``.
    device:
        ``"auto"`` lets ``accelerate`` pick the best device; pass ``"cuda:0"``
        or ``"cpu"`` to force a specific device.
    load_in_4bit:
        Enable NF4 quantization via ``bitsandbytes``.  Reduces GPU memory by
        ~75 %.  Requires a CUDA device.
    load_in_8bit:
        Enable LLM.int8() quantization.  Reduces GPU memory by ~50 %.
        Mutually exclusive with ``load_in_4bit``; 4-bit takes precedence.
    temperature:
        Sampling temperature.  Set to 0 for greedy decoding.
    """

    def __init__(
        self,
        model_name_or_path: str,
        max_new_tokens: int = 512,
        device: str = "auto",
        load_in_4bit: bool = False,
        load_in_8bit: bool = False,
        temperature: float = 0.2,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as exc:
            raise ImportError(
                "Local inference requires additional packages.\n"
                "Run: pip install transformers>=4.40.0 accelerate>=0.30.0 "
                "torch>=2.2.0 bitsandbytes>=0.43.0"
            ) from exc

        print(f"[HFLocalClient] Loading '{model_name_or_path}' …")

        quant_cfg: Optional[object] = None
        if load_in_4bit:
            quant_cfg = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
        elif load_in_8bit:
            quant_cfg = BitsAndBytesConfig(load_in_8bit=True)

        self._tokenizer = AutoTokenizer.from_pretrained(
            model_name_or_path,
            trust_remote_code=True,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            model_name_or_path,
            device_map=device,
            torch_dtype="auto",
            quantization_config=quant_cfg,
            trust_remote_code=True,
        )
        self._max_new_tokens = max_new_tokens
        self._temperature = temperature
        print(f"[HFLocalClient] Ready  model={model_name_or_path}  device={device}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(self, prompt: str) -> str:
        """Run one inference pass and return the assistant reply as a string.

        If the tokenizer ships a chat template (Gemma-IT / Qwen-Instruct all
        do), the prompt is wrapped into a proper chat message before
        tokenisation.  Plain-text fallback is used otherwise.

        The method strips leading/trailing whitespace and removes any
        ``<think>…</think>`` reasoning block that Qwen3 thinking-mode models
        emit before the actual JSON answer.
        """
        import torch

        # Apply chat template when available (all instruct models carry one).
        if (
            hasattr(self._tokenizer, "apply_chat_template")
            and self._tokenizer.chat_template
        ):
            messages = [{"role": "user", "content": prompt}]
            input_text: str = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            input_text = prompt

        inputs = self._tokenizer(input_text, return_tensors="pt").to(
            self._model.device
        )

        do_sample = self._temperature > 0.0
        gen_kwargs: dict = {
            "max_new_tokens": self._max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": self._tokenizer.eos_token_id,
        }
        if do_sample:
            gen_kwargs["temperature"] = self._temperature

        with torch.inference_mode():
            output_ids = self._model.generate(**inputs, **gen_kwargs)

        # Decode only the newly generated tokens (not the echoed prompt).
        new_ids = output_ids[0][inputs["input_ids"].shape[1]:]
        text = self._tokenizer.decode(new_ids, skip_special_tokens=True).strip()

        # Strip Qwen3 thinking-mode <think>…</think> prefix if present.
        text = _strip_thinking_block(text)

        return text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_thinking_block(text: str) -> str:
    """Remove <think>…</think> prefix produced by Qwen3 thinking-mode models.

    The planner expects a JSON object.  Any reasoning preamble before it is
    discarded so ``AgentNode._parse_llm_response`` can locate the ``{`` token.
    """
    import re
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
