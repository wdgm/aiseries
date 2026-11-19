from dataclasses import dataclass


@dataclass
class TextPart:
    index: int
    content: str
    token_count: int
    # Позиция в ИСХОДНОМ тексте: text[char_start:char_end] == content
    char_start: int = 0
    char_end: int = 0


def split_into_chunks(
    text: str,
    chunk_size: int = 8000,
    overlap: int = 500,
) -> list[TextPart]:
    if chunk_size <= 0:
        raise ValueError("chunk_size должен быть больше нуля")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap должен быть меньше chunk_size")

    base = len(text) - len(text.lstrip())
    text = text.strip()

    if not text:
        return []

    chunks = []
    start = 0
    index = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # По возможности не разрываем предложение или абзац.
        if end < len(text):
            boundary = text.rfind("\n\n", start, end)

            if boundary == -1:
                boundary = text.rfind(". ", start, end)

            if boundary > start + chunk_size // 2:
                end = boundary + 1

        raw = text[start:end]
        content = raw.strip()

        if content:
            lead = len(raw) - len(raw.lstrip())
            char_start = base + start + lead
            chunks.append(
                TextPart(
                    index=index,
                    content=content,
                    token_count=max(1, len(content) // 4),
                    char_start=char_start,
                    char_end=char_start + len(content),
                )
            )

            index += 1

        if end >= len(text):
            break

        start = max(start + 1, end - overlap)

    return chunks
