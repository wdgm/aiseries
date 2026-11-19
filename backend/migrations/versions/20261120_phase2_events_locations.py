"""Phase 2: events and locations (second analysis pass)

Revision ID: 20261120phase2
Revises: 20261119phase15
Create Date: 2026-11-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261120phase2"
down_revision: Union[str, Sequence[str], None] = "20261119phase15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column("job_type", sa.String(40), nullable=False, server_default="characters"),
    )

    # ----------------------------------------------------------- locations
    op.create_table(
        "locations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_book_id", UUID, sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("location_type", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        *_ts(),
        sa.UniqueConstraint("project_id", "normalized_name", name="uq_locations_project_name"),
    )
    op.create_index("ix_locations_project_id", "locations", ["project_id"])
    op.create_index("ix_locations_status", "locations", ["status"])

    op.create_table(
        "location_aliases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("location_id", UUID, sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("normalized_alias", sa.String(300), nullable=False),
        *_ts(),
        sa.UniqueConstraint("location_id", "normalized_alias", name="uq_location_alias_normalized"),
    )
    op.create_index("ix_location_aliases_location_id", "location_aliases", ["location_id"])
    op.create_index("ix_location_aliases_normalized_alias", "location_aliases", ["normalized_alias"])

    op.create_table(
        "location_attributes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("location_id", UUID, sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text_chunk_id", UUID, sa.ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("start_position", sa.Integer(), nullable=True),
        sa.Column("end_position", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        *_ts(),
    )
    op.create_index("ix_location_attributes_location_id", "location_attributes", ["location_id"])
    op.create_index("ix_location_attributes_chapter_id", "location_attributes", ["chapter_id"])
    op.create_index("ix_location_attributes_dedupe", "location_attributes",
                    ["location_id", "category", "chapter_id"])

    op.create_table(
        "location_mentions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("location_id", UUID, sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text_chunk_id", UUID, sa.ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mention_text", sa.Text(), nullable=False),
        sa.Column("start_position", sa.Integer(), nullable=False),
        sa.Column("end_position", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        *_ts(),
        sa.UniqueConstraint("location_id", "chapter_id", "start_position",
                            name="uq_location_mentions_position"),
    )
    op.create_index("ix_location_mentions_location_id", "location_mentions", ["location_id"])
    op.create_index("ix_location_mentions_chapter_id", "location_mentions", ["chapter_id"])

    # -------------------------------------------------------------- events
    op.create_table(
        "story_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_id", UUID, sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text_chunk_id", UUID, sa.ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("location_id", UUID, sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("importance", sa.String(20), nullable=False),
        sa.Column("is_turning_point", sa.Boolean(), nullable=False),
        sa.Column("is_climax", sa.Boolean(), nullable=False),
        sa.Column("time_text", sa.Text(), nullable=True),
        sa.Column("cause", sa.Text(), nullable=True),
        sa.Column("consequences", sa.Text(), nullable=True),
        sa.Column("raw_location_name", sa.String(300), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("start_position", sa.Integer(), nullable=False),
        sa.Column("end_position", sa.Integer(), nullable=False),
        sa.Column("timeline_position", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        *_ts(),
    )
    op.create_index("ix_story_events_project_id", "story_events", ["project_id"])
    op.create_index("ix_story_events_book_id", "story_events", ["book_id"])
    op.create_index("ix_story_events_chapter_id", "story_events", ["chapter_id"])
    op.create_index("ix_story_events_location_id", "story_events", ["location_id"])
    op.create_index("ix_story_events_book_timeline", "story_events", ["book_id", "timeline_position"])

    op.create_table(
        "event_participants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("event_id", UUID, sa.ForeignKey("story_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_id", UUID, sa.ForeignKey("characters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("raw_name", sa.String(300), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("match_status", sa.String(20), nullable=False),
        *_ts(),
        sa.UniqueConstraint("event_id", "raw_name", name="uq_event_participants_name"),
    )
    op.create_index("ix_event_participants_event_id", "event_participants", ["event_id"])
    op.create_index("ix_event_participants_character_id", "event_participants", ["character_id"])


def downgrade() -> None:
    op.drop_table("event_participants")
    op.drop_table("story_events")
    op.drop_table("location_mentions")
    op.drop_table("location_attributes")
    op.drop_table("location_aliases")
    op.drop_table("locations")
    op.drop_column("analysis_jobs", "job_type")
