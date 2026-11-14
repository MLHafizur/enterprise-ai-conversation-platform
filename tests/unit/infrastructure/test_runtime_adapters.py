from datetime import UTC, datetime

import pytest

from enterprise_chat_platform.application.accept_user_turn import (
    ActorContext,
    Clock,
    ConversationWriteAuthorizer,
    TurnIds,
)
from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import (
    AgentRunId,
    ConversationId,
    EventId,
    MessageId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.infrastructure.runtime_adapters import (
    OwnerOnlyConversationWriteAuthorizer,
    UtcSystemClock,
    UuidTurnIds,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = TenantId.parse("22222222-2222-4222-8222-222222222222")
OWNER_ID = UserId.parse("33333333-3333-4333-8333-333333333333")
OTHER_USER_ID = UserId.parse("44444444-4444-4444-8444-444444444444")
CONVERSATION_ID = ConversationId.parse("55555555-5555-4555-8555-555555555555")
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _conversation() -> Conversation:
    return Conversation.create(
        id=CONVERSATION_ID,
        tenant_id=TENANT_ID,
        created_by=OWNER_ID,
        at=CREATED_AT,
    )


def test_utc_system_clock_returns_current_aware_utc_time() -> None:
    clock: Clock = UtcSystemClock()
    before = datetime.now(UTC)

    observed = clock.now()

    after = datetime.now(UTC)
    assert observed.tzinfo is UTC
    assert before <= observed <= after


def test_uuid_turn_ids_return_fresh_strongly_typed_uuid4_values() -> None:
    ids: TurnIds = UuidTurnIds()

    generated = (
        ids.new_message_id(),
        ids.new_message_id(),
        ids.new_agent_run_id(),
        ids.new_event_id(),
    )

    assert isinstance(generated[0], MessageId)
    assert isinstance(generated[1], MessageId)
    assert isinstance(generated[2], AgentRunId)
    assert isinstance(generated[3], EventId)
    assert all(identifier.value.version == 4 for identifier in generated)
    assert len({identifier.value for identifier in generated}) == len(generated)


@pytest.mark.asyncio
async def test_owner_can_write_own_conversation() -> None:
    authorizer: ConversationWriteAuthorizer = OwnerOnlyConversationWriteAuthorizer()

    allowed = await authorizer.can_write(
        actor=ActorContext(
            tenant_id=TENANT_ID,
            actor_id=OWNER_ID,
        ),
        conversation=_conversation(),
    )

    assert allowed is True


@pytest.mark.parametrize(
    ("tenant_id", "actor_id"),
    [
        (TENANT_ID, OTHER_USER_ID),
        (OTHER_TENANT_ID, OWNER_ID),
    ],
)
@pytest.mark.asyncio
async def test_non_owner_or_cross_tenant_actor_cannot_write(
    tenant_id: TenantId,
    actor_id: UserId,
) -> None:
    authorizer: ConversationWriteAuthorizer = OwnerOnlyConversationWriteAuthorizer()

    allowed = await authorizer.can_write(
        actor=ActorContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
        ),
        conversation=_conversation(),
    )

    assert allowed is False
