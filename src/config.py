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
    # ---- Llama 3.x ----
    "llama3.3-70b": ModelConfig(
        repo_id="meta-llama/Llama-3.3-70B-Instruct",
        description="Meta Llama 3.3 70B — strong reasoning and tool calling",
        context_window=131_072,
        notes="Requires accepting Meta licence on HuggingFace.",
    ),
    # ---- Mistral ----
    "mistral-small-3.1": ModelConfig(
        repo_id="mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        description="Mistral Small 3.1 24B — latest Mistral with vision support",
        context_window=131_072,
    ),
    # ---- DeepSeek ----
    "deepseek-r1-distill-qwen-32b": ModelConfig(
        repo_id="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        description="DeepSeek R1 distilled on Qwen 32B — strong reasoning",
        context_window=131_072,
    ),
    # ---- Microsoft Phi (GPT-OSS) ----
    "phi-4": ModelConfig(
        repo_id="microsoft/Phi-4",
        description="Microsoft Phi-4 14B — top-tier small model, excellent tool calling",
        context_window=16_384,
        notes="Requires accepting Microsoft licence on HuggingFace.",
    ),
    # ---- Google Gemma ----
    "gemma-3-27b": ModelConfig(
        repo_id="google/gemma-3-27b-it",
        description="Google Gemma 3 27B — best Gemma quality, multimodal",
        context_window=131_072,
        notes="Requires accepting Google licence on HuggingFace.",
    ),
}

DEFAULT_MODEL_ALIAS = "qwen2.5-72b"


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

