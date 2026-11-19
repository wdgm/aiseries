from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.errors import ErrorClass, InvalidOutputError, backoff_seconds, classify
from app.ai.providers import LLMCall, LLMUsage, extract_usage
from app.services.ai_usage import compute_cost, pricing_tier
from app.services.book_analysis.analyzer import parse_analysis
from app.services.book_import.chunking import split_into_chunks
from app.services.book_import.integrity import IntegrityResult, check_chapter_chunks


def _status_error(cls, status):
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    resp = httpx.Response(status, request=req)
    return cls("err", response=resp, body=None)


# ------------------------------------------------------------- chunking


@pytest.mark.parametrize("size,overlap", [(8000, 500), (300, 50), (100, 10)])
def test_chunk_positions_match_source(size, overlap):
    text = "\n\n  " + ("Первый абзац. Второе предложение тут.\n\n" * 200) + "  "
    chunks = split_into_chunks(text, chunk_size=size, overlap=overlap)
    assert chunks
    for c in chunks:
        assert text[c.char_start:c.char_end] == c.content
    result = IntegrityResult()
    check_chapter_chunks(1, text, chunks, result)
    assert result.ok, result.errors


def test_integrity_detects_gap():
    text = "Ааа. " * 100
    chunks = split_into_chunks(text, chunk_size=100, overlap=10)
    del chunks[1]
    result = IntegrityResult()
    check_chapter_chunks(1, text, chunks, result)
    assert not result.ok


# --------------------------------------------------------------- errors


def test_classify():
    assert classify(_status_error(openai.RateLimitError, 429)) == ErrorClass.RATE_LIMIT
    assert classify(_status_error(openai.AuthenticationError, 401)) == ErrorClass.AUTH
    assert classify(_status_error(openai.APIStatusError, 402)) == ErrorClass.BILLING
    assert classify(_status_error(openai.InternalServerError, 503)) == ErrorClass.TRANSIENT
    assert classify(_status_error(openai.BadRequestError, 400)) == ErrorClass.BAD_REQUEST
    assert classify(openai.APITimeoutError(httpx.Request("POST", "https://x"))) == ErrorClass.TRANSIENT
    assert classify(InvalidOutputError("x")) == ErrorClass.INVALID_OUTPUT
    assert backoff_seconds(ErrorClass.RATE_LIMIT, 1) == 30
    assert backoff_seconds(ErrorClass.RATE_LIMIT, 10) == 900


def test_parse_analysis():
    ok = LLMCall(text='```json\n{"characters": [], "notes": []}\n```', usage=LLMUsage(), provider="d", model="m")
    assert parse_analysis(ok).characters == []
    with pytest.raises(InvalidOutputError):
        parse_analysis(LLMCall(text='{"characters": [', usage=LLMUsage(), provider="d", model="m"))
    with pytest.raises(InvalidOutputError):
        parse_analysis(LLMCall(text="{}", usage=LLMUsage(), provider="d", model="m", finish_reason="length"))


# -------------------------------------------------------------- pricing


def test_deepseek_peak_tier():
    # четверг 2026-11-19
    assert pricing_tier("deepseek", datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)) == "peak"
    assert pricing_tier("deepseek", datetime(2026, 11, 19, 5, 0, tzinfo=timezone.utc)) == "off_peak"
    assert pricing_tier("deepseek", datetime(2026, 11, 19, 9, 59, tzinfo=timezone.utc)) == "peak"
    assert pricing_tier("deepseek", datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)) == "off_peak"
    assert pricing_tier("deepseek", datetime(2026, 11, 21, 2, 0, tzinfo=timezone.utc)) == "off_peak"  # суббота
    assert pricing_tier("openai", datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)) == "standard"


def test_cost_with_cache():
    price = SimpleNamespace(
        input_cache_miss_per_1m=Decimal("0.3"),
        input_cache_hit_per_1m=Decimal("0.006"),
        output_per_1m=Decimal("1.2"),
    )
    usage = LLMUsage(input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=500_000)
    assert compute_cost(usage, price) == Decimal("0.18") + Decimal("0.0024") + Decimal("0.6")


def test_extract_deepseek_usage():
    from openai.types import CompletionUsage

    u = CompletionUsage.model_validate(
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
         "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36}
    )
    usage = extract_usage(u)
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (100, 64, 20)
