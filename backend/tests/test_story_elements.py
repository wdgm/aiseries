"""Phase 2: второй проход — события и локации (настоящий Postgres, фейковая LLM).

    DATABASE_URL=postgresql+psycopg://... RUN_DB_TESTS=1 pytest tests/test_story_elements.py
"""

import json
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.ai.providers import LLMCall, LLMUsage
from app.core.database import SessionLocal
from app.models import (
    AIUsage,
    AnalysisJob,
    AnalysisTask,
    Chapter,
    EventParticipant,
    Location,
    LocationAlias,
    LocationMention,
    Project,
    StoryEvent,
    TaskStatus,
    TextChunk,
)
from app.services.book_analysis.queue import (
    JobStateError,
    claim_tasks,
    enqueue_analysis_job,
    enqueue_story_job,
    reclaim_own_tasks,
)
from app.services.book_import.service import import_book
from app.worker.runner import Worker

pytestmark = pytest.mark.skipif(not os.getenv("RUN_DB_TESTS"), reason="needs Postgres")

HEROES = ["Анна", "Борис", "Виктор"]
SENTENCE = re.compile(r"Предложение \d+-\d+-(\d+) о событиях в таверне\.")


def usage():
    return LLMUsage(input_tokens=3000, cached_input_tokens=1000, output_tokens=400, total_tokens=3400)


class FakeProvider:
    """Отвечает на оба типа промптов; все цитаты берутся из самого фрагмента."""

    name = "deepseek"
    model = "deepseek-flash"

    def __init__(self):
        self.character_calls = []
        self.story_calls = []
        self.story_prompts = []
        self.emitted_events = 0

    @staticmethod
    def _chunk_text(prompt: str) -> str:
        return prompt.split("ТЕКСТ ФРАГМЕНТА:", 1)[1]

    def complete_json(self, system_prompt, user_prompt, schema):
        text = self._chunk_text(user_prompt)
        marker = re.search(r"Маркер (\d+)-(\d+)", text)
        c = int(marker.group(1)) if marker else 0
        hero = HEROES[c % len(HEROES)]
        if "ИЗВЕСТНЫЕ ЛОКАЦИИ" in user_prompt:
            self.story_calls.append(marker.group(0) if marker else "?")
            self.story_prompts.append(user_prompt)
            found = [(m.group(0), int(m.group(1))) for m in SENTENCE.finditer(text)]
            sentences = [f for f, _ in found]
            # «Периодические» события — каждое 5-е предложение. Зона перекрытия
            # (500 символов ≈ 12 предложений) попадает в оба соседних фрагмента,
            # поэтому часть событий модель вернёт дважды — их надо отсеять.
            periodic = [f for f, i in found[1:] if i % 5 == 0]
            self.emitted_events += 1 + len(periodic)
            body = {
                "locations": [
                    {
                        "name": "Таверна «Дуб»",
                        "location_type": "BUILDING",
                        "aliases": ["таверна"],
                        "description": "таверна на перекрёстке",
                        "attributes": [
                            {"category": "atmosphere", "value": "шумно", "evidence": sentences[0]},
                            {"category": "geography", "value": "выдумка", "evidence": "этого нет в тексте"},
                        ],
                        "mentions": [{"quote": sentences[0]}, {"quote": sentences[-1]}],
                    }
                ],
                "events": [
                    {
                        "title": f"Начало {sentences[0][12:20]}",
                        "description": "что-то произошло",
                        "event_type": "plot",
                        "importance": "major",
                        "participants": [
                            {"name": hero, "role": "инициатор"},
                            {"name": "Незнакомец", "role": "свидетель"},
                        ],
                        "location_name": "таверна",
                        "time_text": "",
                        "evidence": sentences[0],
                    },
                    *[
                        {
                            "title": f"Периодическое {sentence[12:20]}",
                            "description": "ещё что-то",
                            "event_type": "strange",
                            "participants": [{"name": hero}],
                            "location_name": "Неизвестное место",
                            "evidence": sentence,
                        }
                        for sentence in periodic
                    ],
                    {
                        "title": "Выдумка",
                        "description": "не подтверждено",
                        "evidence": "такой цитаты в книге нет",
                    },
                ],
                "notes": [],
            }
        else:
            self.character_calls.append(marker.group(0) if marker else "?")
            body = {
                "characters": [
                    {
                        "name": hero,
                        "description": "тест",
                        "aliases": [f"{hero}-прозвище"],
                        "mentions": [{"quote": marker.group(0), "context": marker.group(0)}],
                    }
                ],
                "notes": [],
            }
        return LLMCall(
            text=json.dumps(body, ensure_ascii=False),
            usage=usage(),
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            latency_ms=5,
        )


class Factory:
    def __init__(self, provider):
        self.provider = provider

    def __call__(self):
        from app.services.book_analysis.analyzer import BookAnalyzer

        return BookAnalyzer(provider=self.provider)


def make_story_book(db, tmp_path, chapters=2, chunks_per_chapter=3):
    project = Project(name=f"story-{uuid.uuid4().hex[:6]}", status="active")
    db.add(project)
    db.commit()
    parts = []
    for c in range(1, chapters + 1):
        paragraphs = []
        for k in range(chunks_per_chapter):
            sentences = " ".join(
                f"Предложение {c}-{k}-{i} о событиях в таверне." for i in range(170)
            )
            paragraphs.append(f"Маркер {c}-{k}. {sentences}")
        parts.append(f"Глава {c}\n\n" + "\n\n".join(paragraphs))
    data = ("\n\n".join(parts) + f"\n\n{uuid.uuid4()}").encode()
    return import_book(db, project.id, "story.txt", data, title="Таверна", storage_dir=tmp_path)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def make_worker(provider):
    worker = Worker(analyzer_factory=Factory(provider))
    worker.worker_id = f"w-{uuid.uuid4().hex[:6]}"
    worker.ensure_analyzer()
    return worker


def run_until_idle(worker, max_steps=500):
    steps = 0
    while worker.run_once():
        steps += 1
        assert steps < max_steps


def jobs_of(db, book_id):
    db.expire_all()
    return db.scalars(
        select(AnalysisJob).where(AnalysisJob.book_id == book_id).order_by(AnalysisJob.created_at)
    ).all()


def test_story_requires_completed_characters(db, tmp_path):
    book = make_story_book(db, tmp_path, chapters=1, chunks_per_chapter=1)
    with pytest.raises(JobStateError):
        enqueue_story_job(db, book)
    db.rollback()


def test_full_pipeline_events_locations_dedupe(db, tmp_path):
    book = make_story_book(db, tmp_path)
    job = enqueue_analysis_job(db, book)
    db.commit()
    total = job.total_chunks
    assert total >= 6

    provider = FakeProvider()
    worker = make_worker(provider)
    run_until_idle(worker)

    jobs = jobs_of(db, book.id)
    assert [j.job_type for j in jobs] == ["characters", "story_elements"]
    chars_job, story_job = jobs
    assert chars_job.status == "completed"
    # второй проход поставлен автоматически и доведён до конца
    assert story_job.status == "completed", story_job.report
    assert story_job.processed_chunks == total
    assert len(provider.character_calls) == total
    assert len(provider.story_calls) == total

    # контекст: модель видит известных персонажей с псевдонимами, а после
    # первого фрагмента — и уже найденную локацию
    assert "Анна (Анна-прозвище)" in provider.story_prompts[0] or "Борис (Борис-прозвище)" in provider.story_prompts[0]
    assert "Таверна «Дуб»" in provider.story_prompts[-1]

    report = story_job.report
    # одна локация создана один раз, в остальных фрагментах сопоставлена
    assert report["locations_created"] == 1
    assert report["locations_matched"] == total - 1
    locations = db.scalars(select(Location).where(Location.project_id == book.project_id)).all()
    assert len(locations) == 1
    location = locations[0]
    assert location.location_type == "building"
    aliases = db.scalars(select(LocationAlias.alias).where(LocationAlias.location_id == location.id)).all()
    assert aliases == ["таверна"]

    # выдуманные цитаты отброшены: атрибут + событие в каждом фрагменте
    assert report["rejected_evidence"] == 2 * total
    # атрибут «шумно» сохранён один раз, а не в каждом фрагменте
    assert report["location_attributes"] == 1

    events = db.execute(
        select(StoryEvent, Chapter.chapter_number)
        .join(Chapter, StoryEvent.chapter_id == Chapter.id)
        .where(StoryEvent.book_id == book.id)
        .order_by(StoryEvent.timeline_position)
    ).all()
    assert report["events"] == len(events)
    # последнее предложение фрагмента попадает в перекрытие со следующим —
    # там оно же возвращается как «первое» и должно быть отброшено
    assert report["events_duplicate"] > 0
    assert report["events"] + report["events_duplicate"] == provider.emitted_events
    by_chapter: dict = {}
    for event, chapter_number in events:
        for s, e in by_chapter.get(chapter_number, []):
            assert not (s < event.end_position and event.start_position < e), "overlapping duplicate"
        by_chapter.setdefault(chapter_number, []).append((event.start_position, event.end_position))
    # порядок повествования: сначала глава 1, потом 2
    numbers = [n for _, n in events]
    assert numbers == sorted(numbers)

    # цитата и абсолютные позиции согласованы с текстом главы
    for event, _ in events:
        chapter_text = db.get(Chapter, event.chapter_id).text
        assert chapter_text[event.start_position:event.end_position] == event.evidence

    # локация события: алиас «таверна» → Таверна «Дуб»; неизвестная — сырое имя
    in_tavern = [e for e, _ in events if e.location_id == location.id]
    others = [e for e, _ in events if e.location_id is None]
    assert in_tavern and all(e.title.startswith("Начало") for e in in_tavern)
    assert others and all(e.raw_location_name == "Неизвестное место" for e in others)
    assert all(e.event_type == "plot" and e.time_text is None for e, _ in events)

    # участники: герой сопоставлен с персонажем, «Незнакомец» не создан как персонаж
    statuses = dict(
        db.execute(
            select(EventParticipant.raw_name, EventParticipant.match_status)
            .join(StoryEvent, EventParticipant.event_id == StoryEvent.id)
            .where(StoryEvent.book_id == book.id)
        ).all()
    )
    assert statuses["Незнакомец"] == "unknown"
    assert statuses["Борис"] == statuses["Виктор"] == "matched"
    assert report["participants_matched"] == report["events"]

    # упоминания локации без дублей по позиции
    mention_rows = db.execute(
        select(LocationMention.chapter_id, LocationMention.start_position).where(
            LocationMention.location_id == location.id
        )
    ).all()
    assert len(mention_rows) == len(set(mention_rows)) == report["location_mentions"]

    # расход второго прохода учтён отдельной операцией
    n, cost = db.execute(
        select(func.count(), func.sum(AIUsage.cost_usd)).where(
            AIUsage.job_id == story_job.id, AIUsage.operation == "extract_story_elements"
        )
    ).one()
    assert n == total and cost > 0
    assert story_job.cost_usd == cost

    # повторный запуск без force запрещён (не платим дважды)
    with pytest.raises(JobStateError):
        enqueue_story_job(db, book)
    db.rollback()

    # force: повторный проход не создаёт ни одного нового события и локации
    again = enqueue_story_job(db, book, force=True)
    db.commit()
    run_until_idle(worker)
    db.expire_all()
    again = db.get(AnalysisJob, again.id)
    assert again.status == "completed"
    assert again.report["events"] == 0 and again.report["locations_created"] == 0
    assert db.scalar(select(func.count()).select_from(StoryEvent).where(StoryEvent.book_id == book.id)) == len(events)


def test_story_task_crash_reuses_cached_llm_result(db, tmp_path):
    """Воркер упал после ответа LLM, но до сохранения: за LLM повторно не платим."""
    book = make_story_book(db, tmp_path, chapters=1, chunks_per_chapter=2)
    chars_job = enqueue_analysis_job(db, book)
    db.commit()
    provider = FakeProvider()
    worker = make_worker(provider)
    # Только персонажи; второй проход ставится автоматически, но его пока не берём.
    for _ in range(chars_job.total_chunks):
        assert worker.run_once()
    story_job = jobs_of(db, book.id)[-1]
    assert story_job.job_type == "story_elements" and story_job.status == "queued"

    with SessionLocal() as s:
        [task_id] = claim_tasks(s, worker.worker_id, limit=1)
    with SessionLocal() as s:
        task = s.get(AnalysisTask, task_id)
        assert task.task_type == "extract_story_elements"
        content = s.get(TextChunk, task.chunk_id).content
        # LLM ответила, ответ и расход зафиксированы…
        assert worker._call_llm(
            task_id, task.job_id, book.id, content, "extract_story_elements", book.project_id
        ) is not None
    assert len(provider.story_calls) == 1
    with SessionLocal() as s:
        assert s.get(AnalysisTask, task_id).llm_result is not None

    # …и тут воркер умер (kill -9) до сохранения. Рестарт с тем же WORKER_ID:
    with SessionLocal() as s:
        reclaim_own_tasks(s, worker.worker_id)
    run_until_idle(worker)

    db.expire_all()
    story_job = db.get(AnalysisJob, story_job.id)
    assert story_job.status == "completed"
    # Для упавшего фрагмента LLM повторно не вызывалась.
    assert len(provider.story_calls) == story_job.total_chunks
    assert story_job.report["events"] > 0


def test_story_api(db, tmp_path):
    from app.main import app

    book = make_story_book(db, tmp_path, chapters=1, chunks_per_chapter=2)
    client = TestClient(app)

    r = client.post(f"/api/v1/books/{book.id}/analyze/story-elements")
    assert r.status_code == 409  # персонажи ещё не проанализированы

    enqueue_analysis_job(db, book)
    db.commit()
    run_until_idle(make_worker(FakeProvider()))

    r = client.get(f"/api/v1/books/{book.id}/analysis-jobs")
    assert [j["job_type"] for j in r.json()] == ["story_elements", "characters"]
    assert r.json()[0]["progress_percent"] == 100

    r = client.get(f"/api/v1/books/{book.id}/events")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == len(data["items"]) > 0
    first = data["items"][0]
    assert first["chapter_number"] == 1 and first["participants"]
    assert {p["match_status"] for p in first["participants"]} == {"matched", "unknown"}

    hero_id = next(p["character_id"] for p in first["participants"] if p["character_id"])
    r = client.get(f"/api/v1/books/{book.id}/events", params={"character_id": hero_id})
    assert r.json()["total"] == data["total"]

    r = client.get(f"/api/v1/projects/{book.project_id}/locations")
    [loc] = r.json()
    assert loc["canonical_name"] == "Таверна «Дуб»" and loc["events_count"] > 0

    r = client.patch(f"/api/v1/locations/{loc['id']}", json={"canonical_name": "Трактир «Дуб»", "status": "confirmed"})
    assert r.status_code == 200
    body = r.json()
    assert body["canonical_name"] == "Трактир «Дуб»" and body["status"] == "confirmed"
    assert "Таверна «Дуб»" in body["aliases"]  # старое имя стало алиасом
    assert body["events"] and body["mentions"]

    r = client.patch(f"/api/v1/locations/{loc['id']}", json={"status": "bogus"})
    assert r.status_code == 422

    r = client.patch(f"/api/v1/events/{first['id']}", json={"status": "rejected"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/books/{book.id}/events").json()["total"] == data["total"] - 1
