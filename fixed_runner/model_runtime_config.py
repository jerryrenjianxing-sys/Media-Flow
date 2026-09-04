from __future__ import annotations


OPENROUTER_PRIMARY_MODEL = "z-ai/glm-5.3-flash"
OPENROUTER_FALLBACK_MODELS: tuple[str, ...] = ()


def fallback_models_env() -> str:
    return ",".join(OPENROUTER_FALLBACK_MODELS)
