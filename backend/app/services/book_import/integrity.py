"""Проверка целостности импорта: ни один символ главы не потерян при нарезке."""

from dataclasses import dataclass, field

from app.services.book_import.chunking import TextPart

# Если главы покрывают меньше доли извлечённого текста — предупреждение
# (нормально теряются только заголовки и служебные страницы).
MIN_CHAPTER_COVERAGE = 0.90


@dataclass
class IntegrityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_chapter_chunks(
    chapter_number: int, chapter_text: str, chunks: list[TextPart], result: IntegrityResult
) -> None:
    if chapter_text.strip() and not chunks:
        result.errors.append(f"Глава {chapter_number}: есть текст, но нет фрагментов")
        return

    covered_to = 0
    for chunk in chunks:
        if chapter_text[chunk.char_start:chunk.char_end] != chunk.content:
            result.errors.append(
                f"Глава {chapter_number}, фрагмент {chunk.index}: содержимое не совпадает с позицией"
            )
            continue
        gap = chapter_text[covered_to:chunk.char_start]
        if gap.strip():
            result.errors.append(
                f"Глава {chapter_number}: пропущено {len(gap.strip())} символов перед фрагментом {chunk.index}"
            )
        covered_to = max(covered_to, chunk.char_end)

    tail = chapter_text[covered_to:]
    if tail.strip():
        result.errors.append(
            f"Глава {chapter_number}: не покрыт хвост из {len(tail.strip())} символов"
        )


def check_coverage(extracted_chars: int, chapter_chars: int, result: IntegrityResult) -> None:
    ratio = (chapter_chars / extracted_chars) if extracted_chars else 0.0
    result.stats["chapter_coverage"] = round(ratio, 4)
    if ratio < MIN_CHAPTER_COVERAGE:
        result.warnings.append(
            f"Главы покрывают {ratio:.1%} извлечённого текста "
            f"(порог {MIN_CHAPTER_COVERAGE:.0%}): часть текста могла не попасть в главы"
        )
