
import logging
from datetime import datetime, timezone
from uuid import UUID

from app.core.database import SessionLocal
from app.models import AnalysisJob, Book, Chapter, TextChunk
from app.services.book_analysis.analyzer import BookAnalyzer

logger = logging.getLogger(__name__)


def run_analysis_job(job_id: str) -> None:
    """Выполняет анализ книги в отдельной сессии БД."""

    db = SessionLocal()

    try:
        job = db.query(AnalysisJob).filter(
            AnalysisJob.id == UUID(job_id)
        ).first()

        if job is None:
            logger.error("Analysis job %s not found", job_id)
            return

        book = db.query(Book).filter(
            Book.id == job.book_id
        ).first()

        if book is None:
            job.status = "failed"
            job.last_error = "Book not found"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.last_error = None

        job.total_chunks = (
            db.query(TextChunk)
            .join(Chapter, TextChunk.chapter_id == Chapter.id)
            .filter(Chapter.book_id == book.id)
            .count()
        )

        db.commit()

        analyzer = BookAnalyzer()
        result = analyzer.analyze_book(
            db=db,
            book_id=book.id,
            job_id=UUID(job_id),
        )

        # Сохраняем итоговый отчёт.
        job = db.query(AnalysisJob).filter(
            AnalysisJob.id == UUID(job_id)
        ).first()

        if job is None:
            return

        job.report = result
        job.processed_chunks = result.get("processed_chunks", 0)
        job.failed_chunks = result.get("failed_chunks", 0)

        job.status = result.get("status", "completed")
        job.completed_at = datetime.now(timezone.utc)

        db.commit()

        logger.info(
            "Analysis job %s finished with status %s",
            job_id,
            job.status,
        )

    except Exception as exc:
        db.rollback()

        logger.exception("Analysis job %s failed", job_id)

        job = db.query(AnalysisJob).filter(
            AnalysisJob.id == UUID(job_id)
        ).first()

        if job is not None:
            job.status = "failed"
            job.last_error = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

    finally:
        db.close()
