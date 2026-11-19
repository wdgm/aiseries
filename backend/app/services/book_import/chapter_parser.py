import re
from dataclasses import dataclass


@dataclass
class ParsedChapter:
    title: str
    text: str


HEADING_PATTERN = re.compile(
    r"^\s*(?:"
    r"глава|главы|часть|книга|"
    r"chapter|part|book"
    r")\s+[\wIVXLCDMА-Яа-я.-]+.*$",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    text = text.replace("\ufeff", "")

    lines = [line.rstrip() for line in text.split("\n")]

    result = []
    previous_empty = False

    for line in lines:
        is_empty = not line.strip()

        if is_empty and previous_empty:
            continue

        result.append(line)
        previous_empty = is_empty

    return "\n".join(result).strip()


def parse_chapters(text: str) -> list[ParsedChapter]:
    text = normalize_text(text)

    if not text:
        raise ValueError("Нельзя разделить пустой текст на главы")

    lines = text.split("\n")

    headings = []

    for index, line in enumerate(lines):
        if HEADING_PATTERN.match(line):
            headings.append(index)

    if not headings:
        return [
            ParsedChapter(
                title="Полный текст",
                text=text,
            )
        ]

    chapters = []

    # Текст до первой найденной главы сохраняем отдельно.
    if headings[0] > 0:
        introduction = "\n".join(lines[:headings[0]]).strip()

        if introduction:
            chapters.append(
                ParsedChapter(
                    title="Вступление",
                    text=introduction,
                )
            )

    for position, start in enumerate(headings):
        end = (
            headings[position + 1]
            if position + 1 < len(headings)
            else len(lines)
        )

        title = lines[start].strip()
        chapter_text = "\n".join(lines[start + 1:end]).strip()

        if chapter_text:
            chapters.append(
                ParsedChapter(
                    title=title,
                    text=chapter_text,
                )
            )

    if not chapters:
        return [
            ParsedChapter(
                title="Полный текст",
                text=text,
            )
        ]

    return chapters