import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx


class ProviderIntegrationError(RuntimeError):
    """Raised when provider integration is misconfigured or API calls fail."""


@dataclass
class ProviderRepo:
    provider: str  # github | gitlab
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_provider_repo(provider_type: str, repo: str) -> ProviderRepo:
    raw = (repo or "").strip().strip("/")
    if not raw:
        raise ProviderIntegrationError("Provider repo is required (expected owner/repo).")
    if "/" not in raw:
        raise ProviderIntegrationError("Provider repo must be formatted as owner/repo.")
    owner, name = raw.split("/", 1)
    owner = owner.strip()
    name = name.strip()
    if not owner or not name:
        raise ProviderIntegrationError("Provider repo must be formatted as owner/repo.")
    provider = (provider_type or "").strip().lower()
    if provider not in ("github", "gitlab"):
        raise ProviderIntegrationError("provider_type must be either 'github' or 'gitlab'.")
    return ProviderRepo(provider=provider, owner=owner, name=name)


def infer_provider_repo_from_remote(provider_type: str, remote_url: str) -> ProviderRepo:
    cleaned = (remote_url or "").strip()
    cleaned = re.sub(r"\.git$", "", cleaned)
    match = re.search(r"[:/]([^/:]+)/([^/]+)$", cleaned)
    if not match:
        raise ProviderIntegrationError("Could not infer owner/repo from git remote URL.")
    owner, name = match.group(1), match.group(2)
    return parse_provider_repo(provider_type, f"{owner}/{name}")


async def create_change_request(
    *,
    provider_type: str,
    api_base_url: Optional[str],
    token: str,
    repo_slug: str,
    head_branch: str,
    base_branch: str,
    title: str,
    description: str,
    labels: list[str],
) -> dict:
    repo = parse_provider_repo(provider_type, repo_slug)
    if not token:
        raise ProviderIntegrationError("Provider token is required to create a PR/MR.")

    provider = repo.provider
    if provider == "github":
        base_url = (api_base_url or "https://api.github.com").rstrip("/")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/repos/{repo.slug}/pulls",
                headers=headers,
                json={
                    "title": title,
                    "head": head_branch,
                    "base": base_branch,
                    "body": description,
                },
            )
            if response.status_code >= 400:
                raise ProviderIntegrationError(f"GitHub PR creation failed: {response.text[:300]}")
            payload = response.json()
            if labels:
                await client.post(
                    f"{base_url}/repos/{repo.slug}/issues/{payload['number']}/labels",
                    headers=headers,
                    json={"labels": labels},
                )
        return {
            "provider": "github",
            "id": str(payload["id"]),
            "number": int(payload["number"]),
            "url": payload["html_url"],
            "state": payload.get("state", "open"),
        }

    base_url = (api_base_url or "https://gitlab.com/api/v4").rstrip("/")
    headers = {"PRIVATE-TOKEN": token}
    project = quote(repo.slug, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/projects/{project}/merge_requests",
            headers=headers,
            json={
                "title": title,
                "source_branch": head_branch,
                "target_branch": base_branch,
                "description": description,
                "labels": ",".join(labels) if labels else None,
            },
        )
        if response.status_code >= 400:
            raise ProviderIntegrationError(f"GitLab MR creation failed: {response.text[:300]}")
        payload = response.json()
    return {
        "provider": "gitlab",
        "id": str(payload["id"]),
        "number": int(payload["iid"]),
        "url": payload["web_url"],
        "state": payload.get("state", "opened"),
    }


async def get_change_request_status(
    *,
    provider_type: str,
    api_base_url: Optional[str],
    token: str,
    repo_slug: str,
    number: int,
) -> dict:
    repo = parse_provider_repo(provider_type, repo_slug)
    if not token:
        raise ProviderIntegrationError("Provider token is required to fetch PR/MR status.")

    if repo.provider == "github":
        base_url = (api_base_url or "https://api.github.com").rstrip("/")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url}/repos/{repo.slug}/pulls/{number}",
                headers=headers,
            )
        if response.status_code >= 400:
            raise ProviderIntegrationError(f"GitHub PR status fetch failed: {response.text[:300]}")
        payload = response.json()
        return {
            "provider": "github",
            "number": payload["number"],
            "url": payload["html_url"],
            "state": payload.get("state"),
            "merged": bool(payload.get("merged")),
            "draft": bool(payload.get("draft")),
        }

    base_url = (api_base_url or "https://gitlab.com/api/v4").rstrip("/")
    headers = {"PRIVATE-TOKEN": token}
    project = quote(repo.slug, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{base_url}/projects/{project}/merge_requests/{number}",
            headers=headers,
        )
    if response.status_code >= 400:
        raise ProviderIntegrationError(f"GitLab MR status fetch failed: {response.text[:300]}")
    payload = response.json()
    return {
        "provider": "gitlab",
        "number": payload["iid"],
        "url": payload["web_url"],
        "state": payload.get("state"),
        "merged": bool(payload.get("merged_at")),
        "draft": bool(payload.get("draft")),
    }
