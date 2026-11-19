"""Сохранение результата второго прохода (события + локации) для одного фрагмента.

Правила качества:
- каждый атрибут, упоминание и событие подтверждено дословной цитатой; цитата не
  найдена в тексте фрагмента → факт отбрасывается (rejected_evidence);
- позиции цитат переводятся в абсолютные координаты главы — дубли из зоны
  перекрытия соседних фрагментов (500 символов) отбрасываются;
- локации сопоставляются точно (каноническое имя/алиас); похожее, но не точное
  совпадение не объединяется автоматически, а уходит в report["ambiguous"];
- второй проход НЕ создаёт персонажей: участник без однозначного совпадения
  сохраняется с raw_name и match_status = ambiguous | unknown.
"""

import uuid
from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai.errors import TaskDataError
from app.models import (
    Chapter,
    Character,
    CharacterAlias,
    CharacterMention,
    EventParticipant,
    Location,
    LocationAlias,
    LocationAttribute,
    LocationMention,
    StoryEvent,
)
from app.services.book_analysis.matching import normalize_name
from app.services.book_analysis.persistence import find_evidence_span
from app.services.book_analysis.story_schemas import StoryElementsResult

TIMELINE_CHAPTER_STRIDE = 1_000_000_000
LOCATION_FUZZY_THRESHOLD = 0.85


def empty_story_report() -> dict:
    return {
        "locations_created": 0,
        "locations_matched": 0,
        "location_attributes": 0,
        "location_mentions": 0,
        "events": 0,
        "events_duplicate": 0,
        "participants_matched": 0,
        "participants_unresolved": 0,
        "rejected_evidence": 0,
        "ambiguous": [],
    }


# ------------------------------------------------------------------ context


def load_story_context(
    db: Session,
    project_id,
    book_id,
    max_characters: int,
    max_locations: int,
) -> tuple[list[str], list[str]]:
    """Известные персонажи (самые упоминаемые в этой книге) и локации проекта."""
    mention_count = (
        select(CharacterMention.character_id, func.count().label("n"))
        .join(Chapter, CharacterMention.chapter_id == Chapter.id)
        .where(Chapter.book_id == book_id)
        .group_by(CharacterMention.character_id)
        .subquery()
    )
    characters = db.execute(
        select(Character.id, Character.canonical_name)
        .outerjoin(mention_count, mention_count.c.character_id == Character.id)
        .where(Character.project_id == project_id, Character.status != "rejected")
        .order_by(func.coalesce(mention_count.c.n, 0).desc(), Character.canonical_name)
        .limit(max_characters)
    ).all()
    aliases: dict = defaultdict(list)
    if characters:
        for character_id, alias in db.execute(
            select(CharacterAlias.character_id, CharacterAlias.alias).where(
                CharacterAlias.character_id.in_([c.id for c in characters])
            )
        ):
            aliases[character_id].append(alias)

    def fmt(name: str, names: list[str]) -> str:
        extra = [a for a in dict.fromkeys(names) if normalize_name(a) != normalize_name(name)]
        return f"{name} ({', '.join(extra[:6])})" if extra else name

    known_characters = [fmt(c.canonical_name, aliases[c.id]) for c in characters]

    loc_mentions = (
        select(LocationMention.location_id, func.count().label("n"))
        .group_by(LocationMention.location_id)
        .subquery()
    )
    locations = db.execute(
        select(Location.id, Location.canonical_name)
        .outerjoin(loc_mentions, loc_mentions.c.location_id == Location.id)
        .where(Location.project_id == project_id, Location.status != "rejected")
        .order_by(func.coalesce(loc_mentions.c.n, 0).desc(), Location.canonical_name)
        .limit(max_locations)
    ).all()
    loc_aliases: dict = defaultdict(list)
    if locations:
        for location_id, alias in db.execute(
            select(LocationAlias.location_id, LocationAlias.alias).where(
                LocationAlias.location_id.in_([l.id for l in locations])
            )
        ):
            loc_aliases[location_id].append(alias)
    known_locations = [fmt(l.canonical_name, loc_aliases[l.id]) for l in locations]
    return known_characters, known_locations


# ------------------------------------------------------------------ indexes


class NameIndex:
    """normalized name/alias → множество id сущностей проекта."""

    def __init__(self) -> None:
        self.by_name: dict[str, set] = defaultdict(set)
        self.canonical: dict = {}

    def add(self, entity_id, name: str, canonical: bool = False) -> None:
        key = normalize_name(name or "")
        if key:
            self.by_name[key].add(entity_id)
        if canonical:
            self.canonical[entity_id] = key

    def exact(self, name: str) -> set:
        return set(self.by_name.get(normalize_name(name or ""), set()))

    def similar(self, name: str, threshold: float) -> list[tuple]:
        key = normalize_name(name or "")
        result = []
        for entity_id, candidate in self.canonical.items():
            score = SequenceMatcher(None, key, candidate).ratio()
            a, b = set(key.split()), set(candidate.split())
            # Перекрытие слов учитываем только для составных имён,
            # иначе «Дом» совпадал бы с любым «Дом …».
            if len(a) > 1 and len(b) > 1:
                score = max(score, len(a & b) / min(len(a), len(b)))
            if score >= threshold:
                result.append((entity_id, round(score, 3)))
        return sorted(result, key=lambda item: -item[1])


def build_character_index(db: Session, project_id) -> tuple[NameIndex, dict]:
    index = NameIndex()
    names = {}
    for character_id, name in db.execute(
        select(Character.id, Character.canonical_name).where(
            Character.project_id == project_id, Character.status != "rejected"
        )
    ):
        index.add(character_id, name, canonical=True)
        names[character_id] = name
    for character_id, alias in db.execute(
        select(CharacterAlias.character_id, CharacterAlias.alias)
        .join(Character, CharacterAlias.character_id == Character.id)
        .where(Character.project_id == project_id, Character.status != "rejected")
    ):
        index.add(character_id, alias)
    return index, names


def build_location_index(db: Session, project_id) -> tuple[NameIndex, dict]:
    index = NameIndex()
    locations = {}
    for location in db.scalars(select(Location).where(Location.project_id == project_id)):
        index.add(location.id, location.canonical_name, canonical=True)
        locations[location.id] = location
    for location_id, alias in db.execute(
        select(LocationAlias.location_id, LocationAlias.alias)
        .join(Location, LocationAlias.location_id == Location.id)
        .where(Location.project_id == project_id)
    ):
        index.add(location_id, alias)
    return index, locations


# ------------------------------------------------------------------ helpers


def chunk_offset(db: Session, chunk) -> int:
    """Смещение фрагмента в chapter.text (для абсолютных позиций)."""
    if chunk.char_start is not None:
        return chunk.char_start
    chapter_text = db.scalar(select(Chapter.text).where(Chapter.id == chunk.chapter_id)) or ""
    position = chapter_text.find(chunk.content)
    if position < 0:
        raise TaskDataError(
            "Chunk position in chapter is unknown; run "
            "`python -m app.scripts.backfill_phase15`"
        )
    return position


def _add_location_alias(db: Session, location: Location, alias: str, index: NameIndex) -> None:
    normalized = normalize_name(alias or "")
    if not normalized or normalized == location.normalized_name:
        return
    db.execute(
        pg_insert(LocationAlias.__table__)
        .values(
            id=uuid.uuid4(),
            location_id=location.id,
            alias=alias.strip()[:300],
            normalized_alias=normalized[:300],
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_nothing(constraint="uq_location_alias_normalized")
    )
    index.add(location.id, alias)


# ------------------------------------------------------------------ main


def save_story_elements(
    db: Session,
    project_id,
    book_id,
    chapter_number: int,
    result: StoryElementsResult,
    chunk,
) -> dict:
    report = empty_story_report()
    offset = chunk_offset(db, chunk)
    content = chunk.content

    loc_index, locations = build_location_index(db, project_id)
    char_index, char_names = build_character_index(db, project_id)
    # Локации этого фрагмента: нормализованное имя/алиас из ответа → Location
    local_locations: dict[str, Location] = {}

    # ---------------------------------------------------------- locations
    for data in result.locations:
        name = (data.name or "").strip()
        normalized = normalize_name(name)
        if not normalized:
            continue

        location = local_locations.get(normalized)
        if location is None:
            candidates = loc_index.exact(name)
            for alias in data.aliases:
                if not candidates:
                    candidates = loc_index.exact(alias)
            if len(candidates) == 1:
                location = locations[next(iter(candidates))]
                report["locations_matched"] += 1
                if not location.description and data.description:
                    location.description = data.description
            elif len(candidates) > 1:
                report["ambiguous"].append({
                    "kind": "location",
                    "name": name,
                    "candidates": [{"name": locations[c].canonical_name, "score": 1.0} for c in candidates],
                })
                continue
            else:
                similar = loc_index.similar(name, LOCATION_FUZZY_THRESHOLD)
                if similar:
                    report["ambiguous"].append({
                        "kind": "location",
                        "name": name,
                        "candidates": [
                            {"name": locations[c].canonical_name, "score": score}
                            for c, score in similar[:5]
                        ],
                    })
                    continue
                location = Location(
                    project_id=project_id,
                    first_book_id=book_id,
                    canonical_name=name[:300],
                    normalized_name=normalized[:300],
                    location_type=data.location_type,
                    description=data.description,
                    status="discovered",
                )
                db.add(location)
                db.flush()
                locations[location.id] = location
                loc_index.add(location.id, name, canonical=True)
                report["locations_created"] += 1

        local_locations[normalized] = location
        for alias in data.aliases:
            _add_location_alias(db, location, alias, loc_index)
            if normalize_name(alias):
                local_locations[normalize_name(alias)] = location
        if normalized != location.normalized_name:
            _add_location_alias(db, location, name, loc_index)

        for item in data.attributes:
            evidence = item.evidence.strip()
            span = find_evidence_span(content, evidence)
            if span is None:
                report["rejected_evidence"] += 1
                continue
            value_key = normalize_name(item.value)
            exists = any(
                normalize_name(v) == value_key
                for (v,) in db.execute(
                    select(LocationAttribute.value).where(
                        LocationAttribute.location_id == location.id,
                        LocationAttribute.category == item.category,
                    )
                )
            )
            if exists:
                continue
            db.add(
                LocationAttribute(
                    location_id=location.id,
                    category=item.category,
                    value=item.value.strip(),
                    evidence=content[span[0]:span[1]],
                    chapter_id=chunk.chapter_id,
                    text_chunk_id=chunk.id,
                    start_position=offset + span[0],
                    end_position=offset + span[1],
                    confidence=item.confidence,
                )
            )
            report["location_attributes"] += 1

        for item in data.mentions:
            span = find_evidence_span(content, item.quote.strip())
            if span is None:
                report["rejected_evidence"] += 1
                continue
            inserted = db.execute(
                pg_insert(LocationMention.__table__)
                .values(
                    id=uuid.uuid4(),
                    location_id=location.id,
                    chapter_id=chunk.chapter_id,
                    text_chunk_id=chunk.id,
                    mention_text=content[span[0]:span[1]],
                    start_position=offset + span[0],
                    end_position=offset + span[1],
                    confidence=item.confidence,
                    created_at=func.now(),
                    updated_at=func.now(),
                )
                .on_conflict_do_nothing(constraint="uq_location_mentions_position")
                .returning(LocationMention.__table__.c.id)
            ).first()
            # rowcount у INSERT ... ON CONFLICT в psycopg ненадёжен (-1), считаем по RETURNING
            if inserted is not None:
                report["location_mentions"] += 1

    db.flush()

    # ------------------------------------------------------------- events
    existing_spans = db.execute(
        select(StoryEvent.start_position, StoryEvent.end_position).where(
            StoryEvent.chapter_id == chunk.chapter_id
        )
    ).all()

    for data in result.events:
        evidence = data.evidence.strip()
        span = find_evidence_span(content, evidence)
        if span is None:
            report["rejected_evidence"] += 1
            continue
        start, end = offset + span[0], offset + span[1]
        # Та же цитата (или её пересечение) в этой главе уже дала событие —
        # это дубль из зоны перекрытия фрагментов или повтор модели.
        if any(s < end and start < e for s, e in existing_spans):
            report["events_duplicate"] += 1
            continue

        location = None
        raw_location = None
        if data.location_name:
            key = normalize_name(data.location_name)
            location = local_locations.get(key)
            if location is None:
                candidates = loc_index.exact(data.location_name)
                if len(candidates) == 1:
                    location = locations[next(iter(candidates))]
            if location is None:
                raw_location = data.location_name.strip()[:300]

        event = StoryEvent(
            project_id=project_id,
            book_id=book_id,
            chapter_id=chunk.chapter_id,
            text_chunk_id=chunk.id,
            location_id=location.id if location else None,
            raw_location_name=raw_location,
            title=data.title.strip()[:300],
            description=data.description.strip(),
            event_type=data.event_type,
            importance=data.importance,
            is_turning_point=data.is_turning_point,
            is_climax=data.is_climax,
            time_text=data.time_text,
            cause=data.cause,
            consequences=data.consequences,
            evidence=content[span[0]:span[1]],
            start_position=start,
            end_position=end,
            timeline_position=chapter_number * TIMELINE_CHAPTER_STRIDE + start,
            confidence=data.confidence,
        )
        db.add(event)
        db.flush()
        existing_spans.append((start, end))
        report["events"] += 1

        seen_names: set[str] = set()
        seen_characters: set = set()
        for participant in data.participants:
            raw = (participant.name or "").strip()
            key = normalize_name(raw)
            if not key or key in seen_names:
                continue
            seen_names.add(key)
            candidates = char_index.exact(raw)
            if len(candidates) == 1:
                character_id = next(iter(candidates))
                if character_id in seen_characters:
                    continue
                seen_characters.add(character_id)
                status = "matched"
                report["participants_matched"] += 1
            else:
                character_id = None
                status = "ambiguous" if candidates else "unknown"
                report["participants_unresolved"] += 1
                if candidates:
                    report["ambiguous"].append({
                        "kind": "event_participant",
                        "name": raw,
                        "candidates": [{"name": char_names[c], "score": 1.0} for c in candidates],
                    })
            db.add(
                EventParticipant(
                    event_id=event.id,
                    character_id=character_id,
                    raw_name=raw[:300],
                    role=(participant.role or None) and participant.role[:100],
                    match_status=status,
                )
            )

    db.flush()
    return report
