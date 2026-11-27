"""Offline model capability lookup from the bundled LiteLLM snapshot."""

import json
from copy import deepcopy
from pathlib import Path

_SNAPSHOT = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
SOURCE = _SNAPSHOT["source"]
MODEL_CAPABILITIES = _SNAPSHOT["models"]


def get_model_capabilities(model: str) -> dict:
    name = model
    if name not in MODEL_CAPABILITIES:
        prefix, separator, suffix = name.partition("/")
        if separator and prefix in {"group", "openai", "chatgpt-api", "anthropic", "claude"}:
            name = suffix
    capabilities = {
        "context_window": None,
        "max_input_tokens": None,
        "max_output_tokens": None,
        "supported_modalities": None,
        "supported_output_modalities": None,
        "supports_reasoning": None,
        "max_reasoning_tokens": None,
        **deepcopy(MODEL_CAPABILITIES.get(name, {})),
    }
    return {
        "model": model,
        "lookup_model": name if name in MODEL_CAPABILITIES else None,
        "source": SOURCE,
        "snapshot_date": _SNAPSHOT["snapshot_date"],
        **capabilities,
    }


def model_capabilities_prompt(model: str) -> str:
    return (
        "## Model capabilities\n\n"
        "Reference metadata, not guaranteed gateway limits. null or missing means unknown, not unsupported. "
        "max_input_tokens is the source's input limit, not a verified total context window; do not add input "
        "and output limits to infer one. Reasoning effort is not a token budget. "
        "Provider-prefixed names may use the base model entry; gateway overrides are not known.\n"
        + json.dumps(get_model_capabilities(model), ensure_ascii=False, sort_keys=True)
    )
