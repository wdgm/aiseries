import uuid

from sqlalchemy import JSON, BigInteger, Index, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Book(Base, TimestampMixin):
    __tablename__ = "books"
    __table_args__ = (
        Index("ix_books_project_sha256", "project_id", "file_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False
    )

    author: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(20))
    source_filename: Mapped[str | None] = mapped_column(String(500))
    source_file_uri: Mapped[str | None] = mapped_column(Text)

    # Целостность импорта (Phase 1.5)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    import_report: Mapped[dict | None] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(
        String(30),
        default="created",
        nullable=False
    )

    project = relationship("Project", back_populates="books")

    chapters = relationship(
        "Chapter",
        back_populates="book",
        cascade="all, delete-orphan"
    )