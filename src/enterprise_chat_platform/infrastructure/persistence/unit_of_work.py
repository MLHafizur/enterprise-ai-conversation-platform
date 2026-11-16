"""SQLAlchemy transaction boundary for accepting one user turn."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from enterprise_chat_platform.application.accept_user_turn import (
    AgentRunQueuedEvent,
    IdempotencyReceipt,
    IdempotencyScope,
)
from enterprise_chat_platform.application.errors import ConversationConcurrencyError
from enterprise_chat_platform.domain.agent_run import AgentRunStatus
from enterprise_chat_platform.domain.assistant_profile import AssistantProfile
from enterprise_chat_platform.domain.chat import AcceptedUserTurn, Conversation
from enterprise_chat_platform.domain.identifiers import ConversationId, TenantId
from enterprise_chat_platform.domain.primitives import normalize_utc
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.agent_run import (
    SqlAlchemyAgentRunRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.assistant_profile import (
    SqlAlchemyAssistantProfileRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.idempotency_receipt import (
    SqlAlchemyIdempotencyReceiptRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.message import (
    SqlAlchemyMessageRepository,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.outbox_event import (
    SqlAlchemyOutboxEventRepository,
)


@dataclass(frozen=True, slots=True)
class _PendingAcceptedTurn:
    expected_version: int
    accepted_turn: AcceptedUserTurn
    receipt: IdempotencyReceipt
    event: AgentRunQueuedEvent


class SqlAlchemyAcceptUserTurnUnitOfWork:
    """Own one session and transaction for one accept-user-turn attempt."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self._conversation_repository: SqlAlchemyConversationRepository | None = None
        self._receipt_repository: SqlAlchemyIdempotencyReceiptRepository | None = None
        self._message_repository: SqlAlchemyMessageRepository | None = None
        self._agent_run_repository: SqlAlchemyAgentRunRepository | None = None
        self._assistant_profile_repository: SqlAlchemyAssistantProfileRepository | None = None
        self._outbox_repository: SqlAlchemyOutboxEventRepository | None = None
        self._pending_turn: _PendingAcceptedTurn | None = None
        self._entered = False
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("Unit of work instances cannot be entered more than once")

        self._entered = True
        session = self._session_factory()
        transaction = session.begin()

        try:
            await transaction.start()
            conversation_repository = SqlAlchemyConversationRepository(session)
            receipt_repository = SqlAlchemyIdempotencyReceiptRepository(session)
            message_repository = SqlAlchemyMessageRepository(session)
            agent_run_repository = SqlAlchemyAgentRunRepository(session)
            assistant_profile_repository = SqlAlchemyAssistantProfileRepository(session)
            outbox_repository = SqlAlchemyOutboxEventRepository(session)
        except BaseException:
            try:
                if transaction.is_active:
                    await transaction.rollback()
            finally:
                await session.close()
            raise

        self._session = session
        self._transaction = transaction
        self._conversation_repository = conversation_repository
        self._receipt_repository = receipt_repository
        self._message_repository = message_repository
        self._agent_run_repository = agent_run_repository
        self._assistant_profile_repository = assistant_profile_repository
        self._outbox_repository = outbox_repository

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback

        session = self._session
        transaction = self._transaction

        if session is None or transaction is None:
            return

        try:
            if not self._committed and transaction.is_active:
                await transaction.rollback()
        finally:
            try:
                await session.close()
            finally:
                self._clear_active_resources()

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        """Load one conversation inside the owned transaction."""
        self._require_active_transaction()
        repository = self._require_repository(self._conversation_repository)
        return await repository.get(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )

    async def get_idempotency_receipt(
        self,
        *,
        scope: IdempotencyScope,
    ) -> IdempotencyReceipt | None:
        """Serialize one idempotency scope before reading its receipt."""
        self._require_active_transaction()
        repository = self._require_repository(self._receipt_repository)
        await repository.acquire_scope_lock(scope=scope)
        return await repository.get(scope=scope)

    async def get_or_create_active_assistant_profile(
        self,
        *,
        tenant_id: TenantId,
        default_profile: AssistantProfile,
    ) -> AssistantProfile:
        """Lock and return the exact profile revision this turn will pin."""
        self._require_active_transaction()
        repository = self._require_repository(self._assistant_profile_repository)
        return await repository.get_or_create_active(
            tenant_id=tenant_id,
            default_profile=default_profile,
        )

    def stage_accepted_turn(
        self,
        *,
        expected_conversation_version: int,
        accepted_turn: AcceptedUserTurn,
        receipt: IdempotencyReceipt,
        event: AgentRunQueuedEvent,
    ) -> None:
        """Validate and stage every append-only record for one accepted turn."""
        self._require_active_transaction()

        if self._pending_turn is not None:
            raise RuntimeError("Only one accepted turn can be staged per unit of work")

        _validate_cross_object_coherence(
            expected_conversation_version=expected_conversation_version,
            accepted_turn=accepted_turn,
            receipt=receipt,
            event=event,
        )

        self._pending_turn = _PendingAcceptedTurn(
            expected_version=expected_conversation_version,
            accepted_turn=accepted_turn,
            receipt=receipt,
            event=event,
        )

    async def commit(self) -> None:
        """CAS the conversation, flush all staged writes, and commit once."""
        session, transaction = self._require_active_transaction()

        pending = self._pending_turn
        if pending is None:
            raise RuntimeError("An accepted turn must be staged before commit")

        conversation_repository = self._require_repository(self._conversation_repository)
        updated = await conversation_repository.update_if_version_matches(
            expected_version=pending.expected_version,
            updated_conversation=pending.accepted_turn.updated_conversation,
        )

        if not updated:
            raise ConversationConcurrencyError(
                "Conversation changed while the user turn was being accepted"
            )

        # SQLAlchemy has no ORM relationships between these append-only records,
        # so add and flush each dependency layer explicitly in one transaction.
        message_repository = self._require_repository(self._message_repository)
        message_repository.add(message=pending.accepted_turn.message)
        await session.flush()

        agent_run_repository = self._require_repository(self._agent_run_repository)
        agent_run_repository.add(agent_run=pending.accepted_turn.agent_run)
        await session.flush()

        receipt_repository = self._require_repository(self._receipt_repository)
        outbox_repository = self._require_repository(self._outbox_repository)
        receipt_repository.add(receipt=pending.receipt)
        outbox_repository.add(event=pending.event)
        await session.flush()

        await transaction.commit()
        self._committed = True

    def _require_active_transaction(
        self,
    ) -> tuple[AsyncSession, AsyncSessionTransaction]:
        session = self._session
        transaction = self._transaction

        if session is None or transaction is None or self._committed or not transaction.is_active:
            raise RuntimeError("Unit of work requires an active transaction")

        return session, transaction

    @staticmethod
    def _require_repository[RepositoryT](
        repository: RepositoryT | None,
    ) -> RepositoryT:
        if repository is None:
            raise RuntimeError("Unit of work repositories are unavailable outside its context")
        return repository

    def _clear_active_resources(self) -> None:
        self._session = None
        self._transaction = None
        self._conversation_repository = None
        self._receipt_repository = None
        self._message_repository = None
        self._agent_run_repository = None
        self._assistant_profile_repository = None
        self._outbox_repository = None
        self._pending_turn = None


def _validate_cross_object_coherence(
    *,
    expected_conversation_version: int,
    accepted_turn: AcceptedUserTurn,
    receipt: IdempotencyReceipt,
    event: AgentRunQueuedEvent,
) -> None:
    conversation = accepted_turn.updated_conversation
    message = accepted_turn.message
    agent_run = accepted_turn.agent_run

    if expected_conversation_version < 0:
        raise ValueError("Expected conversation version cannot be negative")
    if conversation.version != expected_conversation_version + 1:
        raise ValueError("Accepted conversation version must advance exactly once")
    if agent_run.status is not AgentRunStatus.QUEUED:
        raise ValueError("An accepted user turn must contain a queued agent run")
    if conversation.updated_at != message.created_at or agent_run.created_at != message.created_at:
        raise ValueError("Accepted-turn entities must use the same acceptance timestamp")

    scope = receipt.scope
    result = receipt.result

    if scope.tenant_id != conversation.tenant_id:
        raise ValueError("Receipt tenant must match the accepted conversation")
    if scope.conversation_id != conversation.id:
        raise ValueError("Receipt scope must match the accepted conversation")
    if scope.key != message.idempotency_key:
        raise ValueError("Receipt key must match the accepted message")
    if result.replayed:
        raise ValueError("A newly accepted turn cannot stage a replayed receipt")
    if result.conversation_id != conversation.id:
        raise ValueError("Receipt result must match the accepted conversation")
    if result.conversation_version != conversation.version:
        raise ValueError("Receipt result must match the accepted conversation version")
    if result.message_id != message.id or result.message_sequence != message.sequence:
        raise ValueError("Receipt result must match the accepted message")
    if result.agent_run_id != agent_run.id:
        raise ValueError("Receipt result must match the accepted agent run")

    result_accepted_at = normalize_utc(
        result.accepted_at,
        field_name="idempotency_receipt.accepted_at",
    )
    if result_accepted_at != message.created_at:
        raise ValueError("Receipt result must use the accepted message timestamp")

    if event.tenant_id != conversation.tenant_id:
        raise ValueError("Outbox event tenant must match the accepted conversation")
    if event.conversation_id != conversation.id:
        raise ValueError("Outbox event must match the accepted conversation")
    if event.agent_run_id != agent_run.id:
        raise ValueError("Outbox event must match the accepted agent run")
    if event.input_message_id != message.id:
        raise ValueError("Outbox event must match the accepted message")
    if event.conversation_version != conversation.version:
        raise ValueError("Outbox event must match the accepted conversation version")

    event_occurred_at = normalize_utc(
        event.occurred_at,
        field_name="agent_run_queued_event.occurred_at",
    )
    if event_occurred_at != message.created_at:
        raise ValueError("Outbox event must use the accepted message timestamp")
