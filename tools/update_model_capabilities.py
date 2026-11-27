"""Regenerate the bundled LiteLLM capability snapshot (no runtime network access)."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

SOURCE = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
DESTINATION = Path(__file__).resolve().parents[1] / "core" / "model_capabilities.json"
FIELDS = {
    "litellm_provider",
    "mode",
    "context_window",
    "max_input_tokens",
    "max_output_tokens",
    "max_reasoning_tokens",
    "reasoning_effort_levels",
    "default_reasoning_effort",
    "reasoning_contexts",
    "reasoning_modes",
}


def make_snapshot(raw: bytes) -> dict:
    source = json.loads(raw)
    models = {
        name: {
            key: value for key, value in entry.items() if key in FIELDS or key.startswith(("supports_", "supported_"))
        }
        for name, entry in source.items()
        if name != "sample_spec" and isinstance(entry, dict)
    }
    if not models:
        raise ValueError("LiteLLM source contains no models")
    return {
        "source": SOURCE,
        "snapshot_date": datetime.now(UTC).date().isoformat(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "models": models,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, help="Use a previously downloaded LiteLLM JSON file.")
    args = parser.parse_args()
    if args.source_file:
        raw = args.source_file.read_bytes()
    else:
        with urlopen(SOURCE, timeout=60) as response:
            raw = response.read()
    snapshot = make_snapshot(raw)
    temporary = DESTINATION.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(DESTINATION)
    print(f"Updated {len(snapshot['models'])} model entries in {DESTINATION}")


if __name__ == "__main__":
    main()
