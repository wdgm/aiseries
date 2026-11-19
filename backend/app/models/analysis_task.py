import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    text,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utc_now


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"
    CANCELLED = "cancelled"


class AnalysisTask(TimestampMixin, Base):
    """Единица работы очереди: один фрагмент текста в рамках задачи анализа."""

    __tablename__ = "analysis_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "chunk_id", name="uq_analysis_tasks_job_chunk"),
        Index("ix_analysis_tasks_job_status", "job_id", "status"),
        Index("ix_analysis_tasks_claim", "next_run_at",
              postgresql_where=text("status = 'pending'")),
        Index("ix_analysis_tasks_lease", "locked_until",
              postgresql_where=text("status = 'running'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="analyze_chunk"
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("text_chunks.id", ondelete="CASCADE"),
        nullable=True,
    )
    chapter_number: Mapped[int | None] = mapped_column(Integer)
    chunk_index: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    locked_by: Mapped[str | None] = mapped_column(String(200))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error_class: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)

    llm_result: Mapped[dict | None] = mapped_column(JSON)
    report: Mapped[dict | None] = mapped_column(JSON)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
