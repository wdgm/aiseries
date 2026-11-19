import logging
import uuid

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.errors import InvalidOutputError, classify
from app.ai.providers import AIProvider, LLMCall, create_ai_provider
from app.services.ai_usage import record_usage
from app.services.book_analysis.prompts import (
    SYSTEM_PROMPT,
    build_chunk_prompt,
)
from app.services.book_analysis.schemas import BookAnalysisResult


logger = logging.getLogger(__name__)


def parse_analysis(call: LLMCall) -> BookAnalysisResult:
    if call.finish_reason == "length":
        raise InvalidOutputError(
            "LLM output truncated (finish_reason=length); "
            "increase AI_MAX_OUTPUT_TOKENS or reduce chunk size"
        )
    text = (call.text or "").strip()
    if not text:
        raise InvalidOutputError("LLM returned empty content")
    # На случай, если модель всё же обернула JSON в ```json ... ```
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return BookAnalysisResult.model_validate_json(text)
    except ValidationError as exc:
        raise InvalidOutputError(f"Schema validation failed: {exc}") from exc


class BookAnalyzer:
    def __init__(self, provider: AIProvider | None = None) -> None:
        self.provider = provider or create_ai_provider()

    def call_llm(
        self,
        text: str,
        chapter_number: int,
        chunk_index: int,
    ) -> LLMCall:
        prompt = build_chunk_prompt(
            text=text,
            chapter_number=chapter_number,
            chunk_index=chunk_index,
        )
        return self.provider.complete_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=BookAnalysisResult,
        )

    def analyze_chunk_with_usage(
        self,
        text: str,
        chapter_number: int,
        chunk_index: int,
        db: Session | None = None,
        operation: str = "analyze_chunk_sync",
        book_id: uuid.UUID | None = None,
    ) -> tuple[BookAnalysisResult, dict[str, int]]:
        """Синхронный анализ одного фрагмента (preview / ручной режим).

        Если передан db — запрос записывается в ai_usage отдельной транзакцией.
        """
        call: LLMCall | None = None
        try:
            call = self.call_llm(text, chapter_number, chunk_index)
            result = parse_analysis(call)
        except Exception as exc:
            if db is not None:
                self._record(db, operation, book_id, call, exc)
            raise

        if db is not None:
            self._record(db, operation, book_id, call, None)

        return result, call.usage.as_dict()

    def _record(self, db, operation, book_id, call, exc) -> None:
        try:
            record_usage(
                    db,
                    operation=operation,
                    provider=self.provider.name,
                    model=self.provider.model,
                    status=(
                        "ok" if exc is None
                        else "invalid_output" if call is not None
                        else "error"
                    ),
                    call=call,
                    book_id=book_id,
                    error_class=classify(exc).value if exc else None,
                    error=str(exc) if exc else None,
                )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to record AI usage")
