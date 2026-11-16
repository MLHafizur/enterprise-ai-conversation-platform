from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    AssistantProfileId,
    ConversationId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.prompting import (
    ENTERPRISE_CHAT_PROMPT_VERSION,
    PromptRole,
)
from enterprise_chat_platform.infrastructure.persistence.agent_run_job_store import (
    DEFAULT_HISTORY_ROW_CAP,
    MAX_HISTORY_ROW_CAP,
    SqlAlchemyAgentRunJobStore,
    _context_messages_statement,
    _pinned_assistant_profile_statement,
    _prompt_turn_from_record,
    _select_pinned_assistant_profile,
)
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.mappers.assistant_profile import (
    assistant_profile_to_record,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    MessageRecord,
)

CREATED_AT = datetime(2026, 11, 16, 19, 0, tzinfo=UTC)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class _JobScope:
    tenant_id: TenantId
    conversation_id: ConversationId
    agent_run_id: AgentRunId
    input_message_id: MessageId


def _job() -> _JobScope:
    return _JobScope(
        tenant_id=TenantId.new(),
        conversation_id=ConversationId.new(),
        agent_run_id=AgentRunId.new(),
        input_message_id=MessageId.new(),
    )


def _profile(*, tenant_id: TenantId) -> AssistantProfile:
    return AssistantProfile(
        tenant_id=tenant_id,
        ref=AssistantProfileRef(id=AssistantProfileId.new(), version=3),
        prompt_template_version=ENTERPRISE_CHAT_PROMPT_VERSION,
        assistant_name="Pinned Assistant",
        organization_name="Pinned Organization",
        response_tone=PromptResponseTone.PROFESSIONAL,
        expertise=PromptExpertiseLevel.SENIOR_ENGINEER,
        communication_style=PromptCommunicationStyle.DIRECT_HELPFUL,
        context_character_limit=4_096,
        created_at=CREATED_AT,
        created_by=UserId.new(),
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


def test_profile_query_uses_the_exact_tenant_scoped_run_pin() -> None:
    tenant_id = uuid4()
    profile_id = uuid4()
    statement = _pinned_assistant_profile_statement(
        tenant_id=tenant_id,
        profile_id=profile_id,
        profile_version=7,
    )

    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )

    assert f"assistant_profile_versions.tenant_id = '{tenant_id}'" in sql
    assert f"assistant_profile_versions.profile_id = '{profile_id}'" in sql
    assert "assistant_profile_versions.version = 7" in sql
    assert "tenant_assistant_profile_assignments" not in sql


@pytest.mark.asyncio
async def test_missing_pinned_profile_fails_closed() -> None:
    session = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    run_record = MagicMock(spec=AgentRunRecord)
    run_record.tenant_id = uuid4()
    run_record.assistant_profile_id = uuid4()
    run_record.assistant_profile_version = 2

    with pytest.raises(RuntimeError, match="missing or mismatched assistant profile"):
        await _select_pinned_assistant_profile(
            session=cast(AsyncSession, session),
            run_record=run_record,
        )


@pytest.mark.asyncio
async def test_cross_tenant_pinned_profile_fails_closed() -> None:
    run_tenant_id = uuid4()
    other_profile = _profile(tenant_id=TenantId.new())
    result = MagicMock()
    result.scalar_one_or_none.return_value = assistant_profile_to_record(other_profile)
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=result)
    run_record = MagicMock(spec=AgentRunRecord)
    run_record.tenant_id = run_tenant_id
    run_record.assistant_profile_id = other_profile.ref.id.value
    run_record.assistant_profile_version = other_profile.ref.version

    with pytest.raises(RuntimeError, match="cross-tenant assistant profile"):
        await _select_pinned_assistant_profile(
            session=cast(AsyncSession, session),
            run_record=run_record,
        )
