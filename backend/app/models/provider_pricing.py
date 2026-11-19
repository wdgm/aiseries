import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProviderPricing(TimestampMixin, Base):
    """Версионированные цены провайдеров (USD за 1M токенов)."""

    __tablename__ = "provider_pricing"
    __table_args__ = (
        UniqueConstraint(
            "provider", "model", "tier", "valid_from",
            name="uq_provider_pricing_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    input_cache_miss_per_1m: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    input_cache_hit_per_1m: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_per_1m: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(Text)
