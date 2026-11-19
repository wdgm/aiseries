"""Локации книги (Phase 2): сущность проекта + алиасы, атрибуты, упоминания.

Каждый атрибут и упоминание хранит дословную цитату и её позицию в главе
(provenance). Позиции абсолютные в chapter.text — это позволяет отбрасывать
дубли из зоны перекрытия соседних фрагментов.
"""

import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Location(TimestampMixin, Base):
    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("project_id", "normalized_name", name="uq_locations_project_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Книга, в которой локация впервые обнаружена.
    first_book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="SET NULL"), nullable=True
    )
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False)
    # city | settlement | building | room | nature | region | vehicle | other
    location_type: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    description: Mapped[str | None] = mapped_column(Text)
    # discovered | confirmed | rejected — как у персонажей
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="discovered", index=True)

    aliases: Mapped[list["LocationAlias"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )
    attributes: Mapped[list["LocationAttribute"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )
    mentions: Mapped[list["LocationMention"]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )


class LocationAlias(TimestampMixin, Base):
    __tablename__ = "location_aliases"
    __table_args__ = (
        UniqueConstraint("location_id", "normalized_alias", name="uq_location_alias_normalized"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(300), nullable=False, index=True)

    location = relationship("Location", back_populates="aliases")


class LocationAttribute(TimestampMixin, Base):
    __tablename__ = "location_attributes"
    __table_args__ = (
        Index("ix_location_attributes_dedupe", "location_id", "category", "chapter_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # geography | architecture | environment | atmosphere | time_context | other
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    # Абсолютные позиции цитаты в chapter.text
    start_position: Mapped[int | None] = mapped_column(Integer)
    end_position: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="proposed")

    location = relationship("Location", back_populates="attributes")


class LocationMention(TimestampMixin, Base):
    __tablename__ = "location_mentions"
    __table_args__ = (
        UniqueConstraint(
            "location_id", "chapter_id", "start_position", name="uq_location_mentions_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_position: Mapped[int] = mapped_column(Integer, nullable=False)
    end_position: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)

    location = relationship("Location", back_populates="mentions")

