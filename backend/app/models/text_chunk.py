import uuid

from sqlalchemy import Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TextChunk(Base, TimestampMixin):
    __tablename__ = "text_chunks"

    __table_args__ = (
        UniqueConstraint(
            "chapter_id",
            "chunk_index",
            name="uq_text_chunks_chapter_index"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    token_count: Mapped[int | None] = mapped_column(Integer)

    # Позиция фрагмента в chapter.text: content == chapter.text[char_start:char_end]
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(String(64))

    chapter = relationship("Chapter", back_populates="chunks")