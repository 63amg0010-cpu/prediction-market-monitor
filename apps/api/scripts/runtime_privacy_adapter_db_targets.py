"""Protected frozen-target row parsing for the privacy database adapter."""

# ruff: noqa: EM101, TC003

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import SecretStr

from scripts.release_privacy_contracts import (
    ArtifactTarget,
    CacheTarget,
    FrozenTarget,
    WorkflowTarget,
)
from scripts.runtime_privacy_adapter import PrivacyRuntimeError

WorkflowStatus = Literal[
    "queued",
    "in_progress",
    "waiting",
    "pending",
    "requested",
    "completed",
]


def frozen_target(row: Mapping[str, object]) -> FrozenTarget:
    """Parse one schema-closed durable target without logging its value."""
    if row["kind"] == "artifact":
        return ArtifactTarget(artifact_id=int(str(row["value"])))
    if row["kind"] == "workflow":
        raw_status = row["status"]
        allowed = {
            "queued",
            "in_progress",
            "waiting",
            "pending",
            "requested",
            "completed",
        }
        if not isinstance(raw_status, str) or raw_status not in allowed:
            raise PrivacyRuntimeError("github_workflow_status_invalid")
        return WorkflowTarget(
            run_id=int(str(row["value"])),
            status=cast("WorkflowStatus", raw_status),
        )
    return CacheTarget(key=SecretStr(str(row["value"])))


__all__ = ("frozen_target",)
