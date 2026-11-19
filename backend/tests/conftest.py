import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_queue():
    """Интеграционные тесты считают вызовы LLM, поэтому очередь должна быть
    пустой: задачи, оставшиеся от предыдущих тестов/прогонов, отменяем."""
    if os.getenv("RUN_DB_TESTS"):
        from sqlalchemy import text

        from app.core.database import SessionLocal

        with SessionLocal() as db:
            db.execute(text(
                "UPDATE analysis_tasks SET status='cancelled' "
                "WHERE status IN ('pending','running')"
            ))
            db.execute(text(
                "UPDATE analysis_jobs SET status='cancelled' "
                "WHERE status IN ('queued','running','paused')"
            ))
            db.commit()
    yield
