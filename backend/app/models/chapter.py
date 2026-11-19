import uuid

from sqlalchemy import String, Text, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"

    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "chapter_number",
            name="uq_chapters_book_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    chapter_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str | None] = mapped_column(Text)

    book = relationship("Book", back_populates="chapters")

    chunks = relationship(
        "TextChunk",
        back_populates="chapter",
        cascade="all, delete-orphan"
    )