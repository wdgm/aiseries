"""События книги (Phase 2).

Событие привязано к главе/фрагменту и подтверждено дословной цитатой
(evidence) с абсолютной позицией в chapter.text. По этой позиции строится
порядок событий (timeline_position) и отбрасываются дубли из зоны
перекрытия соседних фрагментов.
"""

import uuid

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class StoryEvent(TimestampMixin, Base):
    __tablename__ = "story_events"
    __table_args__ = (
        Index("ix_story_events_book_timeline", "book_id", "timeline_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # plot | character | background
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, default="plot")
    # major | minor
    importance: Mapped[str] = mapped_column(String(20), nullable=False, default="minor")
    is_turning_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_climax: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Время так, как оно указано в тексте («на рассвете», «через год»); null = не указано.
    time_text: Mapped[str | None] = mapped_column(Text)
    cause: Mapped[str | None] = mapped_column(Text)
    consequences: Mapped[str | None] = mapped_column(Text)
    # Название локации, как его вернула модель, если сопоставить не удалось.
    raw_location_name: Mapped[str | None] = mapped_column(String(300))

    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    start_position: Mapped[int] = mapped_column(Integer, nullable=False)
    end_position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Порядок в книге: chapter_number * 10^9 + start_position (порядок повествования).
    timeline_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")

    participants: Mapped[list["EventParticipant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    location = relationship("Location")


class EventParticipant(TimestampMixin, Base):
    __tablename__ = "event_participants"
    __table_args__ = (
        UniqueConstraint("event_id", "raw_name", name="uq_event_participants_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("story_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # null, если имя не удалось однозначно сопоставить с персонажем (см. match_status).
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw_name: Mapped[str] = mapped_column(String(300), nullable=False)
    # инициатор, жертва, свидетель… свободный текст
    role: Mapped[str | None] = mapped_column(String(100))
    # matched | ambiguous | unknown
    match_status: Mapped[str] = mapped_column(String(20), nullable=False)

    event = relationship("StoryEvent", back_populates="participants")
    character = relationship("Character")
