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
        self._model = _load_model(
            model_name_or_path,
            device_map=device,
            quant_cfg=quant_cfg,
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

def _load_model(model_name_or_path: str, *, device_map: str, quant_cfg):
    """Load a causal LM, automatically upgrading *transformers* if the architecture
    is not recognised by the currently installed version.

    Loading strategy (each stage only runs if the previous one fails with
    "does not recognize this architecture"):

    1. Standard ``AutoModelForCausalLM.from_pretrained`` with
       ``trust_remote_code=True`` — works for all models whose architecture is
       already known to the installed transformers.

    2. Dynamic Hub module loading — for models that ship a custom
       ``modeling_*.py`` in their Hub repo (e.g. older Qwen community uploads).
       Uses ``auto_map`` from the model config to locate the class.

    3. ``pip install --upgrade transformers`` (stable PyPI) then reload and
       retry — covers newly released model types like ``gemma4`` (needs
       transformers ≥ 5.5.0) and ``qwen3`` that land in stable releases shortly
       after the model is published.

    4. ``pip install git+https://github.com/huggingface/transformers`` (dev HEAD)
       then reload and retry — for architectures that exist in the dev branch
       but have not yet been cut into a stable release.

    5. If all four stages fail, raise ``RuntimeError`` with a full diagnostic
       (installed version, detected model type, exact pip commands to try).
    """
    import sys

    _common_kwargs = dict(
        device_map=device_map,
        torch_dtype="auto",
        quantization_config=quant_cfg,
        trust_remote_code=True,
    )

    def _fresh_load():
        """Import AutoModelForCausalLM fresh (picks up any in-process upgrade)."""
        from transformers import AutoModelForCausalLM as _Auto
        return _Auto.from_pretrained(model_name_or_path, **_common_kwargs)

    def _purge_transformers_cache():
        """Remove all transformers sub-modules from sys.modules so the next
        import picks up the newly installed wheel."""
        for key in list(sys.modules.keys()):
            if key == "transformers" or key.startswith("transformers."):
                del sys.modules[key]

    def _pip_install(spec: str) -> bool:
        """Run pip install *spec* quietly; return True on success."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", spec],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[HFLocalClient] pip install '{spec}' failed:\n{result.stderr.strip()}")
            return False
        return True

    def _detect_model_type() -> str:
        try:
            from transformers import AutoConfig
            cfg = AutoConfig.from_pretrained(model_name_or_path, trust_remote_code=True)
            return getattr(cfg, "model_type", "unknown")
        except Exception:
            return "unknown"

    # ── Stage 1: standard Auto routing ───────────────────────────────────
    try:
        return _fresh_load()
    except ValueError as exc1:
        if "does not recognize this architecture" not in str(exc1):
            raise
        first_exc = exc1

    # ── Stage 2: dynamic Hub module (auto_map in model config) ───────────
    print(
        f"[HFLocalClient] Architecture not recognised in installed transformers; "
        "trying dynamic Hub module …"
    )
    try:
        from transformers import AutoConfig
        from transformers.dynamic_module_utils import get_class_from_dynamic_module

        config = AutoConfig.from_pretrained(model_name_or_path, trust_remote_code=True)
        auto_map: dict = getattr(config, "auto_map", {})
        cls_path = auto_map.get("AutoModelForCausalLM")
        if not cls_path:
            raise ValueError("No AutoModelForCausalLM in auto_map — skipping stage 2.")
        model_cls = get_class_from_dynamic_module(cls_path, model_name_or_path)
        return model_cls.from_pretrained(model_name_or_path, **_common_kwargs)
    except Exception as exc2:
        print(f"[HFLocalClient] Dynamic Hub module failed: {exc2}")

    # ── Stage 3: upgrade transformers (stable PyPI) and retry ─────────────
    import transformers as _tf_before
    print(
        f"[HFLocalClient] Attempting 'pip install --upgrade transformers' "
        f"(current: {_tf_before.__version__}) …"
    )
    if _pip_install("--upgrade transformers"):
        _purge_transformers_cache()
        try:
            return _fresh_load()
        except ValueError as exc3:
            if "does not recognize this architecture" not in str(exc3):
                raise
            print("[HFLocalClient] Stable upgrade insufficient; trying dev branch …")
    else:
        print("[HFLocalClient] Stable upgrade failed; trying dev branch …")

    # ── Stage 4: upgrade transformers from git HEAD and retry ─────────────
    _GIT_SOURCE = "git+https://github.com/huggingface/transformers.git"
    print(f"[HFLocalClient] Attempting 'pip install {_GIT_SOURCE}' …")
    if _pip_install(_GIT_SOURCE):
        _purge_transformers_cache()
        try:
            return _fresh_load()
        except ValueError as exc4:
            if "does not recognize this architecture" not in str(exc4):
                raise
            print("[HFLocalClient] Dev-branch install also did not resolve the architecture.")

    # ── All stages exhausted ──────────────────────────────────────────────
    model_type = _detect_model_type()
    try:
        _purge_transformers_cache()
        import transformers as _tf_after
        installed_ver = _tf_after.__version__
    except Exception:
        installed_ver = "(unknown — import failed after upgrade attempt)"

    raise RuntimeError(
        f"Cannot load model '{model_name_or_path}' (model_type='{model_type}').\n"
        f"transformers version after upgrade attempts : {installed_ver}\n"
        f"\n"
        f"Known minimum requirements:\n"
        f"  gemma4  →  transformers >= 5.5.0\n"
        f"  qwen3   →  transformers >= 4.51.0\n"
        f"\n"
        f"Manual fix options:\n"
        f"  pip install --upgrade transformers\n"
        f"  pip install git+https://github.com/huggingface/transformers.git\n"
    ) from first_exc


def _strip_thinking_block(text: str) -> str:
    """Remove <think>…</think> prefix produced by Qwen3 thinking-mode models.

    The planner expects a JSON object.  Any reasoning preamble before it is
    discarded so ``AgentNode._parse_llm_response`` can locate the ``{`` token.
    """
    import re
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
