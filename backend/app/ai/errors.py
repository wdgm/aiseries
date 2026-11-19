"""Классификация ошибок для решения: повторить / поставить задачу на паузу / сдаться."""

import json
from enum import Enum

import openai
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError, SQLAlchemyError


class ErrorClass(str, Enum):
    TRANSIENT = "transient"            # сеть, таймаут, 5xx, потеря соединения с БД
    RATE_LIMIT = "rate_limit"          # 429
    INVALID_OUTPUT = "invalid_output"  # невалидный JSON / не прошёл pydantic / обрезан по length
    AUTH = "auth"                      # 401/403 — чинить ключ
    BILLING = "billing"                # 402 — пополнить баланс
    BAD_REQUEST = "bad_request"        # 400/422 — повтор не поможет
    CONTEXT_LENGTH = "context_length"  # вход слишком длинный
    PERSISTENCE = "persistence"        # ошибка записи в БД
    CONFIG = "config"                  # нет ключа / неверный AI_PROVIDER
    DATA = "data"                      # исходные данные отсутствуют/повреждены
    LEASE_EXPIRED = "lease_expired"    # воркер умер посреди задачи
    UNKNOWN = "unknown"


# Повторяем с backoff, пока не исчерпан max_attempts.
RETRYABLE = {
    ErrorClass.TRANSIENT,
    ErrorClass.RATE_LIMIT,
    ErrorClass.INVALID_OUTPUT,
    ErrorClass.PERSISTENCE,
    ErrorClass.LEASE_EXPIRED,
    ErrorClass.UNKNOWN,
}

# Ошибка не про фрагмент, а про всю среду: ставим задачу на паузу, попытка
# не списывается. После исправления — POST /analysis-jobs/{id}/resume.
PAUSE_JOB = {ErrorClass.AUTH, ErrorClass.BILLING, ErrorClass.CONFIG}


class InvalidOutputError(Exception):
    """LLM вернула ответ, но его нельзя использовать."""


class ProviderConfigError(RuntimeError):
    pass


class TaskDataError(Exception):
    pass


_TRANSIENT_STATUSES = {408, 409, 500, 502, 503, 504}


def classify(exc: BaseException) -> ErrorClass:
    if isinstance(exc, (InvalidOutputError, ValidationError, json.JSONDecodeError)):
        return ErrorClass.INVALID_OUTPUT
    if isinstance(exc, ProviderConfigError):
        return ErrorClass.CONFIG
    if isinstance(exc, TaskDataError):
        return ErrorClass.DATA

    # Порядок важен: RateLimitError ⊂ APIStatusError, APITimeoutError ⊂ APIConnectionError.
    if isinstance(exc, openai.RateLimitError):
        return ErrorClass.RATE_LIMIT
    if isinstance(exc, openai.APIConnectionError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ErrorClass.AUTH
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status == 402:
            return ErrorClass.BILLING
        if status in _TRANSIENT_STATUSES:
            return ErrorClass.TRANSIENT
        if status in (400, 413, 422):
            msg = str(exc).lower()
            if "context" in msg or "maximum" in msg or "too long" in msg or status == 413:
                return ErrorClass.CONTEXT_LENGTH
            return ErrorClass.BAD_REQUEST
        if status == 404:
            # например, неверное имя модели
            return ErrorClass.CONFIG
        return ErrorClass.UNKNOWN

    if isinstance(exc, OperationalError):
        return ErrorClass.TRANSIENT
    if isinstance(exc, SQLAlchemyError):
        return ErrorClass.PERSISTENCE
    return ErrorClass.UNKNOWN


def backoff_seconds(error_class: ErrorClass, attempts: int) -> int:
    """attempts — уже сделанные попытки (>=1)."""
    n = max(attempts - 1, 0)
    if error_class == ErrorClass.RATE_LIMIT:
        return min(30 * 2 ** n, 900)
    if error_class == ErrorClass.INVALID_OUTPUT:
        return min(5 * 2 ** n, 120)
    return min(10 * 2 ** n, 600)
