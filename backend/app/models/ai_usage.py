import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class AIUsage(Base):
    """Одна строка = один HTTP-запрос к LLM (включая неудачные)."""

    __tablename__ = "ai_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_jobs.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="SET NULL"), index=True
    )
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("books.id", ondelete="SET NULL"), index=True
    )
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    response_model: Mapped[str | None] = mapped_column(String(120))
    request_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_class: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    finish_reason: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pricing_tier: Mapped[str | None] = mapped_column(String(20))
    pricing_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("provider_pricing.id", ondelete="SET NULL")
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
