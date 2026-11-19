
import logging
from collections.abc import Generator
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, check_database
from app.models.project import Project
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.text_chunk import TextChunk
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_attribute import CharacterAttribute
from app.models.character_mention import CharacterMention
from app.models.character_reference import CharacterReference
from app.models.character_relationship import CharacterRelationship
from app.services.book_import.service import (
    DuplicateBookError,
    ImportIntegrityError,
    import_book,
)
from app.services.book_analysis.analyzer import BookAnalyzer
from app.services.book_analysis.persistence import save_analysis_result

from app.models import AIUsage, AnalysisJob, AnalysisTask
from app.services.book_analysis.queue import (
    ActiveJobExistsError,
    JobStateError,
    cancel_job,
    enqueue_analysis_job,
    refresh_job,
    resume_job,
)
logger = logging.getLogger(__name__)

router = APIRouter()


# Database

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Schemas

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class CharacterUpdate(BaseModel):
    description: str | None = None
    status: str | None = None
    character_type: str | None = None
    importance: str | None = None


class CharacterReferenceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: str | None = None
    notes: str | None = None


class CharacterReferenceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = None
    notes: str | None = None


def serialize_character_reference(reference: CharacterReference) -> dict:
    return {
        "id": str(reference.id),
        "character_id": str(reference.character_id),
        "title": reference.title,
        "url": reference.url,
        "notes": reference.notes,
    }


def serialize_character(character: Character) -> dict:
    return {
        "id": str(character.id),
        "project_id": str(character.project_id),
        "canonical_name": character.canonical_name,
        "description": character.description,
        "character_type": character.character_type,
        "importance": character.importance,
        "status": character.status,
        "aliases": [
            {
                "id": str(alias.id),
                "alias": alias.alias,
                "alias_type": alias.alias_type,
            }
            for alias in sorted(
                character.aliases,
                key=lambda item: item.alias.lower(),
            )
        ],
        "attributes": [
            {
                "id": str(attribute.id),
                "key": attribute.attribute_key,
                "value": attribute.attribute_value,
                "evidence": attribute.evidence,
                "confidence": attribute.confidence,
                "status": attribute.status,
                "source_chapter_id": (
                    str(attribute.source_chapter_id)
                    if attribute.source_chapter_id is not None
                    else None
                ),
            }
            for attribute in sorted(
                character.attributes,
                key=lambda item: (item.attribute_key.lower(), item.attribute_value.lower()),
            )
        ],
        "mentions": [
            {
                "id": str(mention.id),
                "chapter_id": str(mention.chapter_id),
                "text_chunk_id": (
                    str(mention.text_chunk_id)
                    if mention.text_chunk_id is not None
                    else None
                ),
                "mention_text": mention.mention_text,
                "start_position": mention.start_position,
                "end_position": mention.end_position,
                "confidence": mention.confidence,
            }
            for mention in sorted(
                character.mentions,
                key=lambda item: (item.chapter_id, item.start_position or 0),
            )
        ],
        "relationships": [
            {
                "id": str(relationship.id),
                "target_character_id": str(relationship.target_character_id),
                "target_character_name": (
                    relationship.target_character.canonical_name
                    if relationship.target_character is not None
                    else None
                ),
                "relationship_type": relationship.relationship_type,
                "description": relationship.description,
                "evidence": relationship.evidence,
                "confidence": relationship.confidence,
                "status": relationship.status,
            }
            for relationship in sorted(
                character.relationships,
                key=lambda item: item.relationship_type.lower(),
            )
        ],
        "target_relationships": [
            {
                "id": str(relationship.id),
                "source_character_id": str(relationship.source_character_id),
                "source_character_name": (
                    relationship.source_character.canonical_name
                    if relationship.source_character is not None
                    else None
                ),
                "relationship_type": relationship.relationship_type,
                "description": relationship.description,
                "evidence": relationship.evidence,
                "confidence": relationship.confidence,
                "status": relationship.status,
            }
            for relationship in sorted(
                character.target_relationships,
                key=lambda item: item.relationship_type.lower(),
            )
        ],
        "references": [
            serialize_character_reference(reference)
            for reference in sorted(
                character.references,
                key=lambda item: item.created_at or item.updated_at,
                reverse=True,
            )
        ],
    }


# Health

@router.get("/health", tags=["system"])
def health():
    return {
        "status": "ok",
        "service": "ai-series-backend",
    }


@router.get("/health/db", tags=["system"])
def database_health():
    try:
        check_database()
        return {
            "status": "ok",
            "database": "connected",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from exc


@router.get("/api/v1", tags=["system"])
def api_root():
    return {
        "name": "AI Series API",
        "version": "1.0.0",
        "message": "API foundation is running",
    }


# Projects

@router.post("/api/v1/projects", tags=["projects"])
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
):
    name = payload.name.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Project name cannot be empty",
        )

    project = Project(
        name=name,
        description=payload.description,
        status="active",
    )

    try:
        db.add(project)
        db.commit()
        db.refresh(project)

        return {
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "status": project.status,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("Project creation failed")
        raise HTTPException(
            status_code=500,
            detail="Project creation failed",
        ) from exc


@router.get("/api/v1/projects", tags=["projects"])
def list_projects(
    db: Session = Depends(get_db),
):
    projects = (
        db.query(Project)
        .order_by(Project.created_at.desc())
        .all()
    )

    return [
        {
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "status": project.status,
        }
        for project in projects
    ]


@router.get(
    "/api/v1/projects/{project_id}/characters",
    tags=["characters"],
)
def list_project_characters(
    project_id: UUID,
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    query = db.query(Character).filter(Character.project_id == project_id)

    if search:
        needle = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Character.canonical_name.ilike(needle),
                Character.description.ilike(needle),
                Character.character_type.ilike(needle),
                Character.importance.ilike(needle),
            )
        )

    if role:
        role_value = role.strip()
        query = query.filter(
            or_(
                Character.character_type.ilike(role_value),
                Character.importance.ilike(role_value),
                Character.status.ilike(role_value),
            )
        )

    if status:
        query = query.filter(Character.status.ilike(status.strip()))

    characters = query.order_by(Character.canonical_name.asc()).all()

    return [serialize_character(character) for character in characters]


@router.get(
    "/api/v1/characters/{character_id}",
    tags=["characters"],
)
def get_character(
    character_id: UUID,
    db: Session = Depends(get_db),
):
    character = db.query(Character).filter(Character.id == character_id).first()
    if character is None:
        raise HTTPException(
            status_code=404,
            detail="Character not found",
        )

    return serialize_character(character)


@router.patch(
    "/api/v1/characters/{character_id}",
    tags=["characters"],
)
def update_character(
    character_id: UUID,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
):
    character = db.query(Character).filter(Character.id == character_id).first()
    if character is None:
        raise HTTPException(
            status_code=404,
            detail="Character not found",
        )

    if payload.description is not None:
        character.description = payload.description.strip() or None
    if payload.status is not None:
        character.status = payload.status.strip() or character.status
    if payload.character_type is not None:
        character.character_type = payload.character_type.strip() or character.character_type
    if payload.importance is not None:
        character.importance = payload.importance.strip() or character.importance

    db.commit()
    db.refresh(character)

    return serialize_character(character)


@router.get(
    "/api/v1/characters/{character_id}/references",
    tags=["characters"],
)
def list_character_references(
    character_id: UUID,
    db: Session = Depends(get_db),
):
    character = db.query(Character).filter(Character.id == character_id).first()
    if character is None:
        raise HTTPException(
            status_code=404,
            detail="Character not found",
        )

    return [serialize_character_reference(reference) for reference in sorted(character.references, key=lambda item: (item.created_at or item.updated_at), reverse=True)]


@router.post(
    "/api/v1/characters/{character_id}/references",
    tags=["characters"],
)
def create_character_reference(
    character_id: UUID,
    payload: CharacterReferenceCreate,
    db: Session = Depends(get_db),
):
    character = db.query(Character).filter(Character.id == character_id).first()
    if character is None:
        raise HTTPException(
            status_code=404,
            detail="Character not found",
        )

    reference = CharacterReference(
        character_id=character.id,
        title=payload.title.strip(),
        url=(payload.url or "").strip() or None,
        notes=(payload.notes or "").strip() or None,
    )
    db.add(reference)
    db.commit()
    db.refresh(reference)

    return serialize_character_reference(reference)


@router.patch(
    "/api/v1/characters/{character_id}/references/{reference_id}",
    tags=["characters"],
)
def update_character_reference(
    character_id: UUID,
    reference_id: UUID,
    payload: CharacterReferenceUpdate,
    db: Session = Depends(get_db),
):
    reference = (
        db.query(CharacterReference)
        .filter(
            CharacterReference.id == reference_id,
            CharacterReference.character_id == character_id,
        )
        .first()
    )
    if reference is None:
        raise HTTPException(
            status_code=404,
            detail="Reference not found",
        )

    if payload.title is not None:
        reference.title = payload.title.strip() or reference.title
    if payload.url is not None:
        reference.url = (payload.url or "").strip() or None
    if payload.notes is not None:
        reference.notes = (payload.notes or "").strip() or None

    db.commit()
    db.refresh(reference)

    return serialize_character_reference(reference)


@router.delete(
    "/api/v1/characters/{character_id}/references/{reference_id}",
    tags=["characters"],
)
def delete_character_reference(
    character_id: UUID,
    reference_id: UUID,
    db: Session = Depends(get_db),
):
    reference = (
        db.query(CharacterReference)
        .filter(
            CharacterReference.id == reference_id,
            CharacterReference.character_id == character_id,
        )
        .first()
    )
    if reference is None:
        raise HTTPException(
            status_code=404,
            detail="Reference not found",
        )

    db.delete(reference)
    db.commit()
    return {"status": "deleted", "id": str(reference.id)}


# Create project and import book

@router.post(
    "/api/v1/projects/import-book",
    tags=["projects"],
)
def create_project_with_book(
    project_name: str = Form(...),
    file: UploadFile = File(...),
    description: str | None = Form(default=None),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    language: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    project_name = project_name.strip()

    if not project_name:
        raise HTTPException(
            status_code=400,
            detail="Project name cannot be empty",
        )

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required",
        )

    try:
        file_data = file.file.read()

        project = Project(
            name=project_name,
            description=description,
            status="active",
        )

        db.add(project)
        db.flush()

        book = import_book(
            db=db,
            project_id=project.id,
            filename=file.filename,
            data=file_data,
            title=title,
            author=author,
            language=language,
        )

        return {
            "status": "success",
            "message": "Project created and book imported successfully",
            "project": {
                "id": str(project.id),
                "name": project.name,
                "description": project.description,
                "status": project.status,
            },
            "book": {
                "id": str(book.id),
                "title": book.title,
                "author": book.author,
                "language": book.language,
                "source_filename": book.source_filename,
                "status": book.status,
                "chapters_count": len(book.chapters),
            },
        }

    except ImportIntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Import integrity check failed",
                "errors": exc.result.errors,
                "warnings": exc.result.warnings,
            },
        ) from exc

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        db.rollback()
        logger.exception("Project and book import failed")
        raise HTTPException(
            status_code=500,
            detail="Project creation or book import failed. Check backend logs.",
        ) from exc

    finally:
        file.file.close()


# List books in project

@router.get(
    "/api/v1/projects/{project_id}/books",
    tags=["books"],
)
def list_project_books(
    project_id: UUID,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(
        Project.id == project_id
    ).first()

    if not project:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    books = (
        db.query(Book)
        .filter(Book.project_id == project_id)
        .order_by(Book.created_at.desc())
        .all()
    )

    return [
        {
            "id": str(book.id),
            "title": book.title,
            "author": book.author,
            "language": book.language,
            "source_filename": book.source_filename,
            "status": book.status,
            "chapters_count": len(book.chapters),
        }
        for book in books
    ]


# List chapters in book

@router.get(
    "/api/v1/books/{book_id}/chapters",
    tags=["books"],
)
def list_book_chapters(
    book_id: UUID,
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(
        Book.id == book_id
    ).first()

    if not book:
        raise HTTPException(
            status_code=404,
            detail="Book not found",
        )

    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.chapter_number)
        .all()
    )

    return [
        {
            "id": str(chapter.id),
            "chapter_number": chapter.chapter_number,
            "title": chapter.title,
            "text_length": len(chapter.text or ""),
        }
        for chapter in chapters
    ]


# Read chapter

@router.get(
    "/api/v1/chapters/{chapter_id}",
    tags=["chapters"],
)
def get_chapter(
    chapter_id: UUID,
    db: Session = Depends(get_db),
):
    chapter = db.query(Chapter).filter(
        Chapter.id == chapter_id
    ).first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found",
        )

    chunks = (
        db.query(TextChunk)
        .filter(TextChunk.chapter_id == chapter_id)
        .order_by(TextChunk.chunk_index)
        .all()
    )

    return {
        "id": str(chapter.id),
        "book_id": str(chapter.book_id),
        "chapter_number": chapter.chapter_number,
        "title": chapter.title,
        "text": chapter.text,
        "text_length": len(chapter.text or ""),
        "chunks": [
            {
                "id": str(chunk.id),
                "chunk_index": chunk.chunk_index,
                "token_count": chunk.token_count,
                "content": chunk.content,
            }
            for chunk in chunks
        ],
    }


# Import book into existing project

@router.post(
    "/api/v1/projects/{project_id}/books/import",
    tags=["books"],
)
def import_book_endpoint(
    project_id: UUID,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    language: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename is required",
        )

    try:
        file_data = file.file.read()

        book = import_book(
            db=db,
            project_id=project_id,
            filename=file.filename,
            data=file_data,
            title=title,
            author=author,
            language=language,
        )
        # Ставим анализ в очередь; выполняет его сервис worker.
        analysis_job = enqueue_analysis_job(db, book)
        db.commit()
        db.refresh(analysis_job)

        return {
            "status": "success",
            "message": "Book imported successfully",
            "book": {
                "id": str(book.id),
                "project_id": str(book.project_id),
                "title": book.title,
                "author": book.author,
                "language": book.language,
                "source_filename": book.source_filename,
                "status": book.status,
                "chapters_count": len(book.chapters),
                "file_sha256": book.file_sha256,
                "import_report": book.import_report,
            },
            "analysis": {
                "job_id": str(analysis_job.id),
                "status": analysis_job.status,
                "total_chunks": analysis_job.total_chunks,
                "message": "Book analysis queued",
            },
        }

    except DuplicateBookError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_book_id": str(exc.book_id),
            },
        ) from exc

    except ImportIntegrityError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Import integrity check failed",
                "errors": exc.result.errors,
                "warnings": exc.result.warnings,
            },
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception("Book import failed")
        raise HTTPException(
            status_code=500,
            detail="Book import failed. Check backend logs.",
        ) from exc

    finally:
        file.file.close()
# Book analysis preview (no database writes)

@router.post(
    "/api/v1/books/{book_id}/analyze/preview",
    tags=["book-analysis"],
)
def preview_book_analysis(
    book_id: UUID,
    chapter_number: int,
    chunk_index: int = 0,
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(
        Book.id == book_id
    ).first()

    if not book:
        raise HTTPException(
            status_code=404,
            detail="Book not found",
        )

    chapter = db.query(Chapter).filter(
        Chapter.book_id == book_id,
        Chapter.chapter_number == chapter_number,
    ).first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail=f"Chapter {chapter_number} not found",
        )

    chunk = db.query(TextChunk).filter(
        TextChunk.chapter_id == chapter.id,
        TextChunk.chunk_index == chunk_index,
    ).first()

    if not chunk:
        raise HTTPException(
            status_code=404,
            detail=f"Chunk {chunk_index} not found in chapter {chapter_number}",
        )

    try:
        analyzer = BookAnalyzer()

        result, usage = analyzer.analyze_chunk_with_usage(
            text=chunk.content,
            chapter_number=chapter.chapter_number,
            chunk_index=chunk.chunk_index,
            db=db,
            operation="analyze_preview",
            book_id=book.id,
        )

        return {
            "status": "success",
            "saved_to_database": False,
            "book": book.title,
            "chapter": {
                "number": chapter.chapter_number,
                "title": chapter.title,
            },
            "chunk": {
                "index": chunk.chunk_index,
                "token_count": chunk.token_count,
            },
            "analysis": result.model_dump(mode="json"),
            "usage": usage,
        }

    except Exception as exc:
        logger.exception("Book analysis preview failed")

        raise HTTPException(
            status_code=502,
            detail="AI analysis failed. Check backend logs.",
        ) from exc
@router.post(
    "/api/v1/books/{book_id}/analyze/reanalyze",
    tags=["book-analysis"],
)
def reanalyze_book(
    book_id: UUID,
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(
            status_code=404,
            detail="Book not found",
        )

    try:
        job = enqueue_analysis_job(db, book)
        db.commit()
    except ActiveJobExistsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Book already has an active analysis job; resume or cancel it",
                "job_id": str(exc.job_id),
            },
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Book already has an active analysis job") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.refresh(job)

    return {
        "status": "queued",
        "book_id": str(book.id),
        "job_id": str(job.id),
        "message": "Book re-analysis queued (all chunks)",
        "total_chunks": job.total_chunks,
    }


@router.post(
    "/api/v1/books/{book_id}/analyze/chunk",
    tags=["book-analysis"],
)
def analyze_and_save_chunk(
    book_id: UUID,
    chapter_number: int,
    chunk_index: int = 0,
    db: Session = Depends(get_db),
):
    book = db.query(Book).filter(
        Book.id == book_id
    ).first()

    if not book:
        raise HTTPException(
            status_code=404,
            detail="Book not found",
        )

    chapter = db.query(Chapter).filter(
        Chapter.book_id == book_id,
        Chapter.chapter_number == chapter_number,
    ).first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail="Chapter not found",
        )

    chunk = db.query(TextChunk).filter(
        TextChunk.chapter_id == chapter.id,
        TextChunk.chunk_index == chunk_index,
    ).first()

    if not chunk:
        raise HTTPException(
            status_code=404,
            detail="Text chunk not found",
        )

    try:
        analyzer = BookAnalyzer()

        result, usage = analyzer.analyze_chunk_with_usage(
            text=chunk.content,
            chapter_number=chapter.chapter_number,
            chunk_index=chunk.chunk_index,
            db=db,
            operation="analyze_chunk_manual",
            book_id=book.id,
        )

        report = save_analysis_result(
            db=db,
            project_id=book.project_id,
            result=result,
            chunk=chunk,
        )

        db.commit()

        return {
            "status": "success",
            "saved_to_database": True,
            "book": book.title,
            "chapter_number": chapter.chapter_number,
            "chunk_index": chunk.chunk_index,
            "report": report,
            "usage": usage,
        }

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=502,
            detail=f"Analysis or database save failed: {str(exc)}",
        ) from exc
# Analysis job status and report

@router.get(
    "/api/v1/analysis-jobs/{job_id}",
    tags=["book-analysis"],
)
def get_analysis_job(
    job_id: UUID,
    db: Session = Depends(get_db),
):
    job = db.query(AnalysisJob).filter(
        AnalysisJob.id == job_id
    ).first()

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis job not found",
        )

    return {
        "id": str(job.id),
        "book_id": str(job.book_id),
        "job_type": job.job_type,
        "status": job.status,
        "total_chunks": job.total_chunks,
        "processed_chunks": job.processed_chunks,
        "failed_chunks": job.failed_chunks,
        "input_tokens": job.input_tokens,
        "cached_input_tokens": job.cached_input_tokens,
        "output_tokens": job.output_tokens,
        "total_tokens": job.total_tokens,
        "cost_usd": str(job.cost_usd),
        "cost_complete": job.unpriced_requests == 0,
        "unpriced_requests": job.unpriced_requests,
        "progress_percent": (
            round(
                (job.processed_chunks + job.failed_chunks)
                / job.total_chunks * 100,
                1,
            )
            if job.total_chunks > 0
            else 0
        ),
        "current_chapter_number": job.current_chapter_number,
        "current_chunk_index": job.current_chunk_index,
        "report": job.report,
        "last_error": job.last_error,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


# ---------------------------------------------------------------- Phase 1.5
# Управление очередью анализа


def _get_job_or_404(db: Session, job_id: UUID) -> AnalysisJob:
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


@router.post("/api/v1/analysis-jobs/{job_id}/resume", tags=["book-analysis"])
def resume_analysis_job(job_id: UUID, db: Session = Depends(get_db)):
    """Продолжить задачу: обработать только незавершённые/упавшие фрагменты."""
    job = _get_job_or_404(db, job_id)
    try:
        result = resume_job(db, job)
        db.commit()
    except ActiveJobExistsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "job_id": str(exc.job_id)},
        ) from exc
    except JobStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Book already has an active analysis job") from exc
    refresh_job(db, job_id)
    return {"status": "queued", "job_id": str(job_id), **result}


@router.post("/api/v1/analysis-jobs/{job_id}/cancel", tags=["book-analysis"])
def cancel_analysis_job(job_id: UUID, db: Session = Depends(get_db)):
    job = _get_job_or_404(db, job_id)
    try:
        result = cancel_job(db, job)
        db.commit()
    except JobStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    refresh_job(db, job_id)
    return {"status": "cancelled", "job_id": str(job_id), **result}


@router.get("/api/v1/analysis-jobs/{job_id}/tasks", tags=["book-analysis"])
def list_analysis_tasks(
    job_id: UUID,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    _get_job_or_404(db, job_id)
    query = select(AnalysisTask).where(AnalysisTask.job_id == job_id)
    if status:
        query = query.where(AnalysisTask.status == status)
    tasks = db.scalars(
        query.order_by(AnalysisTask.chapter_number, AnalysisTask.chunk_index)
        .limit(min(limit, 500))
        .offset(offset)
    ).all()
    return [
        {
            "id": str(t.id),
            "chapter_number": t.chapter_number,
            "chunk_index": t.chunk_index,
            "status": t.status,
            "attempts": t.attempts,
            "max_attempts": t.max_attempts,
            "next_run_at": t.next_run_at,
            "locked_by": t.locked_by,
            "error_class": t.error_class,
            "last_error": t.last_error,
            "has_cached_llm_result": t.llm_result is not None,
            "report": t.report,
            "started_at": t.started_at,
            "finished_at": t.finished_at,
        }
        for t in tasks
    ]


@router.get("/api/v1/books/{book_id}/analysis-jobs", tags=["book-analysis"])
def list_book_analysis_jobs(book_id: UUID, db: Session = Depends(get_db)):
    jobs = db.scalars(
        select(AnalysisJob)
        .where(AnalysisJob.book_id == book_id)
        .order_by(AnalysisJob.created_at.desc())
    ).all()
    return [
        {
            "id": str(j.id),
            "job_type": j.job_type,
            "status": j.status,
            "progress_percent": (
                round((j.processed_chunks + j.failed_chunks) / j.total_chunks * 100, 1)
                if j.total_chunks
                else 0
            ),
            "total_chunks": j.total_chunks,
            "processed_chunks": j.processed_chunks,
            "failed_chunks": j.failed_chunks,
            "cost_usd": str(j.cost_usd),
            "last_error": j.last_error,
            "created_at": j.created_at,
            "completed_at": j.completed_at,
        }
        for j in jobs
    ]


@router.get("/api/v1/ai-usage/summary", tags=["ai-usage"])
def ai_usage_summary(
    job_id: UUID | None = None,
    book_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    """Расходы по провайдеру/модели/операции/статусу. cost_usd=null — цена неизвестна."""
    query = select(
        AIUsage.provider,
        AIUsage.model,
        AIUsage.operation,
        AIUsage.status,
        func.count().label("requests"),
        func.sum(AIUsage.input_tokens).label("input_tokens"),
        func.sum(AIUsage.cached_input_tokens).label("cached_input_tokens"),
        func.sum(AIUsage.output_tokens).label("output_tokens"),
        func.sum(AIUsage.reasoning_tokens).label("reasoning_tokens"),
        func.sum(AIUsage.cost_usd).label("cost_usd"),
        func.count().filter(AIUsage.cost_usd.is_(None)).label("unpriced_requests"),
        func.avg(AIUsage.latency_ms).label("avg_latency_ms"),
    ).group_by(AIUsage.provider, AIUsage.model, AIUsage.operation, AIUsage.status)
    if job_id:
        query = query.where(AIUsage.job_id == job_id)
    if book_id:
        query = query.where(AIUsage.book_id == book_id)
    rows = db.execute(query).mappings().all()
    return [
        {
            **{k: v for k, v in row.items() if k not in ("cost_usd", "avg_latency_ms")},
            "cost_usd": str(row["cost_usd"]) if row["cost_usd"] is not None else None,
            "avg_latency_ms": int(row["avg_latency_ms"]) if row["avg_latency_ms"] else None,
        }
        for row in rows
    ]
