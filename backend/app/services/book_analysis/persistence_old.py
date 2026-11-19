import re
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, select
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_mention import CharacterMention
from app.models.character_attribute import CharacterAttribute
from app.models.character_relationship import CharacterRelationship

from app.services.book_analysis.matching import (
    normalize_name,
    find_character_match,
)

def find_evidence_position(
    text: str,
    evidence: str,
) -> int | None:
    if not text or not evidence:
        return None

    evidence = evidence.strip()

    # Разбиваем цитату по многоточиям.
    parts = re.split(
        r"\s*(?:\.{3,}|…|\[\.\.\.\])\s*",
        evidence,
    )

    parts = [
        part.strip()
        for part in parts
        if len(part.strip()) >= 8
    ]

    if not parts:
        return None

    first_position = None

    for part in parts:
        # Пробелы в цитате могут отличаться от исходного текста.
        pattern = re.escape(part)
        pattern = re.sub(r"\\\s+", r"\\s+", pattern)

        match = re.search(pattern, text)

        if not match:
            return None

        if first_position is None:
            first_position = match.start()

    return first_position
def add_alias(
    db: Session,
    character: Character,
    alias: str,
) -> None:
    normalized = normalize_name(alias)

    if not normalized:
        return

    existing = db.scalar(
        select(CharacterAlias).where(
            CharacterAlias.character_id == character.id,
            CharacterAlias.normalized_alias == normalized,
        )
    )

    if existing:
        return

    db.add(
        CharacterAlias(
            character_id=character.id,
            alias=alias.strip(),
            normalized_alias=normalized,
            alias_type="detected",
        )
    )


def save_character(
    db: Session,
    project_id,
    data,
    chunk,
    character_map: dict,
    report: dict,
):
    match = find_character_match(
        db,
        project_id,
        data.name,
    )

    if match["status"] == "ambiguous":
        report["ambiguous"].append({
            "name": data.name,
            "candidates": [
                {
                    "name": item["character"].canonical_name,
                    "score": item["score"],
                }
                if isinstance(item, dict)
                else {
                    "name": item.canonical_name,
                    "score": 1.0,
                }
                for item in match["candidates"]
            ],
        })

        return None

    if match["status"] == "matched":
        character = match["character"]
        report["matched"] += 1

        # Не перезаписываем существующее описание.
        if not character.description and data.description:
            character.description = data.description

    else:
        character = Character(
            project_id=project_id,
            canonical_name=data.name.strip(),
            description=data.description,
            character_type=data.character_type,
            importance=data.importance,
            status="discovered",
        )

        db.add(character)
        db.flush()

        report["created"] += 1

    character_map[normalize_name(data.name)] = character

    for alias in data.aliases:
        add_alias(db, character, alias)

    # Сохраняем имя, если оно отличается от канонического.
    if normalize_name(data.name) != normalize_name(character.canonical_name):
        add_alias(db, character, data.name)

    # Характеристики.
    for item in data.attributes:
        evidence = item.evidence.strip()

        # Не сохраняем выдуманное или неточное доказательство.
        if not evidence or find_evidence_position(
            chunk.content,
            evidence,
        ) is None:
            report["rejected_evidence"] += 1
            continue

        existing = db.scalar(
            select(CharacterAttribute).where(
                CharacterAttribute.character_id == character.id,
                CharacterAttribute.attribute_key == item.category,
                CharacterAttribute.attribute_value == item.value,
                CharacterAttribute.source_chapter_id == chunk.chapter_id,
            )
        )

        if existing:
            continue

        db.add(
            CharacterAttribute(
                character_id=character.id,
                attribute_key=item.category,
                attribute_value=item.value,
                evidence=evidence,
                source_chapter_id=chunk.chapter_id,
                confidence=item.confidence,
                status="proposed",
            )
        )

        report["attributes"] += 1

    # Упоминания.
    for item in data.mentions:
        quote = item.quote.strip()

        if not quote:
            continue

        position = find_evidence_position(
            chunk.content,
            quote,
        )

        if position is None:
            report["rejected_evidence"] += 1
            continue

        existing = db.scalar(
            select(CharacterMention).where(
                CharacterMention.character_id == character.id,
                CharacterMention.chapter_id == chunk.chapter_id,
                CharacterMention.text_chunk_id == chunk.id,
                CharacterMention.mention_text == quote,
            )
        )

        if existing:
            continue

        db.add(
            CharacterMention(
                character_id=character.id,
                chapter_id=chunk.chapter_id,
                text_chunk_id=chunk.id,
                mention_text=quote,
                start_position=position,
                end_position=position + len(quote),
                confidence=item.confidence,
            )
        )

        report["mentions"] += 1

    return character


def save_relationships(
    db: Session,
    project_id,
    result,
    character_map: dict,
    chunk,
    report: dict,
) -> None:

    for source_data in result.characters:
        source = character_map.get(
            normalize_name(source_data.name)
        )

        if source is None:
            continue

        for item in source_data.relationships:
            target_name = normalize_name(
                item.target_character_name
            )

            target = character_map.get(target_name)

            if target is None:
                match = find_character_match(
                    db,
                    project_id,
                    item.target_character_name,
                )

                if match["status"] == "matched":
                    target = match["character"]

            if target is None or target.id == source.id:
                report["unresolved_relationships"] += 1
                continue

            evidence = item.evidence.strip()

            if not evidence or find_evidence_position(
                chunk.content,
                evidence,
            ) is None:
                report["rejected_evidence"] += 1
                continue

            # Не допускаем повтор одного отношения,
            # даже если ИИ вернул его в обратном направлении
            # с тем же доказательством.
            existing = db.scalar(
            select(CharacterRelationship).where(
                or_(
                    and_(
                        CharacterRelationship.source_character_id == source.id,
                        CharacterRelationship.target_character_id == target.id,
                        CharacterRelationship.relationship_type == item.relationship_type,
                    ),
                    and_(
                        CharacterRelationship.source_character_id == target.id,
                        CharacterRelationship.target_character_id == source.id,
                        CharacterRelationship.evidence == evidence,
                    ),
                )
            )
        )

            if existing:
                continue

            db.add(
                CharacterRelationship(
                    source_character_id=source.id,
                    target_character_id=target.id,
                    relationship_type=item.relationship_type,
                    description=item.description,
                    evidence=evidence,
                    confidence=item.confidence,
                    status="proposed",
                )
            )

            report["relationships"] += 1


def save_analysis_result(
    db: Session,
    project_id,
    result,
    chunk,
) -> dict:

    report = {
        "created": 0,
        "matched": 0,
        "attributes": 0,
        "mentions": 0,
        "relationships": 0,
        "ambiguous": [],
        "rejected_evidence": 0,
        "unresolved_relationships": 0,
    }

    character_map = {}

    # Сначала создаём или сопоставляем всех персонажей.
    for data in result.characters:
        save_character(
            db=db,
            project_id=project_id,
            data=data,
            chunk=chunk,
            character_map=character_map,
            report=report,
        )

    db.flush()

    # Затем сохраняем отношения.
    save_relationships(
        db=db,
        project_id=project_id,
        result=result,
        character_map=character_map,
        chunk=chunk,
        report=report,
    )

    db.flush()

    return report