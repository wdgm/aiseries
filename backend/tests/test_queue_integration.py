"""Интеграционные тесты очереди на настоящем Postgres (с применёнными миграциями).

    DATABASE_URL=postgresql+psycopg://... RUN_DB_TESTS=1 pytest tests/test_queue_integration.py

LLM подменён фейком с provider=deepseek/model=deepseek-flash, чтобы сработал прайс.
"""

import json
import os
import re
import uuid

import httpx
import openai
import pytest
from sqlalchemy import func, select, text

from app.ai.providers import LLMCall, LLMUsage
from app.core.database import SessionLocal
from app.models import AIUsage, AnalysisJob, AnalysisTask, Book, Project, TaskStatus
from app.services.book_analysis.queue import enqueue_analysis_job, resume_job
from app.services.book_import.service import DuplicateBookError, import_book
from app.worker.runner import Worker

pytestmark = pytest.mark.skipif(not os.getenv("RUN_DB_TESTS"), reason="needs Postgres")


NAMES = ["Анна", "Борис", "Виктор", "Галина", "Дмитрий", "Елена", "Фёдор",
         "Захар", "Ирина", "Кирилл", "Лидия", "Марат", "Нина", "Олег"]


class FakeProvider:
    name = "deepseek"
    model = "deepseek-flash"

    def __init__(self):
        self.calls = []
        self.fail_next: list[Exception] = []

    def complete_json(self, system_prompt, user_prompt, schema):
        m = re.search(r"Маркер (\d+)-(\d+)", user_prompt)
        self.calls.append(m.group(0) if m else user_prompt[-60:])
        if self.fail_next:
            raise self.fail_next.pop(0)
        name = m.group(0) if m else "x"
        hero = NAMES[(int(m.group(1)) * 4 + int(m.group(2))) % len(NAMES)] if m else "Икс"
        body = {
            "characters": [
                {
                    "name": hero,
                    "description": "тест",
                    "mentions": [{"quote": name, "context": name, "confidence": 0.9}],
                }
            ],
            "notes": [],
        }
        return LLMCall(
            text=json.dumps(body, ensure_ascii=False),
            usage=LLMUsage(input_tokens=2000, cached_input_tokens=500, output_tokens=300, total_tokens=2300),
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            latency_ms=5,
        )


class FakeAnalyzerFactory:
    def __init__(self, provider):
        self.provider = provider

    def __call__(self):
        from app.services.book_analysis.analyzer import BookAnalyzer

        return BookAnalyzer(provider=self.provider)


def make_book(db, tmp_path, chapters=3, chunks_per_chapter=4) -> Book:
    project = Project(name=f"test-{uuid.uuid4().hex[:6]}", status="active")
    db.add(project)
    db.commit()
    parts = []
    for c in range(1, chapters + 1):
        body = []
        for k in range(chunks_per_chapter):
            # каждый абзац ~8000 символов -> отдельный фрагмент
            body.append((f"Маркер {c}-{k}. " + "Текст события. " * 480).strip())
        parts.append(f"Глава {c}\n\n" + "\n\n".join(body))
    data = ("\n\n".join(parts) + f"\n\n{uuid.uuid4()}").encode()
    return import_book(db, project.id, "book.txt", data, title="Тест", storage_dir=tmp_path), data


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def run_until_idle(worker, max_steps=500):
    steps = 0
    while worker.run_once():
        steps += 1
        assert steps < max_steps


def job_state(db, job_id):
    db.expire_all()
    job = db.get(AnalysisJob, job_id)
    counts = dict(
        db.execute(
            select(AnalysisTask.status, func.count()).where(AnalysisTask.job_id == job_id).group_by(AnalysisTask.status)
        ).all()
    )
    return job, counts


def test_duplicate_upload_rejected(db, tmp_path):
    book, data = make_book(db, tmp_path, chapters=1, chunks_per_chapter=1)
    assert book.file_sha256 and book.import_report["ok"]
    with pytest.raises(DuplicateBookError):
        import_book(db, book.project_id, "copy.txt", data, storage_dir=tmp_path)
    db.rollback()


def test_kill_worker_and_resume_only_remaining(db, tmp_path):
    """Критерий готовности Phase 1.5."""
    book, _ = make_book(db, tmp_path)
    job = enqueue_analysis_job(db, book)
    db.commit()
    total = job.total_chunks
    assert total >= 10

    provider = FakeProvider()
    worker = Worker(analyzer_factory=FakeAnalyzerFactory(provider))
    worker.worker_id = f"w-{uuid.uuid4().hex[:6]}"
    worker.ensure_analyzer()

    # обработали 5 фрагментов
    for _ in range(5):
        assert worker.run_once()

    # «убиваем» воркер: взял задачу и умер до обработки
    from app.services.book_analysis.queue import claim_tasks

    claimed = claim_tasks(db, worker.worker_id, limit=1)
    assert len(claimed) == 1
    # и ещё одна задача: LLM ответил, но сохранение не успело — кэш должен спасти от повторной оплаты
    claimed2 = claim_tasks(db, worker.worker_id, limit=1)
    t2 = db.get(AnalysisTask, claimed2[0])
    db.refresh(t2)
    with SessionLocal() as s:
        book_id = s.get(Book, book.id).id
    content = db.execute(text("select content from text_chunks where id=:i"), {"i": t2.chunk_id}).scalar()
    worker._call_llm(t2.id, job.id, book_id, content)
    calls_before_crash = len(provider.calls)
    assert calls_before_crash == 6

    job_mid, counts_mid = job_state(db, job.id)
    assert counts_mid[TaskStatus.SUCCEEDED] == 5
    assert counts_mid[TaskStatus.RUNNING] == 2

    # рестарт воркера с тем же WORKER_ID
    provider2 = FakeProvider()
    worker2 = Worker(analyzer_factory=FakeAnalyzerFactory(provider2))
    worker2.worker_id = worker.worker_id
    worker2.ensure_analyzer()
    from app.services.book_analysis.queue import reclaim_own_tasks

    reclaim_own_tasks(db, worker2.worker_id)
    run_until_idle(worker2)

    job_end, counts = job_state(db, job.id)
    assert job_end.status == "completed"
    assert counts == {TaskStatus.SUCCEEDED: total}
    # только оставшиеся: total - 5 готовых - 1 с кэшем LLM
    assert len(provider2.calls) == total - 6
    # ровно одна строка упоминаний на фрагмент (нет дублей после падения)
    usage_rows = db.scalar(select(func.count()).select_from(AIUsage).where(AIUsage.job_id == job.id))
    assert usage_rows == total
    assert job_end.cost_usd > 0
    assert job_end.unpriced_requests == 0
    assert job_end.input_tokens == 2000 * total
    assert job_end.report["created"] == total
    assert job_end.report["mentions"] == total
    from app.models import Character, CharacterMention

    mentions_in_db = db.scalar(
        select(func.count()).select_from(CharacterMention)
        .join(Character, Character.id == CharacterMention.character_id)
        .where(Character.project_id == book.project_id)
    )
    assert mentions_in_db == total
    print(f"\nchunks={total} cost_usd={job_end.cost_usd} report={ {k: job_end.report[k] for k in ('created','mentions','rejected_evidence')} }")


def test_transient_retry_and_billing_pause_resume(db, tmp_path):
    book, _ = make_book(db, tmp_path, chapters=1, chunks_per_chapter=3)
    job = enqueue_analysis_job(db, book)
    db.commit()

    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    provider = FakeProvider()
    provider.fail_next = [
        openai.InternalServerError("boom", response=httpx.Response(503, request=req), body=None),
    ]
    worker = Worker(analyzer_factory=FakeAnalyzerFactory(provider))
    worker.worker_id = f"w-{uuid.uuid4().hex[:6]}"
    worker.ensure_analyzer()

    assert worker.run_once()  # 503 -> retry с backoff
    _, counts = job_state(db, job.id)
    assert counts[TaskStatus.PENDING] == 3
    db.execute(text("update analysis_tasks set next_run_at=now() where job_id=:j"), {"j": job.id})
    db.commit()

    provider.fail_next = [openai.APIStatusError("Insufficient Balance", response=httpx.Response(402, request=req), body=None)]
    assert worker.run_once()
    job_p, counts = job_state(db, job.id)
    assert job_p.status == "paused" and "billing" in job_p.last_error
    assert not worker.run_once()  # на паузе ничего не берётся

    resume_job(db, job_p)
    db.commit()
    run_until_idle(worker)
    job_end, counts = job_state(db, job.id)
    assert job_end.status == "completed", counts
    errors = dict(db.execute(select(AIUsage.error_class, func.count()).where(AIUsage.job_id == job.id).group_by(AIUsage.error_class)).all())
    assert errors == {None: 3, "transient": 1, "billing": 1}
