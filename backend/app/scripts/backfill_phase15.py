"""Бэкфилл для книг, загруженных до Phase 1.5.

    docker compose exec backend python -m app.scripts.backfill_phase15 --dry-run
    docker compose exec backend python -m app.scripts.backfill_phase15

- books.file_sha256 / file_size_bytes из файла в storage;
- text_chunks.char_start / char_end / content_sha256 (поиск в chapter.text);
- отчёт о дублях (одинаковый sha256 в одном проекте). Дубли НЕ удаляются.
"""

import argparse
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Book, Chapter, TextChunk
from app.services.book_import.service import sha256_hex


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    stats = defaultdict(int)
    by_hash = defaultdict(list)

    try:
        for book in db.scalars(select(Book).order_by(Book.created_at)):
            path = Path(book.source_file_uri or "")
            if book.file_sha256 is None:
                if path.is_file():
                    data = path.read_bytes()
                    book.file_sha256 = sha256_hex(data)
                    book.file_size_bytes = len(data)
                    stats["books_hashed"] += 1
                else:
                    stats["books_missing_file"] += 1
                    print(f"[missing file] book={book.id} {book.title!r} {path}")
            if book.file_sha256:
                by_hash[(book.project_id, book.file_sha256)].append(book)

            for chapter in db.scalars(select(Chapter).where(Chapter.book_id == book.id)):
                cursor = 0
                chunks = db.scalars(
                    select(TextChunk)
                    .where(TextChunk.chapter_id == chapter.id)
                    .order_by(TextChunk.chunk_index)
                )
                for chunk in chunks:
                    if chunk.content_sha256 is None:
                        chunk.content_sha256 = sha256_hex(chunk.content)
                    if chunk.char_start is None:
                        pos = chapter.text.find(chunk.content, max(cursor - 1000, 0))
                        if pos == -1:
                            pos = chapter.text.find(chunk.content)
                        if pos == -1:
                            stats["chunks_unlocated"] += 1
                            continue
                        chunk.char_start = pos
                        chunk.char_end = pos + len(chunk.content)
                        stats["chunks_located"] += 1
                    cursor = chunk.char_end or cursor

        for (project_id, digest), books in by_hash.items():
            if len(books) > 1:
                stats["duplicate_groups"] += 1
                print(f"[duplicate] project={project_id} sha256={digest[:12]}…")
                for b in books:
                    print(f"    book={b.id} {b.title!r} created={b.created_at:%Y-%m-%d %H:%M}")

        if args.dry_run:
            db.rollback()
            print("DRY RUN — nothing written")
        else:
            db.commit()
    finally:
        db.close()

    for key, value in sorted(stats.items()):
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
