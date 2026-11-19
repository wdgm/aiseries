
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers import create_ai_provider

from app.models.book import Book
from app.models.chapter import Chapter
from app.models.text_chunk import TextChunk

from app.services.book_analysis.prompts import (
    SYSTEM_PROMPT,
    build_chunk_prompt,
)
from app.services.book_analysis.schemas import BookAnalysisResult
from app.services.book_analysis.persistence import save_analysis_result


logger = logging.getLogger(__name__)


class BookAnalyzer:
    def __init__(self) -> None:
        self.provider = create_ai_provider()

    def analyze_chunk(
        self,
        text: str,
        chapter_number: int,
        chunk_index: int,
    ) -> BookAnalysisResult:
        result, _ = self.analyze_chunk_with_usage(
            text=text,
            chapter_number=chapter_number,
            chunk_index=chunk_index,
        )
        return result

    def analyze_chunk_with_usage(
        self,
        text: str,
        chapter_number: int,
        chunk_index: int,
    ) -> tuple[BookAnalysisResult, dict[str, int]]:
        prompt = build_chunk_prompt(
            text=text,
            chapter_number=chapter_number,
            chunk_index=chunk_index,
        )

        response = self.provider.client.beta.chat.completions.parse(
            model=self.provider.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format=BookAnalysisResult,
        )

        result = response.choices[0].message.parsed

        if result is None:
            raise RuntimeError(
                f"{self.provider.name} did not return structured analysis"
            )

        usage = response.usage
        if usage is None:
            raise RuntimeError(
                f"{self.provider.name} did not return token usage"
            )

        return result, {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }

    def analyze_book(
        self,
        db: Session,
        book_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> dict:
        book = db.scalar(
            select(Book).where(Book.id == book_id)
        )

        if book is None:
            raise ValueError("Book not found")

        statement = (
            select(Chapter, TextChunk)
            .join(
                TextChunk,
                TextChunk.chapter_id == Chapter.id,
            )
            .where(Chapter.book_id == book_id)
            .order_by(
                Chapter.chapter_number,
                TextChunk.chunk_index,
            )
        )

        rows = db.execute(statement).all()

        if not rows:
            raise ValueError("Book has no text chunks")

        totals = {
            "processed_chunks": 0,
            "failed_chunks": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "created": 0,
            "matched": 0,
            "attributes": 0,
            "mentions": 0,
            "relationships": 0,
            "rejected_evidence": 0,
            "unresolved_relationships": 0,
            "ambiguous": [],
            "errors": [],
        }

        
        for chapter, chunk in rows:
            logger.info(
                "Analyzing book=%s chapter=%s chunk=%s",
                book_id,
                chapter.chapter_number,
                chunk.chunk_index,
            )

            # Обновляем текущую позицию задачи.
            if job_id is not None:
                from app.models import AnalysisJob

                job = db.query(AnalysisJob).filter(
                    AnalysisJob.id == job_id
                ).first()

                if job is not None:
                    job.current_chapter_number = chapter.chapter_number
                    job.current_chunk_index = chunk.chunk_index
                    db.commit()

            try:
                result, usage = self.analyze_chunk_with_usage(
                    text=chunk.content,
                    chapter_number=chapter.chapter_number,
                    chunk_index=chunk.chunk_index,
                )

                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    totals[key] += usage[key]

                report = save_analysis_result(
                    db=db,
                    project_id=book.project_id,
                    result=result,
                    chunk=chunk,
                )

                db.commit()

                totals["processed_chunks"] += 1

                for key in (
                    "created",
                    "matched",
                    "attributes",
                    "mentions",
                    "relationships",
                    "rejected_evidence",
                    "unresolved_relationships",
                ):
                    totals[key] += report.get(key, 0)

                totals["ambiguous"].extend(
                    report.get("ambiguous", [])
                )

            except Exception as exc:
                db.rollback()

                totals["failed_chunks"] += 1

                error = {
                    "chapter_number": chapter.chapter_number,
                    "chunk_index": chunk.chunk_index,
                    "error": str(exc),
                }

                totals["errors"].append(error)

                logger.exception(
                    "Analysis failed for chapter=%s chunk=%s",
                    chapter.chapter_number,
                    chunk.chunk_index,
                )

            # Сохраняем прогресс после каждого фрагмента.
            if job_id is not None:
                from app.models import AnalysisJob

                job = db.query(AnalysisJob).filter(
                    AnalysisJob.id == job_id
                ).first()

                if job is not None:
                    job.processed_chunks = totals["processed_chunks"]
                    job.failed_chunks = totals["failed_chunks"]
                    job.input_tokens = totals["input_tokens"]
                    job.output_tokens = totals["output_tokens"]
                    job.total_tokens = totals["total_tokens"]
                    job.report = dict(totals)
                    db.commit()

        totals["status"] = (
            "completed"
            if totals["failed_chunks"] == 0
            else "completed_with_errors"
        )

        totals["book_id"] = str(book_id)
        totals["total_chunks"] = len(rows)

        return totals
