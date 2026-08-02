from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from app.services.release.dispatch_reservation_cli import dispatch_reserve_request
from app.services.release.dispatch_reservations import DispatchReserveRequest
from app.services.release.receipts import (
    ReceiptDatabaseTimestamps,
    ReleaseChainReceipt,
    canonicalize,
    verify_canonical_receipt,
)
from app.services.release.source_activation_cli import parse_args


def _receipt() -> ReleaseChainReceipt[str]:
    fields = {
        "schema": "release-chain-receipt.v1",
        "command": "dispatch-reserve",
        "reviewed_sha": "a" * 40,
        "approved_plan_sha256": "b" * 64,
        "approval_round_id": "c" * 64,
        "approval_launch_sha256s": ("d" * 64, "e" * 64),
        "activation_nonce": UUID("11111111-1111-4111-8111-111111111111"),
        "dispatch_nonce": UUID("22222222-2222-4222-8222-222222222222"),
        "attempt": 3,
        "database_timestamps": ReceiptDatabaseTimestamps(
            created_at_db=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
            reserved_at_db=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
            selection_floor_at=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
        ),
        "accepted": True,
        "terminal_for_attempt": False,
        "retry_permitted": False,
        "predecessor_receipt_sha256": "f" * 64,
    }
    serialized = ReleaseChainReceipt[str].model_construct(
        _fields_set=set(fields),
        **fields,
        receipt_sha256="0" * 64,
    ).model_dump(mode="json", by_alias=True, exclude={"receipt_sha256"})
    digest = sha256(canonicalize(serialized)).hexdigest()
    return ReleaseChainReceipt[str].model_validate(
        {**fields, "receipt_sha256": digest}
    )


def test_common_release_receipt_is_rfc8785_canonical_and_hash_bound() -> None:
    receipt = _receipt()

    encoded = canonicalize(
        receipt.model_dump(mode="json", by_alias=True, exclude={"receipt_sha256"})
    )

    assert receipt.receipt_sha256 == sha256(encoded).hexdigest()
    assert verify_canonical_receipt(
        canonicalize(receipt.model_dump(mode="json", by_alias=True)),
        ReleaseChainReceipt[str],
    ) == receipt
    assert receipt.approval_launch_sha256s == ("d" * 64, "e" * 64)


def test_common_release_receipt_rejects_noncanonical_or_mutated_bytes() -> None:
    receipt = _receipt()
    canonical = canonicalize(receipt.model_dump(mode="json", by_alias=True))

    with pytest.raises(ValueError, match="receipt_noncanonical"):
        _ = verify_canonical_receipt(
            b" " + canonical, ReleaseChainReceipt[str]
        )
    with pytest.raises(ValueError, match="receipt_hash_mismatch"):
        _ = ReleaseChainReceipt[str].model_validate(
            {**receipt.model_dump(mode="json", by_alias=True), "accepted": False}
        )


def test_dispatch_reserve_parser_exposes_complete_generic_identity() -> None:
    args = parse_args(
        [
            "dispatch-reserve",
            "--database-url-env",
            "MIGRATION_DATABASE_URL",
            "--repository",
            "owner/repository",
            "--workflow",
            "ci.yml",
            "--display-title",
            "ci-nonce-attempt-3",
            "--head-sha",
            "a" * 40,
            "--expected-plan-sha256",
            "b" * 64,
            "--activation-nonce",
            "11111111-1111-4111-8111-111111111111",
            "--dispatch-nonce",
            "22222222-2222-4222-8222-222222222222",
            "--predecessor-receipt",
            "previous.json",
            "--attempt",
            "3",
            "--json-out",
            "reservation.json",
            "--operation-input",
            "attestation_generation=1",
            "--operation-input",
            "attestation_sha256=" + ("c" * 64),
        ]
    )

    assert args.command == "dispatch-reserve"
    assert args.workflow == "ci.yml"
    assert args.attempt == 3
    assert args.git_ref == "refs/heads/main"
    request = dispatch_reserve_request(args)
    assert request.predecessor_receipt == Path("previous.json")
    assert request.json_out == Path("reservation.json")
    assert request.operation_inputs == {
        "attestation_generation": "1",
        "attestation_sha256": "c" * 64,
    }


def test_dispatch_reserve_request_resolves_runtime_path_fields() -> None:
    request = DispatchReserveRequest.model_validate(
        {
            "repository": "owner/repository",
            "workflow": "ci.yml",
            "display_title": "ci-nonce-attempt-3",
            "head_sha": "a" * 40,
            "expected_plan_sha256": "b" * 64,
            "activation_nonce": "11111111-1111-4111-8111-111111111111",
            "dispatch_nonce": "22222222-2222-4222-8222-222222222222",
            "predecessor_receipt": "previous.json",
            "attempt": 3,
            "json_out": "reservation.json",
        }
    )

    assert request.predecessor_receipt == Path("previous.json")
    assert request.json_out == Path("reservation.json")
