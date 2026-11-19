
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class CharacterVersion(TimestampMixin, Base):
    __tablename__ = "character_versions"

    __table_args__ = (
        UniqueConstraint(
            "character_id",
            "version_number",
            name="uq_character_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    character_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    visual_profile: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    personality_profile: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    voice_profile: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    visual_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    change_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="draft",
        index=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    character = relationship("Character", back_populates="versions")
