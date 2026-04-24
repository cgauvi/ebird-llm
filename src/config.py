"""
config.py — HuggingFace model catalog for the eBird birding assistant.

All models are served through the HuggingFace Inference API and must
support tool/function calling.

Selecting a model
-----------------
Set HF_MODEL_ID in your .env file to either:
  • A short alias from the MODELS catalog below  (e.g.  qwen2.5-72b)
  • A full HuggingFace repo ID                   (e.g.  Qwen/Qwen2.5-72B-Instruct)

If HF_MODEL_ID is unset, DEFAULT_MODEL_ALIAS is used.
"""

import os
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    """Metadata for a single HuggingFace model."""

    repo_id: str
    description: str
    context_window: int = 32_768
    notes: str = ""


# Short alias → ModelConfig
MODELS: dict[str, ModelConfig] = {
    # ---- Qwen 2.5 ----
    "qwen2.5-72b": ModelConfig(
        repo_id="Qwen/Qwen2.5-72B-Instruct",
        description="Qwen 2.5 72B — best quality, recommended default",
        context_window=131_072,
    ),
    # ---- Google Gemma ----
    "gemma-3-27b": ModelConfig(
        repo_id="google/gemma-3-27b-it",
        description="Google Gemma 3 27B — best Gemma quality, multimodal",
        context_window=131_072,
        notes="Requires accepting Google licence on HuggingFace.",
    ),
    # ---- OpenAI GPT-OSS ----
    "gpt-oss-120b": ModelConfig(
        repo_id="openai/gpt-oss-120b",
        description="OpenAI GPT-OSS 120B — high reasoning, fits on single 80GB GPU (117B params, 5.1B active)",
        context_window=131_072,
        notes="Uses harmony response format; requires chat template for correct output.",
    ),
    "gpt-oss-20b": ModelConfig(
        repo_id="openai/gpt-oss-20b",
        description="OpenAI GPT-OSS 20B — lower latency, fits within 16GB memory (21B params, 3.6B active)",
        context_window=131_072,
        notes="Uses harmony response format; requires chat template for correct output.",
    ),
}

DEFAULT_MODEL_ALIAS = "gpt-oss-120b"


def resolve_model(hf_model_id: str | None = None) -> tuple[str, ModelConfig | None]:
    """Resolve HF_MODEL_ID (alias or full repo_id) to a (repo_id, ModelConfig|None) pair.

    Args:
        hf_model_id: Value of HF_MODEL_ID env var, an alias, or None.
            If None, reads HF_MODEL_ID from the environment;
            falls back to DEFAULT_MODEL_ALIAS.

    Returns:
        (repo_id, cfg) where cfg is the ModelConfig if the alias was found,
        or None if a raw repo_id was supplied that isn't in the catalog.
    """
    raw = hf_model_id or os.environ.get("HF_MODEL_ID", DEFAULT_MODEL_ALIAS)
    if raw in MODELS:
        cfg = MODELS[raw]
        return cfg.repo_id, cfg
    # Treat as a raw repo_id (e.g. "org/ModelName")
    return raw, None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_llm(hf_model_id: str | None = None) -> BaseChatModel:
    """Return a ChatHuggingFace instance for the requested model.

    Args:
        hf_model_id: Short alias from MODELS or a full HuggingFace repo ID.
            If None, reads HF_MODEL_ID from the environment;
            defaults to DEFAULT_MODEL_ALIAS ('qwen2.5-72b').

    Returns:
        A BaseChatModel instance ready for create_tool_calling_agent().

    Raises:
        EnvironmentError: HUGGINGFACE_API_TOKEN is not set.
    """
    token = os.environ.get("HUGGINGFACE_API_TOKEN", "")
    if not token:
        raise EnvironmentError(
            "HUGGINGFACE_API_TOKEN is not set. "
            "Copy .env.example to .env and fill in your token."
        )

    repo_id, _ = resolve_model(hf_model_id)

    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint  # noqa: PLC0415

    endpoint = HuggingFaceEndpoint(
        repo_id=repo_id,
        task="text-generation",
        huggingfacehub_api_token=token,
        max_new_tokens=2048,
        temperature=0.1,
    )
    return ChatHuggingFace(llm=endpoint, verbose=False)

