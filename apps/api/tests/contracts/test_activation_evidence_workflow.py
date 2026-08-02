from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml
from app.domain.types import JsonValue
from pydantic import TypeAdapter
from scripts.activation_evidence_models import (
    ActivationEvidenceVerifyRequest,
    PublicActivationAttestation,
)

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "activation-evidence.yml"
WORKFLOW_ADAPTER = TypeAdapter(dict[str, JsonValue])


def workflow() -> dict[str, JsonValue]:
    source = WORKFLOW.read_text(encoding="utf-8").replace("\non:\n", "\n'on':\n", 1)
    return WORKFLOW_ADAPTER.validate_python(yaml.safe_load(source))


def mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def verification_step(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    verify = mapping(mapping(document["jobs"])["verify"])
    steps = verify["steps"]
    assert isinstance(steps, list)
    matching_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Verify through scoped release API"
    ]
    assert len(matching_steps) == 1
    return mapping(matching_steps[0])


def test_activation_evidence_identity_and_artifact_are_byte_exact() -> None:
    # Given: the credential-free activation evidence workflow.
    document = workflow()
    source = WORKFLOW.read_text(encoding="utf-8")

    # When/Then: run and archive identities bind generation, dispatch, and attempt.
    exact = (
        "activation-evidence-${{ inputs.activation_nonce }}-generation-"
        "${{ inputs.attestation_generation }}-${{ inputs.dispatch_nonce }}"
        "-attempt-${{ inputs.attempt }}"
    )
    assert document["run-name"] == exact
    assert f"name: {exact}" in source
    assert "activation-attestation-generation-${N}.json" in source
    assert (
        "activation-attestation-${ACTIVATION_NONCE}-generation-${N}.json" not in source
    )


def test_activation_workflow_has_one_secret_and_no_database_credential() -> None:
    # Given: the complete workflow source.
    document = workflow()
    source = WORKFLOW.read_text(encoding="utf-8")
    verify = mapping(mapping(document["jobs"])["verify"])

    # When/Then: only the attestation secret crosses the protected environment.
    assert verify["environment"] == "production-collector"
    assert source.count("secrets.MANIFOLD_ACTIVATION_ATTESTATION_JSON") == 1
    assert "MIGRATION_DATABASE_URL" not in source
    assert "DATABASE_URL" not in source
    assert "postgresql" not in source.lower()


def test_activation_evidence_calls_only_scoped_read_only_api_surface() -> None:
    # Given: a protected-main OIDC workflow.
    source = WORKFLOW.read_text(encoding="utf-8")

    # When/Then: its sole network mutation is the scoped verification endpoint.
    assert "id-token: write" in source
    assert "--request POST" in source
    assert source.count("/internal/release/activation-evidence-verify") == 1
    assert "curl" in source
    assert "persist-credentials: false" in source
    assert "retention-days: 1" in source


def test_activation_workflow_builds_the_complete_schema_valid_api_request() -> None:
    # Given: the API request model and the workflow's verification step.
    document = workflow()
    request_model_fields = ActivationEvidenceVerifyRequest.model_fields
    step = verification_step(document)
    run = step["run"]
    assert isinstance(run, str)

    # When: the workflow's jq request builder is inspected against the API model.
    request_fields = {
        "attestation": "attestation:$attestation[0]",
        "attestation_sha256": "attestation_sha256:$attestation_sha256",
        "reservation_receipt_sha256": "reservation_receipt_sha256:$reservation_sha256",
        "dispatch_nonce": "dispatch_nonce:$dispatch_nonce",
        "attempt": "attempt:$attempt",
        "run_id": "run_id:$run_id",
        "run_attempt": "run_attempt:$run_attempt",
        "head_sha": "head_sha:$head_sha",
    }

    # Then: every required request field is loaded from the canonical artifact or run.
    assert set(request_fields) == set(request_model_fields)
    attestation_artifact = (
        "activation-attestation-generation-${ATTESTATION_GENERATION}.json"
    )
    assert f' --slurpfile attestation "{attestation_artifact}"' in run
    for expression in request_fields.values():
        assert expression in run


def test_activation_workflow_rejects_foreign_attestation_fields() -> None:
    # Given: the public attestation model and protected canonicalization step.
    source = WORKFLOW.read_text(encoding="utf-8")
    expected_fields = set(PublicActivationAttestation.model_fields)

    # When: the jq allowlist is read from the workflow boundary.
    match = re.search(
        r"and \(keys \| sort\) == \[(?P<keys>.*?)\]\n\s+and \.schema_version",
        source,
        flags=re.DOTALL,
    )

    # Then: the allowlist matches the schema exactly and rejects foreign fields.
    assert match is not None
    observed_fields = set(re.findall(r'"([a-z0-9_]+)"', match.group("keys")))
    assert observed_fields == expected_fields
    assert 'else error("activation attestation schema rejected")' in source


def test_workflow_uses_the_api_model_for_canonical_url_hashing() -> None:
    # Given: the workflow command and an attestation with a normalizable bare URL.
    source = WORKFLOW.read_text(encoding="utf-8")
    attestation: dict[str, JsonValue] = {
        "schema_version": 1,
        "reviewed_sha": "a" * 40,
        "activation_nonce": "11111111-1111-4111-8111-111111111111",
        "attestation_generation": 1,
        "source_scope_version": "reviewed-manifold-v1",
        "authorization_evidence_sha256": "b" * 64,
        "free_tier_evidence_sha256": "c" * 64,
        "provenance_sha256": "d" * 64,
        "predecessor_attestation_sha256": None,
        "captured_at": "2026-07-28T00:00:00Z",
        "evidence_database_time": "2026-07-28T00:00:00Z",
        "public_evidence_urls": ["https://example.com"],
    }

    # When: the same committed canonicalizer used by the workflow receives it.
    completed = subprocess.run(  # noqa: S603 - Fixed interpreter and script path.
        [
            sys.executable,
            str(ROOT / "apps" / "api" / "scripts" / "activation_evidence_models.py"),
        ],
        input=WORKFLOW_ADAPTER.dump_json(attestation),
        capture_output=True,
        check=False,
    )

    # Then: the executable surface normalizes the URL and workflow invokes it.
    assert completed.returncode == 0
    assert b'"public_evidence_urls":["https://example.com/"]' in completed.stdout
    assert "python apps/api/scripts/activation_evidence_models.py" in source
    assert "audience=monitor-control" in source


def test_migration_downloads_only_the_exact_run_owned_activation_artifact() -> None:
    # Given: the protected migration workflow's 0011 attestation verifier.
    source = (ROOT / ".github" / "workflows" / "migrate.yml").read_text(
        encoding="utf-8"
    )

    # When/Then: selection binds every identity and rejects the obsolete name.
    exact = (
        "activation-evidence-${ACTIVATION_NONCE}-generation-"
        "${ATTESTATION_GENERATION}-${ATTESTATION_DISPATCH_NONCE}-attempt-${ATTEMPT}"
    )
    assert exact in source
    assert "activation-attestation-generation-${ATTESTATION_GENERATION}.json" in source
    assert "activation-attestation-${ACTIVATION_NONCE}-generation-" not in source
    assert "activation-evidence-public-receipt.json" in source
