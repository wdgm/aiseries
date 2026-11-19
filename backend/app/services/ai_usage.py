"""Учёт стоимости: каждый запрос к LLM → строка ai_usage с ценой на момент запроса."""

import uuid
from datetime import datetime, time, timezone
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.providers import LLMCall, LLMUsage
from app.models import AIUsage, ProviderPricing

MILLION = Decimal(1_000_000)

# DeepSeek: пик 01:00–04:00 и 06:00–10:00 UTC, пн–пт. Китайские праздники
# (там весь день off-peak) не учитываются → в эти дни оценка завышена.
_DEEPSEEK_PEAK_WINDOWS = ((time(1), time(4)), (time(6), time(10)))


def pricing_tier(provider: str, at: datetime) -> str:
    if provider != "deepseek":
        return "standard"
    at = at.astimezone(timezone.utc)
    if at.weekday() >= 5:
        return "off_peak"
    t = at.time()
    for start, end in _DEEPSEEK_PEAK_WINDOWS:
        if start <= t < end:
            return "peak"
    return "off_peak"


def find_pricing(
    db: Session, provider: str, model: str, tier: str, at: datetime
) -> ProviderPricing | None:
    return db.scalar(
        select(ProviderPricing)
        .where(
            ProviderPricing.provider == provider,
            ProviderPricing.model == model,
            ProviderPricing.tier == tier,
            ProviderPricing.valid_from <= at,
            or_(ProviderPricing.valid_to.is_(None), ProviderPricing.valid_to > at),
        )
        .order_by(ProviderPricing.valid_from.desc())
        .limit(1)
    )


def compute_cost(usage: LLMUsage, price: ProviderPricing) -> Decimal:
    cached = min(usage.cached_input_tokens, usage.input_tokens)
    miss = usage.input_tokens - cached
    hit_price = (
        price.input_cache_hit_per_1m
        if price.input_cache_hit_per_1m is not None
        else price.input_cache_miss_per_1m
    )
    # reasoning-токены уже входят в output_tokens и тарифицируются как output.
    return (
        Decimal(miss) * Decimal(price.input_cache_miss_per_1m)
        + Decimal(cached) * Decimal(hit_price)
        + Decimal(usage.output_tokens) * Decimal(price.output_per_1m)
    ) / MILLION


def record_usage(
    db: Session,
    *,
    operation: str,
    provider: str,
    model: str,
    status: str,
    call: LLMCall | None = None,
    job_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    book_id: uuid.UUID | None = None,
    error_class: str | None = None,
    error: str | None = None,
    latency_ms: int | None = None,
) -> AIUsage:
    """Добавляет строку в сессию (commit — на стороне вызывающего)."""
    now = datetime.now(timezone.utc)
    usage = call.usage if call else LLMUsage()
    tier = pricing_tier(provider, now)

    cost = None
    price = None
    if call is not None and usage.total_tokens > 0:
        price = find_pricing(db, provider, model, tier, now)
        if price is not None:
            cost = compute_cost(usage, price)
    elif usage.total_tokens == 0:
        cost = Decimal(0)

    row = AIUsage(
        job_id=job_id,
        task_id=task_id,
        book_id=book_id,
        operation=operation,
        provider=provider,
        model=model,
        response_model=call.response_model if call else None,
        request_id=call.request_id if call else None,
        status=status,
        error_class=error_class,
        error=(error or None) and error[:4000],
        finish_reason=call.finish_reason if call else None,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=usage.total_tokens,
        pricing_tier=tier,
        pricing_id=price.id if price else None,
        cost_usd=cost,
        latency_ms=call.latency_ms if call else latency_ms,
        created_at=now,
    )
    db.add(row)
    return row
