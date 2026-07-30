"""Current Vercel alias observations for retention proofs."""

# ruff: noqa: D102, D107, EM101, EM102
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from scripts.release_runtime_subprocess import VercelRuntimeRunner
from scripts.release_vercel_models import (
    CLI_VERSION,
    ORG_ID_ENV,
    PROJECTS,
    TEAM_SLUG,
    TOKEN_ENV,
    ChildCommand,
    ReleaseHoldError,
)
from scripts.release_vercel_retention import (
    EVIDENCE_SOURCE,
    AliasRetentionObservation,
)
from scripts.release_vercel_validation import validate_alias_listing

if TYPE_CHECKING:
    from datetime import datetime

    from scripts.release_vercel_models import ChildRunner


class AliasRetentionProvider(Protocol):
    """Injectable current-alias observation boundary."""

    def observe(
        self,
        project_kind: Literal["api", "web"],
        alias_receipt: Mapping[str, object],
        observed_at: datetime,
    ) -> AliasRetentionObservation: ...


class AliasRetentionRuntime:
    """Run exact pinned alias-list and inspect reads for one project."""

    def __init__(
        self,
        repository_root: Path | None = None,
        *,
        runner: ChildRunner | None = None,
    ) -> None:
        self._root: Path = (
            Path(__file__).resolve().parents[3]
            if repository_root is None
            else repository_root
        ).resolve(strict=True)
        self._runner: ChildRunner = (
            VercelRuntimeRunner() if runner is None else runner
        )

    def observe(
        self,
        project_kind: Literal["api", "web"],
        alias_receipt: Mapping[str, object],
        observed_at: datetime,
    ) -> AliasRetentionObservation:
        _project_name, project_id_env, _ = PROJECTS[project_kind]
        alias = alias_receipt.get("alias")
        deployment_id = alias_receipt.get("deployment_id")
        if not isinstance(alias, str) or not isinstance(deployment_id, str):
            raise ReleaseHoldError("alias_retention_receipt_incomplete")
        base = ("npx", "--yes", f"vercel@{CLI_VERSION}")
        environment = {
            "VERCEL_ORG_ID_FROM_ENV": ORG_ID_ENV,
            "VERCEL_PROJECT_ID_FROM_ENV": project_id_env,
            "VERCEL_TOKEN_FROM_ENV": TOKEN_ENV,
        }
        listing = self._run_json(
            ChildCommand(
                "alias-ls",
                (*base, "alias", "ls", "--scope", TEAM_SLUG, "--json"),
                self._root,
                environment,
            )
        )
        validate_alias_listing(
            listing,
            expected_alias=alias,
            expected_deployment_id=deployment_id,
        )
        inspected = self._run_json(
            ChildCommand(
                "inspect",
                (*base, "inspect", alias, "--scope", TEAM_SLUG, "--json"),
                self._root,
                environment,
            )
        )
        metadata = inspected.get("meta")
        source_sha = (
            cast("Mapping[str, object]", metadata).get("githubCommitSha")
            if isinstance(metadata, Mapping)
            else None
        )
        values = (
            inspected.get("id"),
            inspected.get("name"),
            inspected.get("team"),
            source_sha,
            inspected.get("readyState"),
            inspected.get("target"),
        )
        if not all(isinstance(value, str) and value for value in values):
            raise ReleaseHoldError("alias_retention_inspect_incomplete")
        return AliasRetentionObservation(
            project_kind=project_kind,
            alias=alias,
            deployment_id=cast("str", values[0]),
            project_name=cast("str", values[1]),
            team_slug=cast("str", values[2]),
            source_sha=cast("str", values[3]),
            ready_state=cast("str", values[4]),
            environment=cast("str", values[5]),
            evidence_source=EVIDENCE_SOURCE,
            observed_at=observed_at,
        )

    def _run_json(self, command: ChildCommand) -> dict[str, object]:
        result = self._runner.execute(command)
        if result.returncode:
            raise ReleaseHoldError(f"alias_retention_{command.stage}_failed")
        try:
            value = cast("object", json.loads(result.stdout))
        except json.JSONDecodeError as error:
            raise ReleaseHoldError(
                f"alias_retention_{command.stage}_invalid_json"
            ) from error
        if not isinstance(value, dict):
            raise ReleaseHoldError(
                f"alias_retention_{command.stage}_invalid_json"
            )
        return cast("dict[str, object]", value)


__all__ = ("AliasRetentionProvider", "AliasRetentionRuntime")
