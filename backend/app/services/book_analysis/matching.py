import re
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.character_alias import CharacterAlias


def normalize_name(value: str) -> str:
    """
    Приводит имя к нормализованному виду.
    """
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"[^\w\s-]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def find_character_match(
    db: Session,
    project_id,
    name: str,
) -> dict:
    """
    Ищет персонажа в рамках проекта.

    Возможные результаты:
    - matched: точное совпадение;
    - ambiguous: есть похожие персонажи;
    - new: совпадений нет.
    """

    normalized = normalize_name(name)

    characters = db.scalars(
        select(Character).where(
            Character.project_id == project_id
        )
    ).all()

    exact_matches = []

    for character in characters:
        if normalize_name(character.canonical_name) == normalized:
            exact_matches.append(character)

    aliases = db.scalars(
        select(CharacterAlias)
        .join(
            Character,
            CharacterAlias.character_id == Character.id,
        )
        .where(Character.project_id == project_id)
    ).all()

    for alias in aliases:
        alias_normalized = (
            alias.normalized_alias
            or normalize_name(alias.alias)
        )

        if alias_normalized == normalized:
            character = next(
                (
                    item
                    for item in characters
                    if item.id == alias.character_id
                ),
                None,
            )

            if character and character not in exact_matches:
                exact_matches.append(character)

    if len(exact_matches) == 1:
        return {
            "status": "matched",
            "character": exact_matches[0],
            "candidates": [],
        }

    if len(exact_matches) > 1:
        return {
            "status": "ambiguous",
            "character": None,
            "candidates": exact_matches,
        }

    similar = []

    for character in characters:
        candidate_name = normalize_name(character.canonical_name)

        score = SequenceMatcher(
            None,
            normalized,
            candidate_name,
        ).ratio()

        new_words = set(normalized.split())
        old_words = set(candidate_name.split())

        if new_words and old_words:
            word_overlap = (
                len(new_words & old_words)
                / min(len(new_words), len(old_words))
            )
            score = max(score, word_overlap)

        if score >= 0.80:
            similar.append({
                "character": character,
                "score": round(score, 3),
            })

    similar.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    if similar:
        return {
            "status": "ambiguous",
            "character": None,
            "candidates": similar,
        }

    return {
        "status": "new",
        "character": None,
        "candidates": [],
    }
