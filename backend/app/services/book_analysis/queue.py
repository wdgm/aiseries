"""Очередь анализа на Postgres (FOR UPDATE SKIP LOCKED), без Redis.

Гарантии:
- каждый фрагмент = строка analysis_tasks; succeeded никогда не перезапускается;
- сохранение результата и пометка succeeded — в ОДНОЙ транзакции (нет дублей
  упоминаний при падении между ними);
- задача, взятая умершим воркером, возвращается в очередь по истечении lease
  (или сразу при рестарте воркера с тем же WORKER_ID).
"""

import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    AIUsage,
    AnalysisJob,
    AnalysisTask,
    Book,
    Chapter,
    TaskStatus,
    TextChunk,
)

ACTIVE_JOB_STATUSES = ("queued", "running", "paused")
DONE_JOB_STATUSES = ("completed", "completed_with_errors")

JOB_CHARACTERS = "characters"
JOB_STORY = "story_elements"
TASK_TYPE_BY_JOB = {
    JOB_CHARACTERS: "analyze_chunk",
    JOB_STORY: "extract_story_elements",
}
CLAIMABLE_JOB_STATUSES = ("queued", "running")



class ActiveJobExistsError(Exception):
    def __init__(self, job_id: uuid.UUID):
        super().__init__(f"Book already has an active analysis job {job_id}")
        self.job_id = job_id


class JobStateError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_active_job(db: Session, book_id: uuid.UUID) -> AnalysisJob | None:
    return db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.book_id == book_id,
            AnalysisJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )


def find_done_job(db: Session, book_id: uuid.UUID, job_type: str) -> AnalysisJob | None:
    return db.scalar(
        select(AnalysisJob)
        .where(
            AnalysisJob.book_id == book_id,
            AnalysisJob.job_type == job_type,
            AnalysisJob.status.in_(DONE_JOB_STATUSES),
        )
        .order_by(AnalysisJob.completed_at.desc().nulls_last())
        .limit(1)
    )


def enqueue_story_job(db: Session, book: Book, force: bool = False) -> AnalysisJob:
    """Второй проход (события + локации). Требует завершённого анализа персонажей.

    Без force повторный запуск запрещён: события уже есть, второй проход по тем же
    фрагментам только потратил бы деньги (дубли всё равно отсеиваются по позиции).
    """
    if find_done_job(db, book.id, JOB_CHARACTERS) is None:
        raise JobStateError(
            "Character analysis must be completed before extracting events and locations"
        )
    if not force and find_done_job(db, book.id, JOB_STORY) is not None:
        raise JobStateError(
            "Events and locations are already extracted for this book; pass force=true to re-run"
        )
    return enqueue_analysis_job(db, book, job_type=JOB_STORY)


def enqueue_analysis_job(
    db: Session, book: Book, job_type: str = JOB_CHARACTERS
) -> AnalysisJob:
    """Создаёт задачу и по одной подзадаче на каждый фрагмент. Commit — у вызывающего."""
    if job_type not in TASK_TYPE_BY_JOB:
        raise ValueError(f"Unknown job_type {job_type}")
    active = find_active_job(db, book.id)
    if active is not None:
        raise ActiveJobExistsError(active.id)

    rows = db.execute(
        select(TextChunk.id, Chapter.chapter_number, TextChunk.chunk_index)
        .join(Chapter, TextChunk.chapter_id == Chapter.id)
        .where(Chapter.book_id == book.id)
        .order_by(Chapter.chapter_number, TextChunk.chunk_index)
    ).all()
    if not rows:
        raise ValueError("Book has no text chunks")

    job = AnalysisJob(
        book_id=book.id,
        job_type=job_type,
        status="queued",
        total_chunks=len(rows),
        report={},
    )
    db.add(job)
    db.flush()

    now = utcnow()
    db.execute(
        AnalysisTask.__table__.insert(),
        [
            {
                "id": uuid.uuid4(),
                "job_id": job.id,
                "task_type": TASK_TYPE_BY_JOB[job_type],
                "chunk_id": chunk_id,
                "chapter_number": chapter_number,
                "chunk_index": chunk_index,
                "status": TaskStatus.PENDING,
                "attempts": 0,
                "max_attempts": settings.task_max_attempts,
                "next_run_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for chunk_id, chapter_number, chunk_index in rows
        ],
    )
    return job


def resume_job(db: Session, job: AnalysisJob) -> dict:
    """Продолжить: dead/cancelled подзадачи → pending, succeeded не трогаем."""
    has_tasks = db.scalar(
        select(func.count()).select_from(AnalysisTask).where(AnalysisTask.job_id == job.id)
    )
    if not has_tasks:
        raise JobStateError(
            "Legacy job without tasks cannot be resumed; use /analyze/reanalyze"
        )
    if job.status in CLAIMABLE_JOB_STATUSES:
        raise JobStateError(f"Job is already {job.status}")

    other = find_active_job(db, job.book_id)
    if other is not None and other.id != job.id:
        raise ActiveJobExistsError(other.id)

    result = db.execute(
        update(AnalysisTask)
        .where(
            AnalysisTask.job_id == job.id,
            AnalysisTask.status.in_((TaskStatus.DEAD, TaskStatus.CANCELLED)),
        )
        .values(
            status=TaskStatus.PENDING,
            attempts=0,
            next_run_at=utcnow(),
            error_class=None,
            locked_by=None,
            locked_until=None,
            finished_at=None,
            updated_at=utcnow(),
        )
    )
    job.status = "queued"
    job.completed_at = None
    job.last_error = None
    db.flush()
    return {"requeued_tasks": result.rowcount}


def cancel_job(db: Session, job: AnalysisJob) -> dict:
    if job.status not in ACTIVE_JOB_STATUSES:
        raise JobStateError(f"Job is {job.status}, nothing to cancel")
    result = db.execute(
        update(AnalysisTask)
        .where(AnalysisTask.job_id == job.id, AnalysisTask.status == TaskStatus.PENDING)
        .values(status=TaskStatus.CANCELLED, finished_at=utcnow(), updated_at=utcnow())
    )
    job.status = "cancelled"
    job.completed_at = utcnow()
    db.flush()
    return {"cancelled_tasks": result.rowcount}


# --------------------------------------------------------------------- worker


def reclaim_own_tasks(db: Session, worker_id: str) -> int:
    """При старте: то, что этот воркер держал до падения, возвращаем сразу."""
    result = db.execute(
        text(
            """
            UPDATE analysis_tasks
               SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                   finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                   error_class = 'lease_expired',
                   last_error = 'Worker restarted while task was running',
                   locked_by = NULL, locked_until = NULL,
                   next_run_at = now(), updated_at = now()
             WHERE status = 'running' AND locked_by = :worker
            RETURNING job_id
            """
        ),
        {"worker": worker_id},
    )
    job_ids = {row[0] for row in result}
    db.commit()
    for job_id in job_ids:
        refresh_job(db, job_id)
    return len(job_ids)


def requeue_expired(db: Session) -> int:
    result = db.execute(
        text(
            """
            UPDATE analysis_tasks
               SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                   finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                   error_class = 'lease_expired',
                   last_error = 'Worker lease expired (worker died or hung)',
                   locked_by = NULL, locked_until = NULL,
                   next_run_at = now(), updated_at = now()
             WHERE status = 'running' AND locked_until < now()
            RETURNING job_id
            """
        )
    )
    job_ids = {row[0] for row in result}
    db.commit()
    for job_id in job_ids:
        refresh_job(db, job_id)
    return len(job_ids)


def claim_tasks(db: Session, worker_id: str, limit: int = 1) -> list[uuid.UUID]:
    rows = db.execute(
        text(
            """
            WITH picked AS (
                SELECT t.id
                  FROM analysis_tasks t
                  JOIN analysis_jobs j ON j.id = t.job_id
                 WHERE t.status = 'pending'
                   AND t.next_run_at <= now()
                   AND j.status IN ('queued', 'running')
                 ORDER BY j.created_at, t.chapter_number, t.chunk_index
                 LIMIT :limit
                 FOR UPDATE OF t SKIP LOCKED
            )
            UPDATE analysis_tasks t
               SET status = 'running',
                   attempts = t.attempts + 1,
                   locked_by = :worker,
                   locked_until = now() + make_interval(secs => :lease),
                   started_at = COALESCE(t.started_at, now()),
                   updated_at = now()
              FROM picked
             WHERE t.id = picked.id
            RETURNING t.id, t.job_id
            """
        ),
        {"limit": limit, "worker": worker_id, "lease": settings.worker_lease_seconds},
    ).all()

    job_ids = {row.job_id for row in rows}
    if job_ids:
        db.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id.in_(job_ids), AnalysisJob.status == "queued")
            .values(status="running", started_at=func.coalesce(AnalysisJob.started_at, func.now()))
        )
    db.commit()
    return [row.id for row in rows]


def lock_owned_task(db: Session, task_id: uuid.UUID, worker_id: str) -> AnalysisTask | None:
    """SELECT ... FOR UPDATE; None, если задача уже не наша (lease потерян)."""
    task = db.scalar(
        select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update()
    )
    if task is None or task.status != TaskStatus.RUNNING or task.locked_by != worker_id:
        return None
    return task


def refresh_job(db: Session, job_id: uuid.UUID) -> AnalysisJob | None:
    """Пересчитывает прогресс/стоимость из подзадач и ai_usage; завершает задачу."""
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
    if job is None:
        db.rollback()
        return None

    counts = Counter(
        dict(
            db.execute(
                select(AnalysisTask.status, func.count())
                .where(AnalysisTask.job_id == job_id)
                .group_by(AnalysisTask.status)
            ).all()
        )
    )
    usage = db.execute(
        select(
            func.coalesce(func.sum(AIUsage.input_tokens), 0),
            func.coalesce(func.sum(AIUsage.cached_input_tokens), 0),
            func.coalesce(func.sum(AIUsage.output_tokens), 0),
            func.coalesce(func.sum(AIUsage.total_tokens), 0),
            func.coalesce(func.sum(AIUsage.cost_usd), 0),
            func.count().filter(AIUsage.cost_usd.is_(None)),
        ).where(AIUsage.job_id == job_id)
    ).one()

    total = sum(counts.values())
    job.total_chunks = total
    job.processed_chunks = counts[TaskStatus.SUCCEEDED]
    job.failed_chunks = counts[TaskStatus.DEAD]
    (
        job.input_tokens,
        job.cached_input_tokens,
        job.output_tokens,
        job.total_tokens,
        cost,
        job.unpriced_requests,
    ) = usage
    job.cost_usd = Decimal(cost)

    last = db.execute(
        select(AnalysisTask.chapter_number, AnalysisTask.chunk_index)
        .where(AnalysisTask.job_id == job_id, AnalysisTask.status == TaskStatus.SUCCEEDED)
        .order_by(AnalysisTask.finished_at.desc().nulls_last())
        .limit(1)
    ).first()
    if last:
        job.current_chapter_number, job.current_chunk_index = last

    open_tasks = counts[TaskStatus.PENDING] + counts[TaskStatus.RUNNING]
    finished = job.status in CLAIMABLE_JOB_STATUSES and total > 0 and open_tasks == 0
    if finished:
        job.status = "completed" if counts[TaskStatus.DEAD] == 0 else "completed_with_errors"
        job.completed_at = utcnow()

    job.report = _build_report(db, job_id, counts)
    db.commit()

    if finished and job.job_type == JOB_CHARACTERS and settings.story_auto_enqueue:
        _auto_enqueue_story(db, job.book_id)
    return job


def _auto_enqueue_story(db: Session, book_id: uuid.UUID) -> None:
    """Персонажи готовы → ставим второй проход, если его ещё не было."""
    import logging

    log = logging.getLogger(__name__)
    try:
        book = db.get(Book, book_id)
        if book is None:
            return
        story = enqueue_story_job(db, book)
        db.commit()
        log.info("Auto-enqueued story_elements job %s for book %s", story.id, book_id)
    except (JobStateError, ActiveJobExistsError) as exc:
        db.rollback()
        log.info("Story job not auto-enqueued for book %s: %s", book_id, exc)
    except Exception:
        db.rollback()
        log.exception("Failed to auto-enqueue story job for book %s", book_id)


def _build_report(db: Session, job_id: uuid.UUID, counts: Counter) -> dict:
    # Суммируем все числовые счётчики подзадач (у разных типов задач они разные).
    totals: dict = {}
    ambiguous: list = []
    for (report,) in db.execute(
        select(AnalysisTask.report).where(
            AnalysisTask.job_id == job_id,
            AnalysisTask.status == TaskStatus.SUCCEEDED,
        )
    ):
        for key, value in (report or {}).items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
        ambiguous.extend((report or {}).get("ambiguous", []))
    totals["ambiguous"] = ambiguous[:500]

    totals["errors"] = [
        {
            "chapter_number": r.chapter_number,
            "chunk_index": r.chunk_index,
            "error_class": r.error_class,
            "error": (r.last_error or "")[:500],
            "attempts": r.attempts,
        }
        for r in db.execute(
            select(
                AnalysisTask.chapter_number,
                AnalysisTask.chunk_index,
                AnalysisTask.error_class,
                AnalysisTask.last_error,
                AnalysisTask.attempts,
            )
            .where(AnalysisTask.job_id == job_id, AnalysisTask.status == TaskStatus.DEAD)
            .order_by(AnalysisTask.chapter_number, AnalysisTask.chunk_index)
            .limit(200)
        )
    ]
    totals["task_counts"] = {
        status: counts[status]
        for status in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.DEAD,
            TaskStatus.CANCELLED,
        )
    }
    return totals
