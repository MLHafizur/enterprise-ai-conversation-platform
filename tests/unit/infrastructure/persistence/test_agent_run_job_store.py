from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    _pending_claim_statement,
)


def test_pending_claim_query_uses_nonblocking_postgres_row_lock() -> None:
    statement = _pending_claim_statement(
        now=datetime(2026, 11, 13, 18, 0, tzinfo=UTC),
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "outbox_events.published_at IS NULL" in sql
    assert "outbox_events.available_at <=" in sql
    assert "outbox_events.lease_token IS NULL" in sql
    assert "outbox_events.lease_expires_at <=" in sql
    assert "ORDER BY chat.outbox_events.available_at" in sql
    assert "LIMIT 1" in sql
