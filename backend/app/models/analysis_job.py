import uuid
from datetime import datetime

from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        # Не больше одной активной задачи анализа на книгу.
        Index(
            "uq_analysis_jobs_one_active_per_book",
            "book_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running','paused')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    book_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # characters — персонажи/упоминания/отношения (основной проход);
    # story_elements — события и локации (второй проход, опирается на персонажей).
    job_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="characters",
        server_default="characters",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        # queued | running | paused | completed | completed_with_errors
        # | cancelled | failed | interrupted (legacy)
        default="queued",
        index=True,
    )

    total_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    processed_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    failed_chunks: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    cached_input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6),
        nullable=False,
        default=Decimal("0"),
    )

    # Запросы, для которых нет цены в provider_pricing (cost_usd неполный).
    unpriced_requests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    current_chapter_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    current_chunk_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    report: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )