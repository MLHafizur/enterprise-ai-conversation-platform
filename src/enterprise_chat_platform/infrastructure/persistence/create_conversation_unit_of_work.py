"""SQLAlchemy transaction boundary for conversation provisioning."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from enterprise_chat_platform.domain.chat import Conversation
from enterprise_chat_platform.domain.identifiers import ConversationId, TenantId
from enterprise_chat_platform.infrastructure.persistence.database import (
    AsyncSessionFactory,
)
from enterprise_chat_platform.infrastructure.persistence.repositories.conversation import (
    SqlAlchemyConversationRepository,
)


class SqlAlchemyCreateConversationUnitOfWork:
    """Own one transaction for one idempotent provisioning attempt."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self._repository: SqlAlchemyConversationRepository | None = None
        self._entered = False
        self._insert_attempted = False
        self._inserted = False
        self._committed = False

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("Unit of work instances cannot be entered more than once")

        self._entered = True
        session = self._session_factory()
        transaction = session.begin()

        try:
            await transaction.start()
            repository = SqlAlchemyConversationRepository(session)
        except BaseException:
            try:
                if transaction.is_active:
                    await transaction.rollback()
            finally:
                await session.close()
            raise

        self._session = session
        self._transaction = transaction
        self._repository = repository
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

    async def insert_if_absent(
        self,
        *,
        conversation: Conversation,
    ) -> bool:
        """Attempt exactly one conflict-safe insert in the owned transaction."""
        self._require_active_transaction()

        if self._insert_attempted:
            raise RuntimeError("Only one conversation can be provisioned per unit of work")

        repository = self._require_repository()
        inserted = await repository.insert_if_absent(
            conversation=conversation,
        )
        self._insert_attempted = True
        self._inserted = inserted
        return inserted

    async def get_conversation(
        self,
        *,
        tenant_id: TenantId,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        """Load the durable tenant-scoped row after an insert conflict."""
        self._require_active_transaction()
        repository = self._require_repository()
        return await repository.get(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )

    async def commit(self) -> None:
        """Commit only a successful insert and never commit a replay lookup."""
        _, transaction = self._require_active_transaction()

        if not self._insert_attempted or not self._inserted:
            raise RuntimeError("A newly inserted conversation is required before commit")

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

    def _require_repository(self) -> SqlAlchemyConversationRepository:
        if self._repository is None:
            raise RuntimeError("Repository is unavailable outside the unit-of-work context")
        return self._repository

    def _clear_active_resources(self) -> None:
        self._session = None
        self._transaction = None
        self._repository = None
