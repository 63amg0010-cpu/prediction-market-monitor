"""Hash-only API, Web, repository, and provider-log privacy verifier."""

# ruff: noqa: D102, D107, EM101, TC003

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from scripts.release_privacy_contracts import (
    IncidentScope,
    ProviderVerification,
)
from scripts.release_runtime_http import ReadOnlyHttpProbe
from scripts.release_runtime_subprocess import RunProcess, VercelRuntimeRunner
from scripts.release_vercel_models import ChildCommand, ChildResult
from scripts.runtime_privacy_adapter import (
    PrivacyProofSession,
    PrivacyRuntimeError,
    digest,
)
from scripts.runtime_privacy_adapter_provider_checks import (
    clean,
    health_ok,
    https_base,
    json_object,
    repository_scan,
    zero_content,
)


class VercelRunner(Protocol):
    """Existing bounded Vercel subprocess boundary."""

    def execute(self, command: ChildCommand) -> ChildResult: ...


class PrivacyProviderAdapter:
    """Concrete read-only provider verifier returning hashes and booleans only."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        repository_root: Path,
        api_url: str,
        web_url: str,
        identities: Mapping[str, str],
        token_env: str,
        team_slug: str,
        api_project_name: str,
        web_project_name: str,
        proof: PrivacyProofSession,
        http: ReadOnlyHttpProbe,
        vercel: VercelRunner,
    ) -> None:
        self._root: Path = repository_root.resolve(strict=True)
        self._api: str = https_base(api_url)
        self._web: str = https_base(web_url)
        if not identities or any(
            not key or not value for key, value in identities.items()
        ):
            raise PrivacyRuntimeError("provider_identity_environment_empty")
        if (
            not token_env
            or not team_slug
            or not api_project_name
            or not web_project_name
        ):
            raise PrivacyRuntimeError("provider_preflight_incomplete")
        self._identities: dict[str, str] = dict(identities)
        self._token_env: str = token_env
        self._team: str = team_slug
        self._projects: tuple[str, str] = (
            api_project_name,
            web_project_name,
        )
        self._proof: PrivacyProofSession = proof
        self._http: ReadOnlyHttpProbe = http
        self._vercel: VercelRunner = vercel

    @classmethod
    def from_env(  # noqa: PLR0913
        cls,
        *,
        repository_root: Path,
        api_url: str,
        web_url: str,
        identity_env_names: tuple[str, ...],
        token_env: str,
        team_slug: str,
        api_project_name: str,
        web_project_name: str,
        proof: PrivacyProofSession,
        environ: Mapping[str, str] | None = None,
        run_process: RunProcess = subprocess.run,
    ) -> PrivacyProviderAdapter:
        """Resolve every provider identity before HTTP or subprocess I/O."""
        source = os.environ if environ is None else environ
        names = (*identity_env_names, token_env)
        if not identity_env_names or any(
            not name or not source.get(name) for name in names
        ):
            raise PrivacyRuntimeError("provider_identity_environment_empty")
        identities = {name: source[name] for name in identity_env_names}
        return cls(
            repository_root=repository_root,
            api_url=api_url,
            web_url=web_url,
            identities=identities,
            token_env=token_env,
            team_slug=team_slug,
            api_project_name=api_project_name,
            web_project_name=web_project_name,
            proof=proof,
            http=ReadOnlyHttpProbe(),
            vercel=VercelRuntimeRunner(environ=source, run_process=run_process),
        )

    def _logs(self) -> tuple[bool, bool, bool, str]:
        outputs: list[str] = []
        conclusive = logs_clean = empty = True
        for project in self._projects:
            result = self._vercel.execute(
                ChildCommand(
                    stage="privacy-provider-logs",
                    argv=(
                        "vercel",
                        "logs",
                        project,
                        "--scope",
                        self._team,
                        "--environment",
                        "production",
                        "--since",
                        "30d",
                        "--json",
                    ),
                    cwd=self._root,
                    env={"VERCEL_TOKEN_FROM_ENV": self._token_env},
                )
            )
            conclusive &= result.returncode == 0
            raw = (result.stdout + result.stderr).encode()
            logs_clean &= clean(raw, ())
            empty &= not result.stdout.strip()
            outputs.append(digest((result.returncode, result.stdout, result.stderr)))
        return logs_clean, conclusive, empty, digest(outputs)

    async def verify(self, scope: IncidentScope) -> ProviderVerification:
        protected = tuple(
            value.encode()
            for value in (
                str(scope.source_id),
                str(scope.epoch_id),
                str(scope.activation_nonce),
                *self._identities.values(),
            )
        )
        posts_raw = self._http.fetch(f"{self._api}/v1/posts?source=manifold&limit=1")
        health_raw = self._http.fetch(f"{self._api}/v1/health")
        web_raw = self._http.fetch(self._web)
        direct_zero = zero_content(json_object(posts_raw))
        healthy, binding_zero = health_ok(json_object(health_raw))
        public_clean = all(
            clean(raw, protected) for raw in (posts_raw, health_raw, web_raw)
        )
        repository_clean, repository_hashes = repository_scan(
            self._root,
            protected,
        )
        log_clean, log_conclusive, log_expired, log_sha = self._logs()
        static_sha = digest(
            (
                digest(posts_raw.hex()),
                digest(health_raw.hex()),
                digest(web_raw.hex()),
                repository_hashes,
            )
        )
        accepted = all(
            (
                direct_zero,
                healthy,
                binding_zero,
                public_clean,
                repository_clean,
                log_clean,
                log_conclusive,
                log_expired,
            )
        )
        if accepted:
            self._proof.record(
                "provider", scope, digest((static_sha, log_sha)), accepted=True
            )
        return ProviderVerification(
            zero_provider_binding=binding_zero,
            direct_api_zero=direct_zero,
            aliases_and_health_restored=healthy and bool(web_raw),
            repository_static_scan_clean=repository_clean,
            public_surfaces_static_scan_clean=public_clean,
            provider_logs_clean=log_clean,
            provider_log_search_conclusive=log_conclusive,
            provider_logs_deleted_or_expired=log_expired,
            static_scan_sha256=static_sha,
            provider_log_disposition_sha256=log_sha,
        )


__all__ = ("PrivacyProviderAdapter",)
