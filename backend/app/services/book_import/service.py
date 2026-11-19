import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Book, Chapter, Project, TextChunk

from app.services.book_import.extractors import extract_book
from app.services.book_import.chapter_parser import parse_chapters
from app.services.book_import.chunking import split_into_chunks
from app.services.book_import.integrity import (
    IntegrityResult,
    check_chapter_chunks,
    check_coverage,
)


STORAGE_DIR = Path("/app/storage/books")


class DuplicateBookError(Exception):
    def __init__(self, book: Book):
        super().__init__(
            f"Эта книга уже загружена в проект (book_id={book.id}, «{book.title}»)"
        )
        self.book_id = book.id
        self.title = book.title


class ImportIntegrityError(ValueError):
    def __init__(self, result: IntegrityResult):
        super().__init__(
            "Проверка целостности импорта не пройдена: " + "; ".join(result.errors[:5])
        )
        self.result = result


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def find_duplicate(db: Session, project_id: uuid.UUID, file_sha256: str) -> Book | None:
    return db.scalar(
        select(Book)
        .where(Book.project_id == project_id, Book.file_sha256 == file_sha256)
        .order_by(Book.created_at)
        .limit(1)
    )


def import_book(
    db: Session,
    project_id: uuid.UUID,
    filename: str,
    data: bytes,
    title: str | None = None,
    author: str | None = None,
    language: str | None = None,
    storage_dir: Path | None = None,
) -> Book:
    if not data:
        raise ValueError("Загруженный файл пуст")

    if len(data) > 50 * 1024 * 1024:
        raise ValueError("Размер книги не должен превышать 50 МБ")

    project = db.get(Project, project_id)

    if project is None:
        raise ValueError("Проект не найден")

    file_sha256 = sha256_hex(data)

    # Сериализуем параллельные загрузки одного файла в проект (двойной клик).
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"import:{project_id}:{file_sha256}"},
    )
    duplicate = find_duplicate(db, project_id, file_sha256)
    if duplicate is not None:
        raise DuplicateBookError(duplicate)

    original_filename = filename.replace("\\", "/").split("/")[-1]

    extracted = extract_book(original_filename, data)

    parsed_chapters = parse_chapters(extracted.text)

    if not parsed_chapters:
        raise ValueError("Не удалось определить главы книги")

    # Нарезка и проверка целостности — ДО любых записей.
    integrity = IntegrityResult()
    planned = []
    for chapter_number, parsed in enumerate(parsed_chapters, start=1):
        chunks = split_into_chunks(parsed.text)
        check_chapter_chunks(chapter_number, parsed.text, chunks, integrity)
        planned.append((chapter_number, parsed, chunks))

    extracted_chars = len(extracted.text.strip())
    chapter_chars = sum(len(p.text.strip()) for p in parsed_chapters)
    check_coverage(extracted_chars, chapter_chars, integrity)

    if not integrity.ok:
        raise ImportIntegrityError(integrity)

    integrity.stats.update(
        {
            "extracted_chars": extracted_chars,
            "chapter_chars": chapter_chars,
            "chapters_count": len(planned),
            "chunks_count": sum(len(c) for _, _, c in planned),
            "text_sha256": sha256_hex(extracted.text),
        }
    )

    extension = Path(original_filename).suffix.lower()

    target_dir = storage_dir or STORAGE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    storage_filename = f"{uuid.uuid4()}{extension}"
    storage_path = target_dir / storage_filename

    book = Book(
        project_id=project_id,
        title=(
            title.strip()
            if title and title.strip()
            else extracted.title or Path(original_filename).stem
        ),
        author=(
            author.strip()
            if author and author.strip()
            else extracted.author
        ),
        language=(
            language.strip()
            if language and language.strip()
            else extracted.language
        ),
        source_filename=original_filename,
        source_file_uri=str(storage_path),
        file_sha256=file_sha256,
        file_size_bytes=len(data),
        import_report={
            "ok": True,
            "warnings": integrity.warnings,
            "stats": integrity.stats,
        },
        status="importing",
    )

    try:
        # Сохраняем оригинальный файл и сверяем хэш записанного.
        storage_path.write_bytes(data)
        if sha256_hex(storage_path.read_bytes()) != file_sha256:
            raise IOError("Хэш сохранённого файла не совпадает с загруженным")

        db.add(book)
        db.flush()

        for chapter_number, parsed, chunks in planned:
            chapter = Chapter(
                book_id=book.id,
                chapter_number=chapter_number,
                title=parsed.title,
                text=parsed.text,
            )

            db.add(chapter)
            db.flush()

            for chunk in chunks:
                db.add(
                    TextChunk(
                        chapter_id=chapter.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        content_sha256=sha256_hex(chunk.content),
                    )
                )

        book.status = "imported"

        db.commit()
        db.refresh(book)

        return book

    except Exception:
        db.rollback()

        if storage_path.exists():
            storage_path.unlink()

        raise
