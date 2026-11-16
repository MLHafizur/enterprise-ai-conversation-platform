import pytest

from enterprise_chat_platform.domain.errors import DomainValidationError
from enterprise_chat_platform.domain.prompting import (
    ENTERPRISE_CHAT_PROMPT_VERSION,
    PromptPlan,
    PromptRole,
    PromptTurn,
)


def _turn(
    sequence: int = 1,
    *,
    content: str = "Explain durable queues.",
    role: PromptRole = PromptRole.USER,
) -> PromptTurn:
    return PromptTurn(
        role=role,
        content=content,
        sequence=sequence,
    )


def test_prompt_values_hide_sensitive_content_from_repr() -> None:
    turn = _turn(content="private-user-message")
    plan = PromptPlan(
        system_instruction="private-system-instruction",
        context_messages=(turn,),
        selected_message_count=1,
        context_character_count=len(turn.content),
    )

    rendered = f"{turn!r}\n{plan!r}"

    assert "private-user-message" not in rendered
    assert "private-system-instruction" not in rendered
    assert plan.version == ENTERPRISE_CHAT_PROMPT_VERSION


def test_prompt_plan_requires_ordered_complete_context_and_exact_metadata() -> None:
    first = _turn(1)
    second = _turn(2, role=PromptRole.ASSISTANT, content="Use an outbox table.")

    plan = PromptPlan(
        system_instruction="Follow the trusted policy.",
        context_messages=(first, second),
        selected_message_count=2,
        omitted_message_count=3,
        context_character_count=len(first.content) + len(second.content),
    )

    assert plan.context_messages == (first, second)
    assert plan.selected_message_count == 2
    assert plan.omitted_message_count == 3

    late_user = _turn(2)
    early_assistant = _turn(
        1,
        role=PromptRole.ASSISTANT,
        content="Earlier answer.",
    )
    with pytest.raises(DomainValidationError, match="strictly increasing"):
        PromptPlan(
            system_instruction="Follow the trusted policy.",
            context_messages=(late_user, early_assistant),
            selected_message_count=2,
            context_character_count=len(late_user.content) + len(early_assistant.content),
        )

    with pytest.raises(DomainValidationError, match="Selected message count"):
        PromptPlan(
            system_instruction="Follow the trusted policy.",
            context_messages=(first,),
            selected_message_count=0,
            context_character_count=len(first.content),
        )

    with pytest.raises(DomainValidationError, match="character count"):
        PromptPlan(
            system_instruction="Follow the trusted policy.",
            context_messages=(first,),
            selected_message_count=1,
            context_character_count=1,
        )

    assistant = _turn(
        1,
        role=PromptRole.ASSISTANT,
        content="An orphaned answer.",
    )
    with pytest.raises(DomainValidationError, match="begin with a user"):
        PromptPlan(
            system_instruction="Follow the trusted policy.",
            context_messages=(assistant,),
            selected_message_count=1,
            context_character_count=len(assistant.content),
        )


def test_prompt_turn_rejects_invalid_sequence_and_content() -> None:
    with pytest.raises(DomainValidationError, match="positive"):
        _turn(0)
    with pytest.raises(DomainValidationError, match="blank"):
        _turn(content="   ")
    with pytest.raises(DomainValidationError, match="NUL"):
        _turn(content="invalid\x00message")


def test_fingerprint_is_deterministic_and_content_sensitive() -> None:
    first = _turn(content="🙂")
    first_plan = PromptPlan(
        system_instruction="Follow policy.",
        context_messages=(first,),
        selected_message_count=1,
        context_character_count=1,
    )
    identical_plan = PromptPlan(
        system_instruction="Follow policy.",
        context_messages=(first,),
        selected_message_count=1,
        context_character_count=1,
    )
    changed = _turn(content="🙃")
    changed_plan = PromptPlan(
        system_instruction="Follow policy.",
        context_messages=(changed,),
        selected_message_count=1,
        context_character_count=1,
    )

    assert first_plan.fingerprint == identical_plan.fingerprint
    assert first_plan.fingerprint != changed_plan.fingerprint
    assert len(first_plan.fingerprint) == 64
