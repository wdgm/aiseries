"""Phase 2: API событий и локаций (второй проход анализа)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.routes import get_db
from app.models import (
    Book,
    Chapter,
    EventParticipant,
    Location,
    LocationAlias,
    LocationMention,
    StoryEvent,
)
from app.services.book_analysis.queue import (
    ActiveJobExistsError,
    JobStateError,
    enqueue_story_job,
)
from app.services.book_analysis.story_schemas import EVENT_IMPORTANCE, EVENT_TYPES, LOCATION_TYPES

router = APIRouter()

REVIEW_STATUSES = ("proposed", "confirmed", "rejected")
LOCATION_STATUSES = ("discovered", "confirmed", "rejected")


class LocationUpdate(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    location_type: str | None = None
    status: str | None = None


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    event_type: str | None = None
    importance: str | None = None
    is_turning_point: bool | None = None
    is_climax: bool | None = None
    status: str | None = None


def _check(value, allowed, field):
    if value is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"{field} must be one of {list(allowed)}")


# ------------------------------------------------------------------ jobs


@router.post("/api/v1/books/{book_id}/analyze/story-elements", tags=["book-analysis"])
def enqueue_story_elements(book_id: UUID, force: bool = False, db: Session = Depends(get_db)):
    """Поставить второй проход (события + локации). Нужен завершённый анализ персонажей."""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    try:
        job = enqueue_story_job(db, book, force=force)
        db.commit()
    except ActiveJobExistsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": "Book already has an active analysis job", "job_id": str(exc.job_id)},
        ) from exc
    except JobStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Book already has an active analysis job") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(job)
    return {
        "status": "queued",
        "book_id": str(book_id),
        "job_id": str(job.id),
        "job_type": job.job_type,
        "total_chunks": job.total_chunks,
    }


# ---------------------------------------------------------------- events


def serialize_event(event: StoryEvent, chapter_number: int | None = None) -> dict:
    return {
        "id": str(event.id),
        "book_id": str(event.book_id),
        "chapter_id": str(event.chapter_id),
        "chapter_number": chapter_number,
        "text_chunk_id": str(event.text_chunk_id) if event.text_chunk_id else None,
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "importance": event.importance,
        "is_turning_point": event.is_turning_point,
        "is_climax": event.is_climax,
        "time_text": event.time_text,
        "cause": event.cause,
        "consequences": event.consequences,
        "location": (
            {"id": str(event.location.id), "name": event.location.canonical_name}
            if event.location is not None
            else None
        ),
        "raw_location_name": event.raw_location_name,
        "participants": [
            {
                "character_id": str(p.character_id) if p.character_id else None,
                "name": p.character.canonical_name if p.character is not None else p.raw_name,
                "raw_name": p.raw_name,
                "role": p.role,
                "match_status": p.match_status,
            }
            for p in event.participants
        ],
        "evidence": event.evidence,
        "start_position": event.start_position,
        "end_position": event.end_position,
        "timeline_position": event.timeline_position,
        "confidence": event.confidence,
        "status": event.status,
    }


def _event_query():
    return (
        select(StoryEvent, Chapter.chapter_number)
        .join(Chapter, StoryEvent.chapter_id == Chapter.id)
        .options(
            selectinload(StoryEvent.participants).selectinload(EventParticipant.character),
            selectinload(StoryEvent.location),
        )
    )


@router.get("/api/v1/books/{book_id}/events", tags=["story"])
def list_book_events(
    book_id: UUID,
    chapter_number: int | None = None,
    importance: str | None = None,
    event_type: str | None = None,
    character_id: UUID | None = None,
    location_id: UUID | None = None,
    include_rejected: bool = False,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """События книги в порядке повествования (timeline_position)."""
    if db.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found")
    query = _event_query().where(StoryEvent.book_id == book_id)
    count_query = select(func.count()).select_from(StoryEvent).where(StoryEvent.book_id == book_id)
    filters = []
    if chapter_number is not None:
        filters.append(Chapter.chapter_number == chapter_number)
    if importance:
        filters.append(StoryEvent.importance == importance)
    if event_type:
        filters.append(StoryEvent.event_type == event_type)
    if location_id:
        filters.append(StoryEvent.location_id == location_id)
    if character_id:
        filters.append(
            StoryEvent.id.in_(
                select(EventParticipant.event_id).where(EventParticipant.character_id == character_id)
            )
        )
    if not include_rejected:
        filters.append(StoryEvent.status != "rejected")
    for f in filters:
        query = query.where(f)
    count_query = count_query.join(Chapter, StoryEvent.chapter_id == Chapter.id)
    for f in filters:
        count_query = count_query.where(f)

    rows = db.execute(
        query.order_by(StoryEvent.timeline_position).limit(min(limit, 1000)).offset(offset)
    ).all()
    return {
        "total": db.scalar(count_query),
        "items": [serialize_event(event, number) for event, number in rows],
    }


@router.patch("/api/v1/events/{event_id}", tags=["story"])
def update_event(event_id: UUID, payload: EventUpdate, db: Session = Depends(get_db)):
    event = db.get(StoryEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    data = payload.model_dump(exclude_unset=True)
    _check(data.get("event_type"), EVENT_TYPES, "event_type")
    _check(data.get("importance"), EVENT_IMPORTANCE, "importance")
    _check(data.get("status"), REVIEW_STATUSES, "status")
    for key, value in data.items():
        setattr(event, key, value)
    db.commit()
    row = db.execute(_event_query().where(StoryEvent.id == event_id)).one()
    return serialize_event(row[0], row[1])


# ------------------------------------------------------------- locations


def _location_counts(db: Session, location_ids: list) -> tuple[dict, dict]:
    if not location_ids:
        return {}, {}
    mentions = dict(
        db.execute(
            select(LocationMention.location_id, func.count())
            .where(LocationMention.location_id.in_(location_ids))
            .group_by(LocationMention.location_id)
        ).all()
    )
    events = dict(
        db.execute(
            select(StoryEvent.location_id, func.count())
            .where(StoryEvent.location_id.in_(location_ids), StoryEvent.status != "rejected")
            .group_by(StoryEvent.location_id)
        ).all()
    )
    return mentions, events


def serialize_location(location: Location, mentions: int = 0, events: int = 0) -> dict:
    return {
        "id": str(location.id),
        "project_id": str(location.project_id),
        "canonical_name": location.canonical_name,
        "location_type": location.location_type,
        "description": location.description,
        "status": location.status,
        "aliases": [a.alias for a in location.aliases],
        "attributes": [
            {
                "id": str(a.id),
                "category": a.category,
                "value": a.value,
                "evidence": a.evidence,
                "chapter_id": str(a.chapter_id),
                "confidence": a.confidence,
                "status": a.status,
            }
            for a in location.attributes
        ],
        "mentions_count": mentions,
        "events_count": events,
    }


@router.get("/api/v1/projects/{project_id}/locations", tags=["story"])
def list_project_locations(
    project_id: UUID,
    include_rejected: bool = False,
    db: Session = Depends(get_db),
):
    query = (
        select(Location)
        .where(Location.project_id == project_id)
        .options(selectinload(Location.aliases), selectinload(Location.attributes))
    )
    if not include_rejected:
        query = query.where(Location.status != "rejected")
    locations = db.scalars(query).all()
    mentions, events = _location_counts(db, [l.id for l in locations])
    items = [
        serialize_location(l, mentions.get(l.id, 0), events.get(l.id, 0)) for l in locations
    ]
    items.sort(key=lambda item: (-(item["mentions_count"] + item["events_count"]), item["canonical_name"]))
    return items


@router.get("/api/v1/locations/{location_id}", tags=["story"])
def get_location(location_id: UUID, db: Session = Depends(get_db)):
    location = db.scalar(
        select(Location)
        .where(Location.id == location_id)
        .options(selectinload(Location.aliases), selectinload(Location.attributes))
    )
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    mentions, events = _location_counts(db, [location.id])
    data = serialize_location(location, mentions.get(location.id, 0), events.get(location.id, 0))
    data["mentions"] = [
        {
            "id": str(m.id),
            "chapter_id": str(m.chapter_id),
            "mention_text": m.mention_text,
            "start_position": m.start_position,
            "end_position": m.end_position,
            "confidence": m.confidence,
        }
        for m in db.scalars(
            select(LocationMention)
            .where(LocationMention.location_id == location_id)
            .order_by(LocationMention.chapter_id, LocationMention.start_position)
            .limit(200)
        )
    ]
    data["events"] = [
        serialize_event(event, number)
        for event, number in db.execute(
            _event_query()
            .where(StoryEvent.location_id == location_id, StoryEvent.status != "rejected")
            .order_by(StoryEvent.timeline_position)
            .limit(200)
        ).all()
    ]
    return data


@router.patch("/api/v1/locations/{location_id}", tags=["story"])
def update_location(location_id: UUID, payload: LocationUpdate, db: Session = Depends(get_db)):
    from app.services.book_analysis.matching import normalize_name

    location = db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")
    data = payload.model_dump(exclude_unset=True)
    _check(data.get("location_type"), LOCATION_TYPES, "location_type")
    _check(data.get("status"), LOCATION_STATUSES, "status")
    if "canonical_name" in data and normalize_name(data["canonical_name"]) != location.normalized_name:
        # Старое имя не теряем — оно остаётся алиасом.
        old_name, old_normalized = location.canonical_name, location.normalized_name
        if not any(a.normalized_alias == old_normalized for a in location.aliases):
            location.aliases.append(
                LocationAlias(alias=old_name, normalized_alias=old_normalized)
            )
        location.normalized_name = normalize_name(data["canonical_name"])[:300]
    for key, value in data.items():
        setattr(location, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Location with this name already exists") from exc
    return get_location(location_id, db)
