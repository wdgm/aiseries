import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_mention import CharacterMention
from app.models.character_attribute import CharacterAttribute
from app.models.character_relationship import CharacterRelationship

from app.services.book_analysis.matching import (
    normalize_name,
    find_character_match,
)


def find_evidence_span(
    text: str,
    evidence: str,
) -> tuple[int, int] | None:
    """
    Ищет дословную цитату в исходном тексте.

    Допускает различия в пробелах и переносах строк.
    Возвращает реальные позиции в исходном тексте:
    (start_position, end_position).
    """

    if not text or not evidence:
        return None

    # Разбиваем текст на слова, сохраняя их исходные позиции.
    def normalize_with_positions(value: str):
        words = []
        positions = []

        for match in re.finditer(r"\S+", value):
            word = match.group()

            if words:
                positions.append(None)

            words.append(word)

            for index in range(match.start(), match.end()):
                positions.append(index)

        normalized = " ".join(words)

        return normalized, positions

    normalized_text, text_positions = normalize_with_positions(text)
    normalized_evidence, _ = normalize_with_positions(evidence)

    if not normalized_text or not normalized_evidence:
        return None

    # Ищем точное совпадение после нормализации пробелов.
    search_position = normalized_text.find(normalized_evidence)

    if search_position == -1:
        return None

    end_search_position = search_position + len(normalized_evidence)

    # Переводим нормализованные позиции обратно
    # в координаты исходного текста.
    actual_positions = [
        position
        for position in text_positions[
            search_position:end_search_position
        ]
        if position is not None
    ]

    if not actual_positions:
        return None

    return actual_positions[0], actual_positions[-1] + 1


def add_alias(
    db: Session,
    character: Character,
    alias: str,
) -> None:
    if not alias or not alias.strip():
        return

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
    normalized_name = normalize_name(data.name)

    if not normalized_name:
        return None

    # Если персонаж уже был обработан в этом фрагменте,
    # используем найденную запись.
    character = character_map.get(normalized_name)

    if character is None:
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

        character_map[normalized_name] = character

    # Добавляем псевдонимы.
    for alias in data.aliases:
        add_alias(db, character, alias)

    # Если обнаруженное имя отличается от канонического,
    # сохраняем его как обнаруженный псевдоним.
    if normalized_name != normalize_name(character.canonical_name):
        add_alias(db, character, data.name)

    # Дополнительные характеристики.
    for item in data.attributes:
        evidence = item.evidence.strip()

        span = find_evidence_span(
            chunk.content,
            evidence,
        )

        if span is None:
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

    # Упоминания персонажа.
    for item in data.mentions:
        quote = item.quote.strip()

        span = find_evidence_span(
            chunk.content,
            quote,
        )

        if span is None:
            report["rejected_evidence"] += 1
            continue

        start_position, end_position = span

        existing = db.scalar(
            select(CharacterMention).where(
                CharacterMention.character_id == character.id,
                CharacterMention.chapter_id == chunk.chapter_id,
                CharacterMention.text_chunk_id == chunk.id,
                CharacterMention.start_position == start_position,
                CharacterMention.end_position == end_position,
            )
        )

        if existing:
            continue

        db.add(
            CharacterMention(
                character_id=character.id,
                chapter_id=chunk.chapter_id,
                text_chunk_id=chunk.id,
                mention_text=chunk.content[
                    start_position:end_position
                ],
                start_position=start_position,
                end_position=end_position,
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

            # Если персонаж не найден среди обработанных
            # в текущем фрагменте, ищем его в проекте.
            if target is None:
                match = find_character_match(
                    db,
                    project_id,
                    item.target_character_name,
                )

                if match["status"] == "matched":
                    target = match["character"]

                elif match["status"] == "ambiguous":
                    candidates = []

                    for candidate in match["candidates"]:
                        if isinstance(candidate, dict):
                            candidate_character = candidate.get("character")
                            candidate_score = candidate.get("score", 1.0)
                        else:
                            candidate_character = candidate
                            candidate_score = 1.0

                        if candidate_character is not None:
                            candidates.append({
                                "name": candidate_character.canonical_name,
                                "score": candidate_score,
                            })

                    report["ambiguous"].append({
                        "name": item.target_character_name,
                        "candidates": candidates,
                    })

            if target is None or target.id == source.id:
                report["unresolved_relationships"] += 1
                continue

            evidence = item.evidence.strip()

            span = find_evidence_span(
                chunk.content,
                evidence,
            )

            if span is None:
                report["rejected_evidence"] += 1
                continue

            # Проверяем повтор именно этого направленного отношения.
            existing = db.scalar(
                select(CharacterRelationship).where(
                    CharacterRelationship.source_character_id == source.id,
                    CharacterRelationship.target_character_id == target.id,
                    CharacterRelationship.relationship_type == item.relationship_type,
                    CharacterRelationship.evidence == evidence,
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

    # Затем сохраняем отношения между ними.
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