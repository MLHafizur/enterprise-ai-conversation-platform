from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from enterprise_chat_platform.application.process_agent_run import ClaimedAgentRun
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
)
from enterprise_chat_platform.domain.prompting import PromptRole
from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    DEFAULT_HISTORY_ROW_CAP,
    MAX_HISTORY_ROW_CAP,
    SqlAlchemyAgentRunJobStore,
    _context_messages_statement,
    _prompt_turn_from_record,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.models import MessageRecord


def _session_factory() -> AsyncSessionFactory:
    return cast(AsyncSessionFactory, object())


def _record(*, role: str, sequence: int = 7) -> MessageRecord:
    return MessageRecord(
        tenant_id=uuid4(),
        id=uuid4(),
        conversation_id=uuid4(),
        author_id=uuid4(),
        role=role,
        content=f"content-{sequence}",
        sequence=sequence,
        idempotency_key=f"context-{sequence}",
    )


def _job() -> ClaimedAgentRun:
    return ClaimedAgentRun(
        event_id=EventId.new(),
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        agent_run_id=AgentRunId.new(),
        input_message_id=MessageId.new(),
        input_content="current input",
        lease_token=uuid4(),
        attempt_count=1,
    )


def test_history_row_cap_has_a_bounded_default_and_accepts_boundaries() -> None:
    assert DEFAULT_HISTORY_ROW_CAP == 20
    assert MAX_HISTORY_ROW_CAP == 50

    SqlAlchemyAgentRunJobStore(_session_factory())
    SqlAlchemyAgentRunJobStore(_session_factory(), history_row_cap=1)
    SqlAlchemyAgentRunJobStore(
        _session_factory(),
        history_row_cap=MAX_HISTORY_ROW_CAP,
    )


@pytest.mark.parametrize(
    "row_cap",
    [
        True,
        0,
        -1,
        MAX_HISTORY_ROW_CAP + 1,
        cast(int, 1.5),
        cast(int, "20"),
    ],
)
def test_history_row_cap_rejects_invalid_values(row_cap: int) -> None:
    with pytest.raises(
        ValueError,
        match=f"between 1 and {MAX_HISTORY_ROW_CAP}",
    ):
        SqlAlchemyAgentRunJobStore(
            _session_factory(),
            history_row_cap=row_cap,
        )


@pytest.mark.parametrize(
    ("stored_role", "expected_role"),
    [
        ("user", PromptRole.USER),
        ("assistant", PromptRole.ASSISTANT),
    ],
)
def test_context_record_maps_to_a_structured_prompt_turn(
    stored_role: str,
    expected_role: PromptRole,
) -> None:
    turn = _prompt_turn_from_record(_record(role=stored_role))

    assert turn.role is expected_role
    assert turn.content == "content-7"
    assert turn.sequence == 7


def test_context_record_rejects_an_unsupported_role() -> None:
    with pytest.raises(
        RuntimeError,
        match="unsupported message role",
    ):
        _prompt_turn_from_record(_record(role="system"))


def test_context_query_requires_positive_policy_and_lifecycle_proofs() -> None:
    job = _job()
    input_created_at = datetime(2026, 11, 16, 20, 30, tzinfo=UTC)
    statement = _context_messages_statement(
        job=job,
        input_sequence=11,
        input_created_at=input_created_at,
        row_cap=7,
    )

    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "messages.tenant_id =" in sql
    assert "messages.conversation_id =" in sql
    assert "messages.sequence < 11" in sql
    assert "messages.role = 'user'" in sql
    assert sql.count("input_policy_assessments.action = 'allow'") == 2
    assert sql.count("input_policy_assessments.assessed_at <=") == 2
    assert sql.count("'2026-11-16 20:30:00+00:00'") == 2
    assert "chat.agent_runs.input_message_id = chat.messages.id" in sql
    assert "messages.role = 'assistant'" in sql
    assert "chat.agent_runs.output_message_id = chat.messages.id" in sql
    assert "chat.agent_runs.status = 'succeeded'" in sql
    assert "ORDER BY chat.messages.sequence DESC" in sql
    assert "LIMIT 7" in sql
