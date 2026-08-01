"""Builders used only by the Todo 11 evidence contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast
from uuid import UUID

from scripts.release_evidence import (
    canonical_hash,
    evidence_import,
    evidence_join,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from scripts.free_tier_domain import JsonObject

SHA = "a" * 40
NONCE = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)
PLAN_PATH = ".omo/plans/fresh-multi-source-search.md"
KINDS = (
    "local-measurement",
    "quota-manifest",
    "github-capture",
    "vercel-api-capture",
    "vercel-web-capture",
    "supabase-capture",
    "production-measurement",
)
PROVIDERS = ("github", "vercel-api", "vercel-web", "supabase")


def review_record(plan: bytes) -> JsonObject:
    """Create one exact protected front matter document."""
    digest = sha256(plan).hexdigest()
    lane = {
        "status": "approved",
        "model": "gpt-5.6",
        "reasoning_effort": "high",
        "workspace_root": "C:/owner/workspace",
        "runtime_home": None,
        "target": PLAN_PATH,
        "round_id": "round-27",
        "plan_sha256": digest,
        "plan_bytes": len(plan),
        "descriptor_chain_verified": True,
        "regular_file": True,
        "launch_id": "launch-one",
        "session": "owner-only",
        "result": "OKAY",
    }
    return cast(
        "JsonObject",
        {
            "slug": "fresh-multi-source-search",
            "status": "approved",
            "intent": "unclear",
            "review_required": True,
            "plan_path": PLAN_PATH,
            "plan_sha256": digest,
            "plan_bytes": len(plan),
            "review_round_id": "round-27",
            "round_status": "approved",
            "pending-action": "execute",
            "review": {
                "momus": lane,
                "independent": {**lane, "launch_id": "launch-two"},
            },
            "approach": "reviewed",
        },
    )


def base_receipt(
    command: str,
    plan: str,
    predecessor: str | None,
) -> JsonObject:
    """Create one accepted chain receipt with exact approval hashes."""
    return {
        "schema_version": 1,
        "command": command,
        "attempt": 1,
        "reviewed_sha": SHA,
        "approved_plan_sha256": plan,
        "approval_round_id": sha256(b"round-27").hexdigest(),
        "approval_launch_sha256s": [
            sha256(b"launch-one").hexdigest(),
            sha256(b"launch-two").hexdigest(),
        ],
        "activation_nonce": str(NONCE),
        "dispatch_nonce": None,
        "state_before": "20260726_0009",
        "state_after": "20260726_0009",
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": predecessor,
    }


def artifacts(
    plan: str,
) -> tuple[
    list[JsonObject],
    JsonObject,
    JsonObject,
]:
    """Create exact provider, local, and Production evidence leaves."""
    plans = ("public-standard", "hobby", "hobby", "free")
    shared = {
        "reviewed_sha": SHA,
        "approved_plan_sha256": plan,
        "activation_nonce": str(NONCE),
        "captured_at": "2026-07-29T00:00:00Z",
    }
    captures: list[JsonObject] = [
        {
            **shared,
            "provider": provider,
            "plan": provider_plan,
            "paid_enabled": False,
            "overage_enabled": False,
            "add_ons_enabled": False,
            "accepted": True,
        }
        for provider, provider_plan in zip(PROVIDERS, plans, strict=True)
    ]
    local: JsonObject = {**shared, "kind": "local-measurement"}
    production: JsonObject = {
        **shared,
        "transaction_read_only": True,
        "sampled": False,
    }
    return captures, local, production


def quota_manifest(plan: str) -> JsonObject:
    """Create the exact pre-0010 quota-manifest evidence leaf."""
    return {
        "schema": "free-tier.quota-manifest.v1",
        "phase": "pre-0010",
        "reviewed_sha": SHA,
        "approved_plan_sha256": plan,
        "activation_nonce": str(NONCE),
    }


def evidence_graph(
    root: Mapping[str, object],
    documents: list[Mapping[str, object]],
    tmp_path: Path,
) -> JsonObject:
    """Create all seven content-addressed branches and their join."""
    branches: list[JsonObject] = []
    for kind, document in zip(KINDS, documents, strict=True):
        hashed = canonical_hash(document)
        digest = str(hashed["input_sha256"])
        branches.append(
            evidence_import(
                kind=kind,
                document=document,
                expected_input_sha256=digest,
                output_path=tmp_path / kind / f"{digest}.json",
                expected_sha=SHA,
                expected_plan_sha256=str(root["approved_plan_sha256"]),
                activation_nonce=NONCE,
                predecessor_receipt=hashed,
            )
        )
    return evidence_join(
        deployment_root=root,
        branches=branches,
        expected_branches=KINDS,
        expected_sha=SHA,
        expected_plan_sha256=str(root["approved_plan_sha256"]),
        activation_nonce=NONCE,
        predecessor_receipt=root,
    )


class SecretRunner:
    """Capture exact secret-upload argv and stdin without output."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes]] = []

    def run(self, argv: tuple[str, ...], stdin: bytes) -> int:
        """Record one injected child call."""
        self.calls.append((argv, stdin))
        return 0


__all__ = (
    "NONCE",
    "NOW",
    "PLAN_PATH",
    "SHA",
    "SecretRunner",
    "artifacts",
    "base_receipt",
    "evidence_graph",
    "quota_manifest",
    "review_record",
)
