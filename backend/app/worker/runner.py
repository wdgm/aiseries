"""Воркер анализа: python -m app.worker

Масштабирование: docker compose up -d --scale worker=N. Сохранение результатов
сериализуется по проекту (advisory lock), чтобы параллельные воркеры не
создавали дубли персонажей при сопоставлении.
"""

import logging
import os
import signal
import socket
import time
import uuid
from datetime import timedelta

from sqlalchemy import inspect, select, text

from app.ai.errors import (
    PAUSE_JOB,
    RETRYABLE,
    ErrorClass,
    ProviderConfigError,
    TaskDataError,
    backoff_seconds,
    classify,
)
from app.ai.providers import LLMCall
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models import AnalysisJob, AnalysisTask, Book, Chapter, TaskStatus, TextChunk
from app.services.ai_usage import record_usage
from app.services.book_analysis.analyzer import BookAnalyzer, parse_analysis
from app.services.book_analysis.persistence import save_analysis_result
from app.services.book_analysis.queue import (
    claim_tasks,
    lock_owned_task,
    reclaim_own_tasks,
    refresh_job,
    requeue_expired,
    utcnow,
)
from app.services.book_analysis.schemas import BookAnalysisResult

logger = logging.getLogger("app.worker")


class Worker:
    def __init__(self, analyzer_factory=BookAnalyzer) -> None:
        self.worker_id = settings.worker_id or f"{socket.gethostname()}"
        self.analyzer_factory = analyzer_factory
        self.analyzer: BookAnalyzer | None = None
        self.stopping = False

    # ------------------------------------------------------------ lifecycle

    def request_stop(self, *_):
        if not self.stopping:
            logger.info("Stop requested; finishing current task…")
        self.stopping = True

    def wait_for_schema(self) -> None:
        while not self.stopping:
            try:
                if inspect(engine).has_table("analysis_tasks"):
                    return
                logger.info("Waiting for migrations (analysis_tasks missing)…")
            except Exception as exc:  # БД ещё не поднялась
                logger.info("Waiting for database: %s", exc)
            time.sleep(3)

    def ensure_analyzer(self) -> bool:
        if self.analyzer is not None:
            return True
        try:
            self.analyzer = self.analyzer_factory()
            logger.info(
                "Provider: %s / %s", self.analyzer.provider.name, self.analyzer.provider.model
            )
            return True
        except ProviderConfigError as exc:
            logger.error("AI provider misconfigured: %s (retry in 30s)", exc)
            return False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        logger.info("Worker %s starting", self.worker_id)

        self.wait_for_schema()
        with SessionLocal() as db:
            reclaimed = reclaim_own_tasks(db, self.worker_id)
            if reclaimed:
                logger.info("Reclaimed tasks from previous run (%s jobs)", reclaimed)

        while not self.stopping:
            if not self.ensure_analyzer():
                self._sleep(30)
                continue
            try:
                did_work = self.run_once()
            except Exception:
                logger.exception("Worker loop error")
                did_work = False
            if not did_work:
                self._sleep(settings.worker_poll_seconds)

        logger.info("Worker %s stopped", self.worker_id)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    def run_once(self) -> bool:
        with SessionLocal() as db:
            requeue_expired(db)
            task_ids = claim_tasks(db, self.worker_id, limit=1)
        for task_id in task_ids:
            self.process(task_id)
        return bool(task_ids)

    # -------------------------------------------------------------- process

    def process(self, task_id: uuid.UUID) -> None:
        with SessionLocal() as db:
            task = db.get(AnalysisTask, task_id)
            if task is None:
                return
            job_id = task.job_id
            log = f"task={task_id} ch={task.chapter_number} chunk={task.chunk_index} attempt={task.attempts}"
            try:
                ctx = self._load_context(db, task)
            except Exception as exc:
                db.rollback()
                self._fail(task_id, job_id, exc)
                return
            book_id, project_id, chunk_content = ctx
            cached = task.llm_result
            db.commit()

        # 1) LLM (вне транзакции; результат + usage фиксируются сразу)
        if cached is None:
            logger.info("LLM %s", log)
            result = self._call_llm(task_id, job_id, book_id, chunk_content)
            if result is None:
                return
        else:
            logger.info("Using cached LLM result %s", log)
            result = BookAnalysisResult.model_validate(cached)

        # 2) Сохранение + succeeded — атомарно
        with SessionLocal() as db:
            try:
                task = lock_owned_task(db, task_id, self.worker_id)
                if task is None:
                    logger.warning("Lost lease before persist, skipping %s", log)
                    db.rollback()
                    return
                job = db.get(AnalysisJob, job_id)
                if job is not None and job.status == "cancelled":
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = utcnow()
                    task.locked_by = task.locked_until = None
                    db.commit()
                    refresh_job(db, job_id)
                    return

                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": f"persist:project:{project_id}"},
                )
                chunk = db.get(TextChunk, task.chunk_id)
                report = save_analysis_result(
                    db=db, project_id=project_id, result=result, chunk=chunk
                )
                task.status = TaskStatus.SUCCEEDED
                task.report = _jsonable(report)
                task.finished_at = utcnow()
                task.locked_by = None
                task.locked_until = None
                task.error_class = None
                db.commit()
                logger.info("Done %s", log)
            except Exception as exc:
                db.rollback()
                logger.exception("Persist failed %s", log)
                self._fail(task_id, job_id, exc)
                return
            refresh_job(db, job_id)

    def _load_context(self, db, task: AnalysisTask):
        if task.chunk_id is None:
            raise TaskDataError("Task has no chunk_id")
        row = db.execute(
            select(TextChunk.content, Book.id, Book.project_id)
            .join(Chapter, TextChunk.chapter_id == Chapter.id)
            .join(Book, Chapter.book_id == Book.id)
            .where(TextChunk.id == task.chunk_id)
        ).first()
        if row is None:
            raise TaskDataError("Chunk/chapter/book not found")
        content, book_id, project_id = row
        return book_id, project_id, content

    def _call_llm(self, task_id, job_id, book_id, content) -> BookAnalysisResult | None:
        with SessionLocal() as db:
            task = db.get(AnalysisTask, task_id)
            chapter_number, chunk_index = task.chapter_number, task.chunk_index

        provider = self.analyzer.provider
        call: LLMCall | None = None
        started = time.monotonic()
        try:
            call = self.analyzer.call_llm(content, chapter_number, chunk_index)
            result = parse_analysis(call)
        except Exception as exc:
            cls = classify(exc)
            with SessionLocal() as db:
                record_usage(
                    db,
                    operation="analyze_chunk",
                    provider=provider.name,
                    model=provider.model,
                    status="invalid_output" if call is not None else "error",
                    call=call,
                    job_id=job_id,
                    task_id=task_id,
                    book_id=book_id,
                    error_class=cls.value,
                    error=str(exc),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                db.commit()
            logger.warning("LLM failed task=%s class=%s: %s", task_id, cls.value, exc)
            self._fail(task_id, job_id, exc)
            return None

        # usage и кэш результата — одним commit: деньги потрачены, результат не теряем.
        with SessionLocal() as db:
            record_usage(
                db,
                operation="analyze_chunk",
                provider=provider.name,
                model=provider.model,
                status="ok",
                call=call,
                job_id=job_id,
                task_id=task_id,
                book_id=book_id,
            )
            db.execute(
                AnalysisTask.__table__.update()
                .where(AnalysisTask.id == task_id)
                .values(llm_result=result.model_dump(mode="json"), updated_at=utcnow())
            )
            db.commit()
        return result

    def _fail(self, task_id: uuid.UUID, job_id: uuid.UUID, exc: BaseException) -> None:
        cls = classify(exc)
        message = f"{type(exc).__name__}: {exc}"[:4000]
        with SessionLocal() as db:
            task = lock_owned_task(db, task_id, self.worker_id)
            if task is None:
                db.rollback()
                return
            task.error_class = cls.value
            task.last_error = message
            task.locked_by = None
            task.locked_until = None

            if cls in PAUSE_JOB:
                # Проблема среды (ключ/баланс/конфиг): попытку не списываем.
                task.status = TaskStatus.PENDING
                task.attempts = max(task.attempts - 1, 0)
                task.next_run_at = utcnow()
                job = db.get(AnalysisJob, job_id, with_for_update=True)
                if job is not None and job.status in ("queued", "running"):
                    job.status = "paused"
                    job.last_error = f"[{cls.value}] {message}"
                logger.error("Job %s paused: [%s] %s", job_id, cls.value, message)
                if cls == ErrorClass.CONFIG:
                    self.analyzer = None
            elif cls in RETRYABLE and task.attempts < task.max_attempts:
                delay = backoff_seconds(cls, task.attempts)
                task.status = TaskStatus.PENDING
                task.next_run_at = utcnow() + timedelta(seconds=delay)
                logger.info("Retry task=%s in %ss (%s)", task_id, delay, cls.value)
            else:
                task.status = TaskStatus.DEAD
                task.finished_at = utcnow()
                logger.error("Task %s dead: [%s] %s", task_id, cls.value, message)
            db.commit()
            refresh_job(db, job_id)


def _jsonable(value):
    import json

    return json.loads(json.dumps(value, default=str))


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Worker().run_forever()
