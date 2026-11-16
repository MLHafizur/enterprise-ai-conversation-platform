"""SQLAlchemy models for immutable assistant-profile configuration."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_chat_platform.domain.assistant_profile import (
    MAX_CONTEXT_CHARACTER_LIMIT,
    MAX_PROFILE_TEXT_LENGTH,
    MAX_PROMPT_TEMPLATE_VERSION_LENGTH,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
    Base,
)


class AssistantProfileVersionRecord(Base):
    """One immutable, tenant-scoped assistant-profile revision."""

    __tablename__ = "assistant_profile_versions"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prompt_template_version: Mapped[str] = mapped_column(
        String(MAX_PROMPT_TEMPLATE_VERSION_LENGTH, collation="C"),
        nullable=False,
    )
    assistant_name: Mapped[str] = mapped_column(
        String(MAX_PROFILE_TEXT_LENGTH),
        nullable=False,
    )
    organization_name: Mapped[str] = mapped_column(
        String(MAX_PROFILE_TEXT_LENGTH),
        nullable=False,
    )
    response_tone: Mapped[str] = mapped_column(
        String(16, collation="C"),
        nullable=False,
    )
    expertise: Mapped[str] = mapped_column(
        String(32, collation="C"),
        nullable=False,
    )
    communication_style: Mapped[str] = mapped_column(
        String(32, collation="C"),
        nullable=False,
    )
    context_character_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "prompt_template_version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'",
            name="prompt_template_version_valid",
        ),
        CheckConstraint(
            "assistant_name ~ '^[A-Za-z0-9][A-Za-z0-9 .&()''_-]{0,127}$'",
            name="assistant_name_valid",
        ),
        CheckConstraint(
            "organization_name ~ '^[A-Za-z0-9][A-Za-z0-9 .&()''_-]{0,127}$'",
            name="organization_name_valid",
        ),
        CheckConstraint(
            "response_tone IN ("
            f"'{PromptResponseTone.PROFESSIONAL.value}', "
            f"'{PromptResponseTone.FRIENDLY.value}'"
            ")",
            name="response_tone_valid",
        ),
        CheckConstraint(
            "expertise IN ("
            f"'{PromptExpertiseLevel.SENIOR_ENGINEER.value}', "
            f"'{PromptExpertiseLevel.PRODUCT_SPECIALIST.value}'"
            ")",
            name="expertise_valid",
        ),
        CheckConstraint(
            "communication_style IN ("
            f"'{PromptCommunicationStyle.DIRECT_HELPFUL.value}', "
            f"'{PromptCommunicationStyle.CONCISE_SUPPORTIVE.value}'"
            ")",
            name="communication_style_valid",
        ),
        CheckConstraint(
            f"context_character_limit BETWEEN 1 AND {MAX_CONTEXT_CHARACTER_LIMIT}",
            name="context_character_limit_valid",
        ),
    )


class TenantAssistantProfileAssignmentRecord(Base):
    """Mutable tenant pointer to one exact immutable profile revision."""

    __tablename__ = "tenant_assistant_profile_assignments"

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    profile_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "profile_id", "profile_version"],
            [
                f"{APP_SCHEMA}.assistant_profile_versions.tenant_id",
                f"{APP_SCHEMA}.assistant_profile_versions.profile_id",
                f"{APP_SCHEMA}.assistant_profile_versions.version",
            ],
            name="fk_tenant_assistant_profile_assignments_active_profile",
            ondelete="RESTRICT",
        ),
        CheckConstraint("profile_version > 0", name="profile_version_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )
