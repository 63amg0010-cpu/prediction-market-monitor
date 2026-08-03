from __future__ import annotations

import base64
import json
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from scripts.migration_dispatch_validator import (
    DispatchRequest,
    DispatchValidationError,
    RunCandidate,
    canonical_body,
    select_unique_run,
    validate_current,
    validate_database_urls,
    validate_dispatch,
    validate_heads,
    validate_result,
)

if TYPE_CHECKING:
    from app.domain.types import JsonValue

SHA = "a" * 40
PLAN_SHA = "b" * 64
NO_SPEND_SHA = "d" * 64
ARTIFACT_SHA = "e" * 64
LAUNCH_ONE = "1" * 64
LAUNCH_TWO = "2" * 64
ACTIVATION_NONCE = UUID("11111111-1111-4111-8111-111111111111")
DISPATCH_NONCE = UUID("22222222-2222-4222-8222-222222222222")
TEST_DATABASE_CREDENTIAL = "local-contract-fixture"


def _encoded(value: JsonValue) -> str:
    return base64.b64encode(canonical_body(value)).decode("ascii")


def _database_url(driver: str, host: str, port: int) -> str:
    return f"{driver}://user:{TEST_DATABASE_CREDENTIAL}@{host}:{port}/postgres"


def _review_root() -> dict[str, JsonValue]:
    root: dict[str, JsonValue] = {
        "activation_nonce": str(ACTIVATION_NONCE),
        "approval_launch_sha256s": [LAUNCH_ONE, LAUNCH_TWO],
        "approval_round_id": "3" * 64,
        "approved_plan_sha256": PLAN_SHA,
        "command": "deployment-prestate",
        "protected_identity_hashes": {
            "github_repository": "4" * 64,
            "supabase_project": "5" * 64,
            "vercel_api_project": "6" * 64,
            "vercel_web_project": "7" * 64,
        },
        "public_provider_names": ["github", "supabase", "vercel"],
        "reviewed_sha": SHA,
        "schema_version": 1,
    }
    return root


def _no_spend(
    root_sha256: str,
) -> dict[str, JsonValue]:
    receipt: dict[str, JsonValue] = {
        "activation_nonce": str(ACTIVATION_NONCE),
        "approved_plan_sha256": PLAN_SHA,
        "billing_disabled": True,
        "command": "no-spend-preflight",
        "predecessor_receipt_sha256": root_sha256,
        "projection_below_70_percent": True,
        "reviewed_sha": SHA,
        "schema_version": 1,
    }
    return receipt


def _bootstrap_request(**overrides: str | int) -> DispatchRequest:
    root = _review_root()
    root_sha256 = sha256(canonical_body(root)).hexdigest()
    no_spend = _no_spend(root_sha256)
    values: dict[str, str | int] = {
        "operation": "upgrade",
        "revision": "20260727_0010",
        "attempt": 1,
        "expected_commit_sha": SHA,
        "confirm": "migrate-production",
        "activation_nonce": str(ACTIVATION_NONCE),
        "dispatch_nonce": str(DISPATCH_NONCE),
        "expected_plan_sha256": PLAN_SHA,
        "review_root_sha256": root_sha256,
        "review_root_b64": _encoded(root),
        "no_spend_receipt_sha256": sha256(canonical_body(no_spend)).hexdigest(),
        "no_spend_receipt_b64": _encoded(no_spend),
        "attempt1_failed_receipt_sha256": "",
        "attempt1_failed_receipt_b64": "",
        "attestation_run_id": "",
        "attestation_generation": 0,
        "attestation_dispatch_nonce": "",
        "attestation_sha256": "",
        "reservation_sha256": "",
    }
    values.update(overrides)
    return DispatchRequest.model_validate(values)


def _post_ledger_request(
    operation: str,
    revision: str,
    confirm: str,
    **overrides: str | int,
) -> DispatchRequest:
    values: dict[str, str | int] = {
        "operation": operation,
        "revision": revision,
        "attempt": 1,
        "expected_commit_sha": SHA,
        "confirm": confirm,
        "activation_nonce": str(ACTIVATION_NONCE),
        "dispatch_nonce": str(DISPATCH_NONCE),
        "expected_plan_sha256": PLAN_SHA,
        "review_root_sha256": "",
        "review_root_b64": "",
        "no_spend_receipt_sha256": "",
        "no_spend_receipt_b64": "",
        "attempt1_failed_receipt_sha256": "",
        "attempt1_failed_receipt_b64": "",
        "attestation_run_id": "123" if revision == "20260727_0011" else "",
        "attestation_generation": 1 if revision == "20260727_0011" else 0,
        "attestation_dispatch_nonce": (
            "33333333-3333-4333-8333-333333333333"
            if revision == "20260727_0011"
            else ""
        ),
        "attestation_sha256": ARTIFACT_SHA if revision == "20260727_0011" else "",
        "reservation_sha256": "9" * 64,
    }
    values.update(overrides)
    return DispatchRequest.model_validate(values)


def test_bootstrap_tuple_is_accepted_with_exact_quoted_revision() -> None:
    # Given: a schema-closed public-safe bootstrap tuple.
    request = _bootstrap_request()

    # When: the dispatch boundary parses it.
    result = validate_dispatch(request)

    # Then: the sole mutation argv contains one inert revision argument.
    assert result.alembic_argv == (
        "alembic",
        "-c",
        "apps/api/alembic.ini",
        "upgrade",
        "20260727_0010",
    )


@pytest.mark.parametrize(
    ("revision", "confirm", "current"),
    [
        ("20260803_0010a", "repair-release-foundation", "20260727_0010"),
        ("20260803_0010b", "rebind-release-root", "20260803_0010a"),
        ("20260803_0010c", "rebind-release-root", "20260803_0010b"),
        ("20260803_0010d", "rebind-release-root", "20260803_0010c"),
        ("20260803_0010e", "rebind-release-root", "20260803_0010d"),
        ("20260803_0010f", "rebind-release-root", "20260803_0010e"),
        ("20260803_0010g", "rebind-release-root", "20260803_0010f"),
    ],
)
def test_release_corrections_are_attempt_one_and_exactly_sequenced(
    revision: str,
    confirm: str,
    current: str,
) -> None:
    request = _bootstrap_request(revision=revision, confirm=confirm)

    result = validate_dispatch(request)
    validate_current(current, request)
    validate_result(revision, request)

    assert result.attempt == 1
    assert result.alembic_argv[-2:] == ("upgrade", revision)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "head"),
        ("revision", "head; env"),
        ("revision", "20260727_9999"),
        ("expected_commit_sha", "f" * 40),
        ("dispatch_nonce", str(ACTIVATION_NONCE)),
        ("confirm", "rollback-manifold"),
    ],
)
def test_bootstrap_rejects_untrusted_or_mismatched_values(
    field: str, value: str
) -> None:
    # Given: one malicious or mismatched dispatch field.
    request = _bootstrap_request(**{field: value})

    # When/Then: validation stops before an Alembic argv exists.
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(request, actual_event_sha=SHA)


def test_attempt_one_rejects_nonempty_retry_proof() -> None:
    # Given: attempt one with an unexpected proof body.
    request = _bootstrap_request(
        attempt1_failed_receipt_sha256=ARTIFACT_SHA,
        attempt1_failed_receipt_b64=_encoded(_review_root()),
    )

    # When/Then: the retry proof cannot be smuggled into attempt one.
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(request)


def test_attempt_two_requires_matching_failed_safe_receipt() -> None:
    # Given: a failed terminal attempt-one receipt bound to the same release root.
    attempt_one_nonce = UUID("33333333-3333-4333-8333-333333333333")
    request = _bootstrap_request(attempt=2)
    failed: dict[str, JsonValue] = {
        "accepted": False,
        "activation_nonce": str(ACTIVATION_NONCE),
        "approved_plan_sha256": PLAN_SHA,
        "artifact_sha256": ARTIFACT_SHA,
        "attempt": 1,
        "command": "migrate-0010-bootstrap",
        "dispatch_nonce": str(attempt_one_nonce),
        "enum_residue": True,
        "ledger_exists": False,
        "manifold_data_exists": False,
        "no_spend_receipt_sha256": request.no_spend_receipt_sha256,
        "retry_permitted": True,
        "review_root_sha256": request.review_root_sha256,
        "reviewed_sha": SHA,
        "run_id": 123,
        "schema_version": 1,
        "state_after": "20260726_0009",
        "state_before": "20260726_0009",
        "terminal_for_attempt": True,
    }
    failed_sha = sha256(canonical_body(failed)).hexdigest()
    request_with_proof = request.model_copy(
        update={
            "attempt1_failed_receipt_sha256": failed_sha,
            "attempt1_failed_receipt_b64": _encoded(failed),
        }
    )

    # When: the retry proof is independently parsed.
    result = validate_dispatch(request_with_proof)

    # Then: attempt two is accepted without changing its immutable target.
    assert result.attempt == 2
    assert result.revision == "20260727_0010"


def test_post_ledger_tuples_require_empty_bodies_and_exact_attestation() -> None:
    # Given: the reviewed 0011 upgrade and 0011-to-0010g downgrade tuples.
    upgrade = _post_ledger_request("upgrade", "20260727_0011", "migrate-production")
    downgrade = _post_ledger_request("downgrade", "20260803_0010g", "rollback-manifold")

    # When: both post-ledger requests are validated.
    upgrade_result = validate_dispatch(
        upgrade, claimed_reservation_sha256=upgrade.reservation_sha256
    )
    downgrade_result = validate_dispatch(
        downgrade, claimed_reservation_sha256=downgrade.reservation_sha256
    )

    # Then: each yields only its exact quoted revision and operation.
    assert upgrade_result.alembic_argv[-2:] == ("upgrade", "20260727_0011")
    assert downgrade_result.alembic_argv[-2:] == (
        "downgrade",
        "20260803_0010g",
    )


@pytest.mark.parametrize(
    "dispatch_request",
    [
        _post_ledger_request(
            "upgrade",
            "20260727_0011",
            "migrate-production",
            attestation_run_id="",
        ),
        _post_ledger_request(
            "upgrade",
            "20260727_0011",
            "migrate-production",
            attestation_sha256="f" * 63,
        ),
        _post_ledger_request(
            "downgrade",
            "20260803_0010g",
            "rollback-manifold",
            review_root_sha256="c" * 64,
        ),
    ],
)
def test_post_ledger_rejects_missing_attestation_or_bootstrap_body(
    dispatch_request: DispatchRequest,
) -> None:
    # Given: a post-ledger request with missing or forbidden evidence.
    # When/Then: validation fails before mutation argv construction.
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(dispatch_request)


def test_body_rejects_unknown_or_sensitive_fields() -> None:
    # Given: a canonical root containing a forbidden credential-shaped field.
    root = _review_root()
    root["credential_material"] = "sentinel"
    request = _bootstrap_request(
        review_root_sha256=sha256(canonical_body(root)).hexdigest(),
        review_root_b64=_encoded(root),
    )

    # When/Then: the schema-closed parser rejects the extra field.
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(request)


def test_body_rejects_noncanonical_or_oversize_bytes() -> None:
    # Given: valid JSON with noncanonical whitespace and an oversize body.
    root = _review_root()
    noncanonical = base64.b64encode(json.dumps(root).encode()).decode("ascii")
    oversized = base64.b64encode(b"{" + (b" " * 8192) + b"}").decode("ascii")

    # When/Then: both encodings stop at the boundary.
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(_bootstrap_request(review_root_b64=noncanonical))
    with pytest.raises(DispatchValidationError):
        _ = validate_dispatch(_bootstrap_request(review_root_b64=oversized))


def test_multiple_or_wrong_alembic_heads_are_rejected() -> None:
    # Given: a wrong single head and an ambiguous multi-head repository.
    # When/Then: neither may reach migration execution.
    with pytest.raises(DispatchValidationError):
        validate_heads("20260727_0010\n")
    with pytest.raises(DispatchValidationError):
        validate_heads("20260727_0011\n20260728_0012\n")


def test_database_urls_require_one_matching_direct_or_session_5432_target() -> None:
    migration = (
        "postgresql+asyncpg://user.tenant:"
        "test-credential@aws-0-region.pooler.supabase.com:5432/postgres"
    )
    native = (
        "postgresql://user.tenant:"
        "test-credential@aws-0-region.pooler.supabase.com:5432/postgres"
    )
    validate_database_urls(migration, native, native)


@pytest.mark.parametrize(
    ("migration", "dump", "restore"),
    [
        (
            _database_url("postgresql+asyncpg", "pooler.supabase.com", 6543),
            _database_url("postgresql", "pooler.supabase.com", 6543),
            _database_url("postgresql", "pooler.supabase.com", 6543),
        ),
        (
            _database_url("postgresql+asyncpg", "pooler.supabase.com", 5432),
            _database_url("postgresql", "other.supabase.com", 5432),
            _database_url("postgresql", "pooler.supabase.com", 5432),
        ),
        (
            _database_url("postgresql", "pooler.supabase.com", 5432),
            _database_url("postgresql", "pooler.supabase.com", 5432),
            _database_url("postgresql", "pooler.supabase.com", 5432),
        ),
    ],
)
def test_database_urls_reject_transaction_mismatch_or_wrong_driver(
    migration: str,
    dump: str,
    restore: str,
) -> None:
    with pytest.raises(DispatchValidationError):
        validate_database_urls(migration, dump, restore)


def test_run_selection_requires_one_exact_workflow_dispatch_identity() -> None:
    # Given: an exact run and a misleading duplicate.
    exact = RunCandidate(
        database_id=123,
        workflow_path=".github/workflows/migrate.yml",
        display_title=(f"migrate-upgrade-20260727_0010-{DISPATCH_NONCE}-attempt-1"),
        head_sha=SHA,
        event="workflow_dispatch",
        attempt=1,
        dispatch_nonce=DISPATCH_NONCE,
    )
    wrong_sha = exact.model_copy(update={"database_id": 124, "head_sha": "f" * 40})

    # When: exact correlation is applied.
    selected = select_unique_run((exact, wrong_sha), _bootstrap_request())

    # Then: only the exact immutable run is selected.
    assert selected.database_id == 123


def test_run_selection_rejects_zero_or_multiple_exact_matches() -> None:
    # Given: no candidate and two byte-identical identity candidates.
    request = _bootstrap_request()
    exact = RunCandidate(
        database_id=123,
        workflow_path=".github/workflows/migrate.yml",
        display_title=(f"migrate-upgrade-20260727_0010-{DISPATCH_NONCE}-attempt-1"),
        head_sha=SHA,
        event="workflow_dispatch",
        attempt=1,
        dispatch_nonce=DISPATCH_NONCE,
    )

    # When/Then: correlation fails closed in both cases.
    with pytest.raises(DispatchValidationError):
        _ = select_unique_run((), request)
    with pytest.raises(DispatchValidationError):
        _ = select_unique_run(
            (exact, exact.model_copy(update={"database_id": 124})),
            request,
        )
