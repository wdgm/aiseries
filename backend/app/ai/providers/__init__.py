from dataclasses import dataclass

from openai import OpenAI

from app.core.config import settings


@dataclass
class AIProvider:
    name: str
    client: OpenAI
    model: str


def create_ai_provider() -> AIProvider:
    provider_name = settings.ai_provider.strip().lower()

    if provider_name == "groq":
        api_key = settings.groq_api_key
        model = settings.groq_model
        base_url = "https://api.groq.com/openai/v1"
    elif provider_name == "openai":
        api_key = settings.openai_api_key
        model = settings.openai_model
        base_url = None
    else:
        raise ValueError(
            "AI_PROVIDER must be either 'groq' or 'openai'"
        )

    if not api_key:
        raise RuntimeError(
            f"{provider_name.upper()}_API_KEY is not configured"
        )

    if base_url is not None:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.ai_timeout_seconds,
        )
    else:
        client = OpenAI(
            api_key=api_key,
            timeout=settings.ai_timeout_seconds,
        )

    return AIProvider(
        name=provider_name,
        client=client,
        model=model,
    )