from typing import cast

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)

from enterprise_chat_platform.domain.agent_run import (
    MAX_FAILURE_CODE_LENGTH,
    MAX_SAFE_FAILURE_MESSAGE_LENGTH,
    AgentRunStatus,
)
from enterprise_chat_platform.infrastructure.persistence.metadata import (
    APP_SCHEMA,
)
from enterprise_chat_platform.infrastructure.persistence.models import (
    AgentRunRecord,
    registered_model_metadata,
)


def _agent_run_table() -> Table:
    """Give mypy the concrete type created by declarative mapping."""
    return cast(Table, AgentRunRecord.__table__)


def test_agent_run_record_has_expected_table_shape() -> None:
    table = _agent_run_table()

    assert table.schema == APP_SCHEMA == "chat"
    assert table.name == "agent_runs"

    assert tuple(table.columns.keys()) == (
        "tenant_id",
        "id",
        "conversation_id",
        "input_message_id",
        "assistant_profile_id",
        "assistant_profile_version",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "output_message_id",
        "failure_code",
        "failure_safe_message",
    )

    assert tuple(column.name for column in table.primary_key.columns) == (
        "tenant_id",
        "id",
    )

    assert {column.name for column in table.columns if column.nullable} == {
        "started_at",
        "finished_at",
        "output_message_id",
        "failure_code",
        "failure_safe_message",
    }

    for column_name in (
        "tenant_id",
        "id",
        "conversation_id",
        "input_message_id",
        "assistant_profile_id",
        "output_message_id",
    ):
        assert isinstance(table.c[column_name].type, Uuid)

    status_type = table.c.status.type
    assert isinstance(status_type, String)
    assert status_type.length == 16
    assert isinstance(table.c.assistant_profile_version.type, BigInteger)

    failure_code_type = table.c.failure_code.type
    assert isinstance(failure_code_type, String)
    assert failure_code_type.length == MAX_FAILURE_CODE_LENGTH

    failure_message_type = table.c.failure_safe_message.type
    assert isinstance(failure_message_type, String)
    assert failure_message_type.length == MAX_SAFE_FAILURE_MESSAGE_LENGTH

    for column_name in (
        "created_at",
        "started_at",
        "finished_at",
    ):
        timestamp_type = table.c[column_name].type
        assert isinstance(timestamp_type, DateTime)
        assert timestamp_type.timezone is True


def test_agent_run_record_has_tenant_and_conversation_safe_relationships() -> None:
    table = _agent_run_table()

    foreign_keys = {
        str(constraint.name): (
            tuple(constraint.column_keys),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys == {
        "fk_agent_runs_assistant_profile": (
            (
                "tenant_id",
                "assistant_profile_id",
                "assistant_profile_version",
            ),
            (
                "chat.assistant_profile_versions.tenant_id",
                "chat.assistant_profile_versions.profile_id",
                "chat.assistant_profile_versions.version",
            ),
            "RESTRICT",
        ),
        "fk_agent_runs_conversation": (
            (
                "tenant_id",
                "conversation_id",
            ),
            (
                "chat.conversations.tenant_id",
                "chat.conversations.id",
            ),
            "RESTRICT",
        ),
        "fk_agent_runs_input_message": (
            (
                "tenant_id",
                "conversation_id",
                "input_message_id",
            ),
            (
                "chat.messages.tenant_id",
                "chat.messages.conversation_id",
                "chat.messages.id",
            ),
            "RESTRICT",
        ),
        "fk_agent_runs_output_message": (
            (
                "tenant_id",
                "conversation_id",
                "output_message_id",
            ),
            (
                "chat.messages.tenant_id",
                "chat.messages.conversation_id",
                "chat.messages.id",
            ),
            "RESTRICT",
        ),
    }


def test_agent_run_record_preserves_lifecycle_invariants() -> None:
    table = _agent_run_table()

    checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert set(checks) == {
        "ck_agent_runs_assistant_profile_version_positive",
        "ck_agent_runs_status_valid",
        "ck_agent_runs_timestamps_ordered",
        "ck_agent_runs_failure_code_valid",
        "ck_agent_runs_failure_safe_message_not_blank",
        "ck_agent_runs_failure_safe_message_printable",
        "ck_agent_runs_lifecycle_state_valid",
    }

    status_check = checks["ck_agent_runs_status_valid"]

    for status in AgentRunStatus:
        assert f"'{status.value}'" in status_check

    timestamp_check = checks["ck_agent_runs_timestamps_ordered"]
    assert "started_at >= created_at" in timestamp_check
    assert "finished_at >= created_at" in timestamp_check
    assert "finished_at >= started_at" in timestamp_check

    failure_code_check = checks["ck_agent_runs_failure_code_valid"]
    assert "^[a-z][a-z0-9_.-]*$" in failure_code_check

    lifecycle_check = checks["ck_agent_runs_lifecycle_state_valid"]

    for status in AgentRunStatus:
        assert f"status = '{status.value}'" in lifecycle_check


def test_agent_run_profile_pin_has_supporting_tenant_index() -> None:
    indexes = {
        str(index.name): tuple(column.name for column in index.columns)
        for index in _agent_run_table().indexes
    }

    assert indexes == {
        "ix_agent_runs_tenant_assistant_profile": (
            "tenant_id",
            "assistant_profile_id",
            "assistant_profile_version",
        )
    }


def test_model_registry_exposes_agent_runs_to_migrations() -> None:
    metadata = registered_model_metadata()

    assert "chat.agent_runs" in metadata.tables
    assert metadata.tables["chat.agent_runs"] is AgentRunRecord.__table__


def test_agent_run_supports_conversation_scoped_receipt_reference() -> None:
    table = _agent_run_table()

    unique_constraints = {
        str(constraint.name): tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints == {
        "uq_agent_runs_tenant_id_conversation_id_id": (
            "tenant_id",
            "conversation_id",
            "id",
        )
    }
