"""Concrete current-state capture provider for acceptance evidence."""

# ruff: noqa: D102, D107, PLR0913, SIM117
# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false
# pyright: reportImplicitStringConcatenation=false
# pyright: reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import text

from scripts.release_chain_capture import CaptureObservation

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncEngine

    from scripts.release_runtime_subprocess import (
        DispatchRuntimeRunner,
        VercelRuntimeRunner,
    )
    from scripts.release_vercel_models import ChildCommand

DB_BINDING = text(
    """
    SELECT v.version_num AS revision, s.enabled,
           s.active_authorization_id, s.current_budget_id,
           s.current_binding_id, s.current_cadence_id,
           t.state
    FROM community_sources s
    CROSS JOIN alembic_version v
    LEFT JOIN LATERAL (
        SELECT state FROM source_activation_state_transitions
        WHERE source_id = s.id
        ORDER BY transition_at_db DESC, id DESC LIMIT 1
    ) t ON true
    WHERE s.platform = 'manifold'
    """
)


class AcceptanceRuntimeError(RuntimeError):
    """Stable current-capture error."""


class ReleaseAcceptanceProvider:
    """Capture six redacted live facts with exact read-only commands."""

    def __init__(
        self,
        *,
        repository_root: Path,
        repository: str,
        expected_sha: str,
        engine: AsyncEngine,
        github: DispatchRuntimeRunner,
        vercel: VercelRuntimeRunner,
        vercel_commands: Mapping[str, ChildCommand],
        api_url: str,
        web_url: str,
        http_fetch: Callable[[str], bytes],
        clock: Callable[[], datetime],
    ) -> None:
        expected = {"vercel-api-inspection.json", "vercel-web-inspection.json"}
        if set(vercel_commands) != expected:
            msg = "acceptance_vercel_set_invalid"
            raise AcceptanceRuntimeError(msg)
        self._root = repository_root
        self._repository = repository
        self._expected_sha = expected_sha
        self._engine = engine
        self._github = github
        self._vercel = vercel
        self._vercel_commands = dict(vercel_commands)
        self._api_url = api_url
        self._web_url = web_url
        self._http_fetch = http_fetch
        self._clock = clock

    def capture(self, member_name: str) -> CaptureObservation:
        handlers: dict[str, Callable[[], bytes]] = {
            "repository-scan.json": self._repository_scan,
            "github-public-scan.json": self._github_scan,
            "vercel-api-inspection.json": lambda: self._vercel_scan(member_name),
            "vercel-web-inspection.json": lambda: self._vercel_scan(member_name),
            "provider-log-disposition.json": self._provider_logs,
            "db-binding-health.json": self._database_binding,
        }
        handler = handlers.get(member_name)
        if handler is None:
            msg = "acceptance_member_unknown"
            raise AcceptanceRuntimeError(msg)
        raw = handler()
        return CaptureObservation(
            evidence_sha256=sha256(raw).hexdigest(),
            captured_at=self._clock(),
            tool_version="release-runtime-v1",
            accepted=True,
        )

    def _repository_scan(self) -> bytes:
        result = self._github.run(
            ("git", "rev-parse", "--verify", "HEAD^{commit}")
        )
        if result.returncode or result.stdout.strip() != self._expected_sha:
            msg = "repository_sha_mismatch"
            raise AcceptanceRuntimeError(msg)
        return result.stdout.encode()

    def _github_scan(self) -> bytes:
        result = self._github.run(
            ("gh", "api", f"/repos/{self._repository}")
        )
        if result.returncode:
            msg = "github_public_scan_failed"
            raise AcceptanceRuntimeError(msg)
        return result.stdout.encode()

    def _vercel_scan(self, name: str) -> bytes:
        result = self._vercel.execute(self._vercel_commands[name])
        if result.returncode:
            msg = "vercel_inspection_failed"
            raise AcceptanceRuntimeError(msg)
        return result.stdout.encode()

    def _provider_logs(self) -> bytes:
        result = self._github.run(
            (
                "gh",
                "api",
                f"/repos/{self._repository}/actions/runs?per_page=1",
            )
        )
        if result.returncode:
            msg = "provider_log_probe_failed"
            raise AcceptanceRuntimeError(msg)
        api = self._http_fetch(f"{self._api_url.rstrip('/')}/health")
        web = self._http_fetch(self._web_url)
        return sha256(result.stdout.encode() + api + web).digest()

    def _database_binding(self) -> bytes:
        async def read() -> bytes:
            async with self._engine.connect() as connection:
                async with connection.begin():
                    _ = await connection.execute(
                        text(
                            "SET TRANSACTION ISOLATION LEVEL "
                            "REPEATABLE READ READ ONLY"
                        )
                    )
                    row = (await connection.execute(DB_BINDING)).mappings().one()
                    payload = {
                        key: str(value) if value is not None else None
                        for key, value in row.items()
                    }
                    return json.dumps(
                        payload,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode()

        return anyio.run(read)


__all__ = ("AcceptanceRuntimeError", "ReleaseAcceptanceProvider")
