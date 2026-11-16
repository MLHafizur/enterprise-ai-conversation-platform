from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from enterprise_chat_platform.application.tenant_prompting import (
    TenantPromptBuilder,
    build_prompt_plan,
)
from enterprise_chat_platform.domain.assistant_profile import (
    AssistantProfile,
    AssistantProfileRef,
    PromptCommunicationStyle,
    PromptExpertiseLevel,
    PromptResponseTone,
)
from enterprise_chat_platform.domain.identifiers import (
    AssistantProfileId,
    TenantId,
    UserId,
)
from enterprise_chat_platform.domain.prompting import (
    ENTERPRISE_CHAT_PROMPT_VERSION,
    PromptRole,
    PromptTurn,
)

TENANT_ID = TenantId.parse("11111111-1111-4111-8111-111111111111")


def _profile(**changes: Any) -> AssistantProfile:
    values: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "ref": AssistantProfileRef(
            id=AssistantProfileId.parse("22222222-2222-4222-8222-222222222222"),
            version=3,
        ),
        "prompt_template_version": ENTERPRISE_CHAT_PROMPT_VERSION,
        "assistant_name": "Acme Assistant",
        "organization_name": "Acme Corp",
        "response_tone": PromptResponseTone.PROFESSIONAL,
        "expertise": PromptExpertiseLevel.SENIOR_ENGINEER,
        "communication_style": PromptCommunicationStyle.DIRECT_HELPFUL,
        "context_character_limit": 100,
        "created_at": datetime(2026, 11, 16, 15, 0, tzinfo=UTC),
        "created_by": UserId.parse("33333333-3333-4333-8333-333333333333"),
    }
    values.update(changes)
    return AssistantProfile(**values)


def _turn(sequence: int, content: str, role: PromptRole = PromptRole.USER) -> PromptTurn:
    return PromptTurn(role=role, content=content, sequence=sequence)


def test_builder_is_deterministic_and_keeps_user_text_out_of_instruction() -> None:
    context = (
        _turn(1, "private user instructions"),
        _turn(2, "prior assistant reply", PromptRole.ASSISTANT),
    )
    builder = TenantPromptBuilder()
    profile = _profile()

    first = builder.build(profile=profile, context_messages=context)
    second = builder.build(profile=profile, context_messages=context)

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.version == profile.prompt_template_version
    assert "Acme Assistant" in first.system_instruction
    assert "senior engineer" in first.system_instruction
    assert "direct helpful" in first.system_instruction
    assert "private user instructions" not in first.system_instruction
    assert first.context_messages == context


def test_context_budget_counts_unicode_code_points_and_drops_oldest_whole_messages() -> None:
    profile = _profile(context_character_limit=4)
    context = (
        _turn(1, "old"),
        _turn(2, "🙂🙂🙂"),
    )

    plan = build_prompt_plan(profile=profile, context_messages=context)

    assert plan.context_messages == (context[1],)
    assert plan.selected_message_count == 1
    assert plan.omitted_message_count == 1
    assert plan.context_character_count == 3
    assert context[0].content == "old"


def test_context_budget_drops_a_leading_orphan_assistant_turn() -> None:
    profile = _profile(context_character_limit=8)
    context = (
        _turn(1, "ask?"),
        _turn(2, "reply", PromptRole.ASSISTANT),
        _turn(3, "new"),
    )

    plan = build_prompt_plan(profile=profile, context_messages=context)

    assert plan.context_messages == (context[2],)
    assert plan.selected_message_count == 1
    assert plan.omitted_message_count == 2
    assert plan.context_character_count == len(context[2].content)


def test_context_drops_all_leading_assistant_turns_without_budget_eviction() -> None:
    context = (
        _turn(1, "orphan one", PromptRole.ASSISTANT),
        _turn(2, "orphan two", PromptRole.ASSISTANT),
        _turn(3, "complete question"),
    )

    plan = build_prompt_plan(profile=_profile(), context_messages=context)

    assert plan.context_messages == (context[2],)
    assert plan.omitted_message_count == 2
    assert plan.context_character_count == len(context[2].content)


def test_typed_profile_traits_are_rendered_by_v1_renderer() -> None:
    profile = replace(
        _profile(),
        response_tone=PromptResponseTone.FRIENDLY,
        expertise=PromptExpertiseLevel.PRODUCT_SPECIALIST,
        communication_style=PromptCommunicationStyle.CONCISE_SUPPORTIVE,
    )

    plan = build_prompt_plan(profile=profile, context_messages=())

    assert "friendly tone" in plan.system_instruction
    assert "product specialist" in plan.system_instruction
    assert "concise supportive" in plan.system_instruction


def test_unsupported_template_version_fails_closed() -> None:
    profile = replace(
        _profile(),
        prompt_template_version="enterprise-chat.v2",
    )

    with pytest.raises(ValueError, match="Unsupported assistant profile prompt template"):
        build_prompt_plan(profile=profile, context_messages=())
