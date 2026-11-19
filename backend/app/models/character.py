
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.character_alias import CharacterAlias
    from app.models.character_mention import CharacterMention
    from app.models.character_attribute import CharacterAttribute
    from app.models.character_reference import CharacterReference
    from app.models.character_relationship import CharacterRelationship
    from app.models.character_version import CharacterVersion


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    canonical_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    character_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="person",
    )

    importance: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="secondary",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="discovered",
        index=True,
    )

    project: Mapped["Project"] = relationship(
        back_populates="characters",
    )

    aliases: Mapped[list["CharacterAlias"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
    )

    mentions: Mapped[list["CharacterMention"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
    )

    attributes: Mapped[list["CharacterAttribute"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
    )

    versions: Mapped[list["CharacterVersion"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        order_by="CharacterVersion.version_number",
    )

    references: Mapped[list["CharacterReference"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        order_by="CharacterReference.created_at.desc()",
    )

    relationships: Mapped[list["CharacterRelationship"]] = relationship(
        "CharacterRelationship",
        foreign_keys="CharacterRelationship.source_character_id",
        back_populates="source_character",
        cascade="all, delete-orphan",
    )

    target_relationships: Mapped[list["CharacterRelationship"]] = relationship(
        "CharacterRelationship",
        foreign_keys="CharacterRelationship.target_character_id",
        back_populates="target_character",
    )
