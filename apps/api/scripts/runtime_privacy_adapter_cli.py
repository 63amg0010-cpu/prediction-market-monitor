"""Exact CLI handler registry for concrete privacy runtime adapters."""

# ruff: noqa: EM101, TC003

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import anyio
from pydantic import JsonValue

from scripts.release_gate_cli_io import read_document, write_document
from scripts.release_privacy import (
    model_sha256,
    privacy_contain,
    privacy_purge,
    privacy_verify,
)
from scripts.release_privacy_contracts import IncidentScope, ViolationKind
from scripts.release_privacy_models import (
    ContainmentReceipt,
    PurgeReceipt,
)
from scripts.release_vercel_models import canonical_bytes, verify_receipt
from scripts.runtime_privacy_adapter import (
    PrivacyProofSession,
    PrivacyRuntimeError,
)
from scripts.runtime_privacy_adapter_cli_validation import matrix_proof
from scripts.runtime_privacy_adapter_db import PrivacyDatabaseAdapter
from scripts.runtime_privacy_adapter_github import PrivacyGitHubAdapter
from scripts.runtime_privacy_adapter_provider import PrivacyProviderAdapter

ROOT = Path(__file__).resolve().parents[3]
JsonObject = dict[str, JsonValue]


class PrivacyArgs(Protocol):
    """Typed projection of the three argparse privacy command surfaces."""

    database_url_env: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: str
    source_id: str
    epoch_id: str
    violation_kind: ViolationKind
    json_out: str
    repository: str
    github_token_env: str
    containment_receipt: str
    purge_receipt: str
    matrix_b_health: str
    matrix_b_chain: str
    expected_current: str
    api_url: str
    web_url: str
    github_repository_id_env: str
    team_slug: str
    org_id_env: str
    api_project_name: str
    api_project_id_env: str
    web_project_name: str
    web_project_id_env: str
    vercel_token_env: str
    supabase_org_id_env: str
    supabase_project_id_env: str


def _required_env(*names: str) -> None:
    if any(not name or not os.environ.get(name) for name in names):
        raise PrivacyRuntimeError("privacy_environment_empty")


def verified_predecessor(args: PrivacyArgs) -> str:
    """Verify canonical transport, self-hash, acceptance, and exact bindings."""
    path = Path(args.predecessor_receipt)
    raw = path.read_bytes()
    value = read_document(str(path))
    if raw != canonical_bytes(value):
        raise PrivacyRuntimeError("privacy_predecessor_noncanonical")
    return verify_receipt(
        value,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
    )


def _scope(args: PrivacyArgs, predecessor_sha256: str) -> IncidentScope:
    return IncidentScope(
        source_id=UUID(args.source_id),
        epoch_id=UUID(args.epoch_id),
        activation_nonce=UUID(args.activation_nonce),
        violation_kind=args.violation_kind,
        predecessor_sha256=predecessor_sha256,
        reviewed_sha=args.expected_sha,
        approved_plan_sha256=args.expected_plan_sha256,
    )


def _containment(args: PrivacyArgs) -> ContainmentReceipt:
    return ContainmentReceipt.model_validate(read_document(args.containment_receipt))


async def _contain(args: PrivacyArgs) -> int:
    _required_env(args.database_url_env)
    proof = PrivacyProofSession()
    database = PrivacyDatabaseAdapter.from_env(
        args.database_url_env,
        proof,
    )
    try:
        receipt = await privacy_contain(
            _scope(args, verified_predecessor(args)),
            database,
        )
        write_document(
            args.json_out,
            cast("JsonObject", receipt.model_dump(mode="json")),
        )
        return 0
    finally:
        await database.dispose()


async def _purge(args: PrivacyArgs) -> int:
    _required_env(args.database_url_env, args.github_token_env)
    proof = PrivacyProofSession()
    containment = _containment(args)
    scope = _scope(args, containment.predecessor_sha256)
    database = PrivacyDatabaseAdapter.from_env(args.database_url_env, proof)
    github = PrivacyGitHubAdapter.from_env(
        ROOT,
        args.github_token_env,
        proof,
        scope,
        repository=args.repository,
    )
    try:
        predecessor = ContainmentReceipt.model_validate(
            read_document(args.predecessor_receipt)
        )
        if model_sha256(predecessor) != model_sha256(containment):
            raise PrivacyRuntimeError("privacy_predecessor_mismatch")
        receipt = await privacy_purge(
            scope,
            containment,
            database,
            github,
        )
        write_document(
            args.json_out,
            cast("JsonObject", receipt.model_dump(mode="json")),
        )
        return 0
    finally:
        await database.dispose()


def _provider_envs(args: PrivacyArgs) -> tuple[str, ...]:
    return (
        args.github_repository_id_env,
        args.org_id_env,
        args.api_project_id_env,
        args.web_project_id_env,
        args.supabase_org_id_env,
        args.supabase_project_id_env,
    )


async def _verify(args: PrivacyArgs) -> int:
    identity_envs = _provider_envs(args)
    _required_env(
        args.database_url_env,
        args.github_token_env,
        args.vercel_token_env,
        *identity_envs,
    )
    containment = _containment(args)
    purge = PurgeReceipt.model_validate(read_document(args.purge_receipt))
    matrix = matrix_proof(args)
    scope = _scope(args, containment.predecessor_sha256)
    proof = PrivacyProofSession()
    database = PrivacyDatabaseAdapter.from_env(args.database_url_env, proof)
    github = PrivacyGitHubAdapter.from_env(
        ROOT,
        args.github_token_env,
        proof,
        scope,
        repository=args.repository,
    )
    provider = PrivacyProviderAdapter.from_env(
        repository_root=ROOT,
        api_url=args.api_url,
        web_url=args.web_url,
        identity_env_names=identity_envs,
        token_env=args.vercel_token_env,
        team_slug=args.team_slug,
        api_project_name=args.api_project_name,
        web_project_name=args.web_project_name,
        proof=proof,
    )
    try:
        receipt = await privacy_verify(
            scope,
            containment,
            purge,
            matrix,
            database,
            github,
            provider,
        )
        write_document(
            args.json_out,
            cast("JsonObject", receipt.model_dump(mode="json")),
        )
        return 0
    finally:
        await database.dispose()


def run_privacy_contain(args: argparse.Namespace) -> int:
    return anyio.run(_contain, cast("PrivacyArgs", cast("object", args)))


def run_privacy_purge(args: argparse.Namespace) -> int:
    return anyio.run(_purge, cast("PrivacyArgs", cast("object", args)))


def run_privacy_verify(args: argparse.Namespace) -> int:
    return anyio.run(_verify, cast("PrivacyArgs", cast("object", args)))


HANDLERS = {
    "privacy-contain": run_privacy_contain,
    "privacy-purge": run_privacy_purge,
    "privacy-verify": run_privacy_verify,
}

__all__ = ("HANDLERS", "PrivacyArgs", "verified_predecessor")
