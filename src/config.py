"""Configuration loaded from the environment.

Reads from a local .env file if present (real environment variables take
precedence). See .env.example for the supported variables.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # no-op if there is no .env file

# Default to a small model on purpose: factual grounding comes from the
# Wikipedia tool, not the model's parametric knowledge, so a larger model's
# broader recall is a liability (and a cost) rather than a benefit here.
# Override with ANTHROPIC_MODEL when a task genuinely needs more capability.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "1024"))

# Temperature is optional and only sent to the API when explicitly set.
# The default model (claude-opus-4-8, and the 4.7 / Fable families) rejects
# `temperature` with a 400 error, so leaving this unset keeps the default path
# working. Set it only when using a model that accepts sampling parameters.
_temperature = os.environ.get("ANTHROPIC_TEMPERATURE")
TEMPERATURE = float(_temperature) if _temperature not in (None, "") else None


def get_api_key() -> str:
    """Return the Anthropic API key, or raise a clear error if it is missing."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your "
            "key, or export ANTHROPIC_API_KEY in your shell."
        )
    return key
