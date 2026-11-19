import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Book, Chapter, Project, TextChunk

from app.services.book_import.extractors import extract_book
from app.services.book_import.chapter_parser import parse_chapters
from app.services.book_import.chunking import split_into_chunks


STORAGE_DIR = Path("/app/storage/books")


def import_book(
    db: Session,
    project_id: uuid.UUID,
    filename: str,
    data: bytes,
    title: str | None = None,
    author: str | None = None,
    language: str | None = None,
) -> Book:
    if not data:
        raise ValueError("Загруженный файл пуст")

    if len(data) > 50 * 1024 * 1024:
        raise ValueError("Размер книги не должен превышать 50 МБ")

    project = db.get(Project, project_id)

    if project is None:
        raise ValueError("Проект не найден")

    original_filename = filename.replace("\\", "/").split("/")[-1]

    extracted = extract_book(original_filename, data)

    parsed_chapters = parse_chapters(extracted.text)

    if not parsed_chapters:
        raise ValueError("Не удалось определить главы книги")

    extension = Path(original_filename).suffix.lower()

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    storage_filename = f"{uuid.uuid4()}{extension}"
    storage_path = STORAGE_DIR / storage_filename

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
        status="importing",
    )

    try:
        # Сохраняем оригинальный файл.
        storage_path.write_bytes(data)

        db.add(book)
        db.flush()

        for chapter_number, parsed in enumerate(parsed_chapters, start=1):
            chapter = Chapter(
                book_id=book.id,
                chapter_number=chapter_number,
                title=parsed.title,
                text=parsed.text,
            )

            db.add(chapter)
            db.flush()

            chunks = split_into_chunks(parsed.text)

            for chunk in chunks:
                db.add(
                    TextChunk(
                        chapter_id=chapter.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        token_count=chunk.token_count,
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