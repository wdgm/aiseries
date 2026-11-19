"""Phase 1.5: durable task queue, AI usage/cost tracking, import integrity

Revision ID: 20261119phase15
Revises: 20261110tokens
Create Date: 2026-11-19 00:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261119phase15"
down_revision: Union[str, Sequence[str], None] = "20261110tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_JOB_STATUSES = "('queued','running','paused')"

DEEPSEEK_PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"


def _ts():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def upgrade() -> None:
    # ---------------------------------------------------------------- books
    op.add_column("books", sa.Column("file_sha256", sa.String(64), nullable=True))
    op.add_column("books", sa.Column("file_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("books", sa.Column("import_report", sa.JSON(), nullable=True))
    # Не UNIQUE: в существующих данных уже могут быть дубли. Защита от
    # дублей — в сервисе импорта под advisory-lock.
    op.create_index("ix_books_project_sha256", "books", ["project_id", "file_sha256"])

    # ---------------------------------------------------------- text_chunks
    op.add_column("text_chunks", sa.Column("char_start", sa.Integer(), nullable=True))
    op.add_column("text_chunks", sa.Column("char_end", sa.Integer(), nullable=True))
    op.add_column("text_chunks", sa.Column("content_sha256", sa.String(64), nullable=True))

    # -------------------------------------------------------- analysis_jobs
    op.add_column("analysis_jobs", sa.Column(
        "cached_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("analysis_jobs", sa.Column(
        "cost_usd", sa.Numeric(14, 6), nullable=False, server_default="0"))
    op.add_column("analysis_jobs", sa.Column(
        "unpriced_requests", sa.Integer(), nullable=False, server_default="0"))

    # Старые задачи выполнялись в BackgroundTasks процесса API и после
    # рестарта навсегда зависали в pending/running.
    op.execute(
        "UPDATE analysis_jobs SET status='interrupted', "
        "last_error=COALESCE(last_error, 'Interrupted: legacy in-process job (before worker queue)') "
        "WHERE status IN ('pending','running')"
    )
    # Не больше одной активной задачи анализа на книгу.
    op.create_index(
        "uq_analysis_jobs_one_active_per_book", "analysis_jobs", ["book_id"],
        unique=True, postgresql_where=sa.text(f"status IN {ACTIVE_JOB_STATUSES}"),
    )

    # ------------------------------------------------------- analysis_tasks
    op.create_table(
        "analysis_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(40), nullable=False, server_default="analyze_chunk"),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("text_chunks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("chapter_number", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        # pending | running | succeeded | dead | cancelled
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_by", sa.String(200), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_class", sa.String(40), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        # Кэш валидного ответа LLM: если упало сохранение, повтор не платит за LLM второй раз.
        sa.Column("llm_result", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
        sa.UniqueConstraint("job_id", "chunk_id", name="uq_analysis_tasks_job_chunk"),
    )
    op.create_index("ix_analysis_tasks_job_status", "analysis_tasks", ["job_id", "status"])
    op.create_index(
        "ix_analysis_tasks_claim", "analysis_tasks", ["next_run_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_analysis_tasks_lease", "analysis_tasks", ["locked_until"],
        postgresql_where=sa.text("status = 'running'"),
    )

    # ------------------------------------------------------ provider_pricing
    pricing = op.create_table(
        "provider_pricing",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        # standard | peak | off_peak
        sa.Column("tier", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("input_cache_miss_per_1m", sa.Numeric(12, 6), nullable=False),
        sa.Column("input_cache_hit_per_1m", sa.Numeric(12, 6), nullable=True),
        sa.Column("output_per_1m", sa.Numeric(12, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        *_ts(),
        sa.UniqueConstraint("provider", "model", "tier", "valid_from", name="uq_provider_pricing_version"),
    )

    # Цены DeepSeek со страницы pricing на 2026-11-19 (USD за 1M токенов).
    valid_from = datetime(2026, 11, 19, tzinfo=timezone.utc)
    rows = []
    for model, tiers in {
        "deepseek-flash": {"peak": ("0.3", "0.006", "1.2"), "off_peak": ("0.15", "0.003", "0.6")},
        "deepseek-v4-pro": {"peak": ("1.32", "0.044", "3.96"), "off_peak": ("0.66", "0.022", "1.98")},
    }.items():
        for tier, (miss, hit, out) in tiers.items():
            rows.append({
                "id": uuid.uuid4(), "provider": "deepseek", "model": model, "tier": tier,
                "input_cache_miss_per_1m": miss, "input_cache_hit_per_1m": hit,
                "output_per_1m": out, "currency": "USD", "valid_from": valid_from,
                "valid_to": None, "source_url": DEEPSEEK_PRICING_SOURCE,
            })
    op.bulk_insert(pricing, rows)

    # -------------------------------------------------------------- ai_usage
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("analysis_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("analysis_tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("book_id", sa.Uuid(), sa.ForeignKey("books.id", ondelete="SET NULL"), nullable=True),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("response_model", sa.String(120), nullable=True),
        sa.Column("request_id", sa.String(200), nullable=True),
        # ok | invalid_output | error
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_class", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finish_reason", sa.String(40), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pricing_tier", sa.String(20), nullable=True),
        sa.Column("pricing_id", sa.Uuid(), sa.ForeignKey("provider_pricing.id", ondelete="SET NULL"), nullable=True),
        # NULL = цена неизвестна (нет строки в provider_pricing), а не «бесплатно».
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_usage_job_id", "ai_usage", ["job_id"])
    op.create_index("ix_ai_usage_task_id", "ai_usage", ["task_id"])
    op.create_index("ix_ai_usage_book_id", "ai_usage", ["book_id"])
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("provider_pricing")
    op.drop_table("analysis_tasks")
    op.drop_index("uq_analysis_jobs_one_active_per_book", table_name="analysis_jobs")
    op.drop_column("analysis_jobs", "unpriced_requests")
    op.drop_column("analysis_jobs", "cost_usd")
    op.drop_column("analysis_jobs", "cached_input_tokens")
    op.drop_column("text_chunks", "content_sha256")
    op.drop_column("text_chunks", "char_end")
    op.drop_column("text_chunks", "char_start")
    op.drop_index("ix_books_project_sha256", table_name="books")
    op.drop_column("books", "import_report")
    op.drop_column("books", "file_size_bytes")
    op.drop_column("books", "file_sha256")
