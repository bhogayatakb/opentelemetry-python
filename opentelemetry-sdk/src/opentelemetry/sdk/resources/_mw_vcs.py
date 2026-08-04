# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Middleware fork-only addition.

Resolves VCS metadata (repository URL, commit SHA) consumed by Middleware's
Ops AI to point generated fixes at the right file/line and open PRs against
the right commit/branch:
https://docs.middleware.io/opsai/apm_configuration/python#vcs-metadata

Reads ``MW_VCS_REPOSITORY_URL`` / ``MW_VCS_COMMIT_SHA`` if set (e.g. injected
by CI), otherwise falls back to the local ``.git`` checkout via the `git`
CLI. Mirrors middleware-labs/agent-apm-python's `get_git_info()` (PRs #61,
#62), but shells out to `git` instead of depending on GitPython, so this adds
no mandatory dependency to opentelemetry-sdk.

This is registered as the "vcs" resource detector entry point rather than
wired into the default detector list, so it stays purely opt-in and requires
no changes to `Resource.create` / `_build_resource_detectors`: enable it by
adding "vcs" to the `OTEL_EXPERIMENTAL_RESOURCE_DETECTORS` environment
variable (e.g. `OTEL_EXPERIMENTAL_RESOURCE_DETECTORS=vcs`).
"""

from __future__ import annotations

import subprocess
from logging import getLogger
from os import environ

from opentelemetry.sdk.resources import Resource, ResourceDetector

_logger = getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 2

VCS_REPOSITORY_URL = "vcs.repository_url"
VCS_COMMIT_SHA = "vcs.commit_sha"


def _run_git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except Exception as error:  # git missing, not a repo, no HEAD yet, timeout, ...
        _logger.debug("git %s failed: %s", " ".join(args), error)
        return None
    return result.stdout.strip() or None


def _detect_git_info() -> tuple[str | None, str | None]:
    # `git` walks up to find the enclosing repository on its own, so this
    # works from any subdirectory without needing to search for `.git`.
    commit_sha = _run_git("rev-parse", "HEAD")
    repository_url = _run_git("config", "--get", "remote.origin.url")
    if repository_url and repository_url.endswith(".git"):
        repository_url = repository_url[: -len(".git")]
    return repository_url, commit_sha


class VcsResourceDetector(ResourceDetector):
    """Adds `vcs.repository_url` / `vcs.commit_sha` resource attributes from
    `MW_VCS_REPOSITORY_URL` / `MW_VCS_COMMIT_SHA`, falling back to the local
    git checkout. Omits either attribute entirely if neither source resolves
    it."""

    def detect(self) -> Resource:
        repository_url = environ.get("MW_VCS_REPOSITORY_URL")
        commit_sha = environ.get("MW_VCS_COMMIT_SHA")

        if not repository_url or not commit_sha:
            git_repository_url, git_commit_sha = _detect_git_info()
            repository_url = repository_url or git_repository_url
            commit_sha = commit_sha or git_commit_sha

        attributes = {}
        if repository_url:
            attributes[VCS_REPOSITORY_URL] = repository_url
        if commit_sha:
            attributes[VCS_COMMIT_SHA] = commit_sha
        return Resource(attributes)
