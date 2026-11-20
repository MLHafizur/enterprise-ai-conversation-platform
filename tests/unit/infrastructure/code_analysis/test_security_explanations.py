from unittest.mock import AsyncMock

import pytest

from enterprise_chat_platform.application.code_security_ai import (
    LocalSecurityExplanationProvider,
    SecurityFindingContext,
)
from enterprise_chat_platform.infrastructure.code_analysis.gemini_explanations import (
    GeminiSecurityExplanationProvider,
    SecurityAIUnavailableError,
)


def _finding() -> SecurityFindingContext:
    return SecurityFindingContext(
        rule_id="command_injection",
        severity="high",
        message="Potential command injection vulnerability",
        file_path="app.py",
        line_number=8,
        code_snippet='os.system("ls " + <redacted>)',
        recommendation="Use subprocess with shell=False and validate input",
        cwe_id="CWE-78",
        owasp_category="A03:2021-Injection",
    )


@pytest.mark.asyncio
async def test_local_provider_returns_deterministic_guidance() -> None:
    provider = LocalSecurityExplanationProvider()

    explanation = await provider.explain(finding=_finding())

    assert "CWE-78" in explanation.meaning
    assert explanation.remediation_steps[0].startswith("Use subprocess")


@pytest.mark.asyncio
async def test_gemini_provider_sends_redacted_structured_evidence() -> None:
    response = AsyncMock()
    response.status_code = 200
    response.json = lambda: {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"meaning":"Risk","exploitation":"Attack",'
                                '"remediation_steps":["Fix"],'
                                '"secure_example":"Safe"}'
                            )
                        }
                    ]
                }
            }
        ]
    }
    client = AsyncMock()
    client.post.return_value = response
    provider = GeminiSecurityExplanationProvider(
        client=client,
        api_key="secret-key",
        model="gemini-2.5-flash",
        timeout_seconds=10,
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )

    explanation = await provider.explain(finding=_finding())

    assert explanation.meaning == "Risk"
    request_payload = client.post.await_args.kwargs["json"]
    serialized = str(request_payload)
    assert "<redacted>" in serialized
    assert "secret-key" not in serialized
    assert request_payload["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_gemini_provider_fails_with_safe_error() -> None:
    client = AsyncMock()
    client.post.side_effect = RuntimeError("request contained sensitive headers")
    provider = GeminiSecurityExplanationProvider(
        client=client,
        api_key="secret-key",
        model="gemini-2.5-flash",
        timeout_seconds=10,
        api_base_url="https://generativelanguage.googleapis.com/v1beta",
    )

    with pytest.raises(SecurityAIUnavailableError, match="unavailable"):
        await provider.explain(finding=_finding())
