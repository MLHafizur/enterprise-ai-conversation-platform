import pytest

from enterprise_chat_platform.infrastructure.code_analysis.repository_url import (
    validate_git_ref,
    validate_repository_url,
)

ALLOWED_HOSTS = frozenset({"github.com"})


def test_repository_url_is_canonicalized() -> None:
    repository = validate_repository_url(
        "https://github.com/Acme/secure-app.git",
        allowed_hosts=ALLOWED_HOSTS,
    )

    assert repository.clone_url == "https://github.com/Acme/secure-app.git"
    assert repository.normalized_url == "https://github.com/Acme/secure-app"
    assert repository.provider == "github"
    assert repository.owner == "Acme"
    assert repository.repository == "secure-app"


@pytest.mark.parametrize(
    "raw_url",
    [
        "file:///etc/passwd",
        "ssh://git@github.com/acme/app.git",
        "https://user:token@github.com/acme/app.git",
        "https://localhost/acme/app.git",
        "https://127.0.0.1/acme/app.git",
        "https://github.com:8443/acme/app.git",
        "https://github.com/acme/app.git?token=secret",
        "https://github.com/acme/app/extra",
        "https://github.com/acme/%2e%2e",
    ],
)
def test_repository_url_rejects_unsafe_targets(raw_url: str) -> None:
    with pytest.raises(ValueError):
        validate_repository_url(raw_url, allowed_hosts=ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "ref",
    ["main", "feature/security-gate", "release/2026.11", "pull_request-42"],
)
def test_git_ref_accepts_conservative_names(ref: str) -> None:
    assert validate_git_ref(ref) == ref


@pytest.mark.parametrize(
    "ref",
    ["--upload-pack=evil", "../main", "refs//heads/main", "feature@{1}", "x.lock"],
)
def test_git_ref_rejects_ambiguous_or_option_like_values(ref: str) -> None:
    with pytest.raises(ValueError, match="invalid"):
        validate_git_ref(ref)
