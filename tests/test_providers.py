import pytest

from backend.providers import (
    ProviderIntegrationError,
    infer_provider_repo_from_remote,
    parse_provider_repo,
)


def test_parse_provider_repo_valid():
    repo = parse_provider_repo("github", "openai/forge")
    assert repo.provider == "github"
    assert repo.owner == "openai"
    assert repo.name == "forge"
    assert repo.slug == "openai/forge"


def test_parse_provider_repo_rejects_invalid_slug():
    with pytest.raises(ProviderIntegrationError):
        parse_provider_repo("github", "missing-slash")


def test_infer_provider_repo_from_ssh_remote():
    repo = infer_provider_repo_from_remote("gitlab", "git@gitlab.com:my-org/my-repo.git")
    assert repo.provider == "gitlab"
    assert repo.slug == "my-org/my-repo"
