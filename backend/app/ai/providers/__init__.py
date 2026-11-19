"""Адаптер LLM-провайдеров (OpenAI-совместимый API).

Единый контракт: complete_json() возвращает сырой текст + usage ДО валидации,
чтобы заплаченные токены учитывались даже при невалидном ответе.

Режимы структурированного вывода:
- openai / groq: response_format=json_schema (strict), схема из pydantic;
- deepseek: только response_format=json_object (строгой схемы нет), поэтому
  JSON Schema кладётся в system prompt, а ответ валидируется pydantic.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from app.ai.errors import ProviderConfigError
from app.core.config import settings


@dataclass
class LLMUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LLMCall:
    text: str
    usage: LLMUsage
    provider: str
    model: str
    response_model: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None) or {}
        value = extra.get(name)
    return value


def extract_usage(usage: Any) -> LLMUsage:
    if usage is None:
        return LLMUsage()
    prompt = _get(usage, "prompt_tokens") or 0
    completion = _get(usage, "completion_tokens") or 0
    # DeepSeek: prompt_cache_hit_tokens; OpenAI: prompt_tokens_details.cached_tokens
    cached = _get(usage, "prompt_cache_hit_tokens")
    if cached is None:
        cached = _get(_get(usage, "prompt_tokens_details"), "cached_tokens")
    reasoning = _get(_get(usage, "completion_tokens_details"), "reasoning_tokens")
    return LLMUsage(
        input_tokens=int(prompt),
        cached_input_tokens=int(cached or 0),
        output_tokens=int(completion),
        reasoning_tokens=int(reasoning or 0),
        total_tokens=int(_get(usage, "total_tokens") or prompt + completion),
    )


@dataclass
class AIProvider:
    name: str
    client: OpenAI
    model: str

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> LLMCall:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": settings.ai_max_output_tokens,
        }

        if self.name == "deepseek":
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            system = (
                f"{system_prompt}\n\n"
                "ФОРМАТ ОТВЕТА: верни ТОЛЬКО один JSON-объект (json), строго соответствующий "
                "этой JSON Schema. Без markdown, без пояснений, без лишних полей.\n"
                f"JSON Schema:\n{schema_json}"
            )
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["extra_body"] = {
                "thinking": {"type": settings.deepseek_thinking}
            }
        else:
            system = system_prompt
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": to_strict_json_schema(schema),
                    "strict": True,
                },
            }

        started = time.monotonic()
        raw = self.client.chat.completions.with_raw_response.create(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            **kwargs,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        response = raw.parse()

        choice = response.choices[0] if response.choices else None
        return LLMCall(
            text=(choice.message.content or "") if choice else "",
            usage=extract_usage(response.usage),
            provider=self.name,
            model=self.model,
            response_model=getattr(response, "model", None),
            request_id=raw.headers.get("x-request-id") or getattr(response, "id", None),
            finish_reason=choice.finish_reason if choice else None,
            latency_ms=latency_ms,
        )


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
    elif provider_name == "deepseek":
        api_key = settings.deepseek_api_key
        model = settings.deepseek_model
        base_url = settings.deepseek_base_url
    else:
        raise ProviderConfigError(
            "AI_PROVIDER must be one of: 'groq', 'openai', 'deepseek'"
        )

    if not api_key:
        raise ProviderConfigError(
            f"{provider_name.upper()}_API_KEY is not configured"
        )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=settings.ai_timeout_seconds,
        # Повторы делает очередь: так каждый запрос попадает в ai_usage,
        # а lease воркера не истекает из-за скрытых ретраев SDK.
        max_retries=0,
    )

    return AIProvider(name=provider_name, client=client, model=model)
