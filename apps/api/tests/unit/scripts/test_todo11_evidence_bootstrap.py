from __future__ import annotations

import inspect
from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from scripts.release_evidence import (
    EvidenceHoldError,
    PublicActivationAttestation,
    ReviewRecordAccess,
    attest,
    attestation_secret_upload,
    canonical_bytes,
    no_spend_preflight,
    receipt_sha256,
    validate_review_record,
)

from .release_evidence_test_support import (
    NONCE,
    NOW,
    PLAN_PATH,
    SHA,
    SecretRunner,
    artifacts,
    base_receipt,
    evidence_graph,
    quota_manifest,
    review_record,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def test_review_record_binds_live_bytes_and_unconditional_distinct_approvals() -> None:
    plan = b"approved plan bytes"
    record = review_record(plan)
    access = ReviewRecordAccess(
        committed=False,
        symlinked=False,
        world_readable=False,
    )
    result = validate_review_record(
        record,
        access=access,
        live_plan_path=PLAN_PATH,
        live_plan_bytes=plan,
        expected_sha=SHA,
    )
    assert result.approved_plan_sha256 == sha256(plan).hexdigest()
    bad = deepcopy(record)
    bad["status"] = "pending"
    with pytest.raises(EvidenceHoldError, match="review_record_schema_rejected"):
        _ = validate_review_record(
            bad,
            access=access,
            live_plan_path=PLAN_PATH,
            live_plan_bytes=plan,
            expected_sha=SHA,
        )


def test_content_addressed_graph_and_credential_free_no_spend(
    tmp_path: Path,
) -> None:
    assert canonical_bytes({"z": 1e21, "a": 1e-6}) == (b'{"a":0.000001,"z":1e+21}')
    plan_bytes = b"approved plan bytes"
    plan = sha256(plan_bytes).hexdigest()
    root = base_receipt("deployment-prestate", plan, None)
    captures, local, production = artifacts(plan)
    manifest = quota_manifest(plan)
    joined = evidence_graph(root, [local, manifest, *captures, production], tmp_path)
    assert joined["branch_kinds"] == [
        "local-measurement",
        "quota-manifest",
        "github-capture",
        "vercel-api-capture",
        "vercel-web-capture",
        "supabase-capture",
        "production-measurement",
    ]
    free_tier = {
        **base_receipt("free-tier-pre-0010", plan, receipt_sha256(joined)),
        "phase": "pre-0010",
        "db_now": "2026-07-29T01:02:03Z",
        "manifest_sha256": sha256(canonical_bytes(manifest)).hexdigest(),
        "measurements_sha256": sha256(canonical_bytes(local)).hexdigest(),
        "dimensions": [
            {
                "name": "all",
                "ratio": 0.699,
                "status": "known",
                "accepted": True,
            }
        ],
    }
    access = ReviewRecordAccess(
        committed=False,
        symlinked=False,
        world_readable=False,
    )

    def run_no_spend(
        capture_values: Sequence[Mapping[str, object]],
        *,
        evidence_join: Mapping[str, object] = joined,
    ) -> dict[str, object]:
        return no_spend_preflight(
            review_record=review_record(plan_bytes),
            review_access=access,
            live_plan_path=PLAN_PATH,
            live_plan_bytes=plan_bytes,
            expected_sha=SHA,
            activation_nonce=NONCE,
            deployment_prestate=root,
            evidence_join_receipt=evidence_join,
            provider_captures=capture_values,
            production_measurements=production,
            free_tier_result=free_tier,
            predecessor_receipt=free_tier,
            bootstrap_attempt_exists=False,
        )

    receipt = run_no_spend(captures)
    assert receipt["operation_scope"] == "migrate-0010-bootstrap-only"
    assert {"database_url", "ledger"}.isdisjoint(
        inspect.signature(no_spend_preflight).parameters
    )
    paid = deepcopy(captures)
    paid[1]["paid_enabled"] = True
    with pytest.raises(EvidenceHoldError, match="provider_spend_enabled"):
        _ = run_no_spend(paid)
    missing_manifest = deepcopy(joined)
    missing_manifest["branch_kinds"] = [
        "local-measurement",
        "github-capture",
        "vercel-api-capture",
        "vercel-web-capture",
        "supabase-capture",
        "production-measurement",
    ]
    for field in ("branch_input_sha256s", "branch_receipt_sha256s"):
        branches = missing_manifest[field]
        assert isinstance(branches, dict)
        del branches["quota-manifest"]
    with pytest.raises(
        EvidenceHoldError,
        match="pre_0010_evidence_graph_rejected",
    ):
        _ = run_no_spend(captures, evidence_join=missing_manifest)


def test_public_attestation_is_closed_and_secret_upload_is_stdin_only() -> None:
    plan = "b" * 64
    captures, _, _ = artifacts(plan)
    predecessor = base_receipt("compat-state", plan, "9" * 64)
    bindings = {
        "reviewed_sha": SHA,
        "approved_plan_sha256": plan,
        "activation_nonce": str(NONCE),
    }
    artifact = attest(
        provider_captures=captures,
        authorization_live_proof={**bindings, "accepted": True},
        free_tier_result={
            **bindings,
            "accepted": True,
            "dimensions": [
                {"name": "usage", "numerator": 1, "quota": 10, "ratio": 0.1}
            ],
        },
        measurement_receipt={
            **bindings,
            "accepted": True,
            "transaction_read_only": True,
        },
        attestation_generation=1,
        database_time=NOW,
        source_scope_version="phase1-reviewed-v1",
        predecessor_attestation_sha256=None,
        public_evidence_urls=(
            "https://github.com/63amg0010-cpu/prediction-market-monitor/actions",
        ),
        expected_sha=SHA,
        expected_plan_sha256=plan,
        activation_nonce=NONCE,
        predecessor_receipt=predecessor,
    )
    with pytest.raises(ValidationError):
        _ = PublicActivationAttestation.model_validate(
            {
                **artifact.attestation.model_dump(mode="json"),
                "protected_project_id": "secret",
            }
        )
    runner = SecretRunner()
    receipt = attestation_secret_upload(
        runner,
        canonical_attestation=artifact.canonical_attestation,
        predecessor_receipt=artifact.receipt,
        expected_sha=SHA,
        expected_plan_sha256=plan,
        activation_nonce=NONCE,
    )
    argv, stdin = runner.calls[0]
    assert argv[-2:] == ("--env", "production-collector")
    assert stdin == artifact.canonical_attestation
    assert stdin not in canonical_bytes(receipt)
